#!/usr/bin/env python3
"""
Crypto Trader — 15-min and 1-hour Up/Down Markets

Bets on BTC/ETH/SOL/XRP directional markets when probability >= 90%
with sufficient time remaining. Targets 15m and 1h timeframes only.

Bet sizing: 5% of bankroll per trade, min $10, cap $50.
With $300 starting bankroll: $15/bet, max 4 concurrent positions.

Entry:  prob >= 90%, mins_remaining in [3, 13] for 15m / [10, 50] for 1h
Exit:   target 99% (profit lock), stop-loss 40%, or hold to resolution

Usage:
    python3 crypto_trader.py             # live trading
    python3 crypto_trader.py --dry-run   # simulate without placing orders
    python3 crypto_trader.py --no-confirm  # skip GO prompt (background mode)

nohup python3 crypto_trader.py --no-confirm > crypto_trader.log 2>&1 &
"""

import json
import logging
import os
import signal
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CLOB_API     = "https://clob.polymarket.com"
GAMMA_API    = "https://gamma-api.polymarket.com/events"
DATA_API     = "https://data-api.polymarket.com"
POLL_INTERVAL = 30   # seconds between cycles

# Entry parameters (from backtest on 2-day dataset)
ENTRY_THRESHOLD = 0.90
MIN_MINS = {"15m": 3,  "1h": 10}   # must have at least this many minutes left
MAX_MINS = {"15m": 13, "1h": 50}   # don't enter with more time than this (unstable early probs)

# Exit parameters
STOP_LOSS    = 0.40
TARGET_EXIT  = 0.99

# Position sizing
BET_PCT      = 0.10    # 10% of bankroll per trade
BET_MIN      = 10.0    # minimum bet $10
BET_CAP      = 50.0    # maximum bet $50
MAX_CONCURRENT = 4     # max open positions at once
MAX_PER_MARKET_PCT = 0.20  # never more than 20% of bankroll in one position

# Balance sync: re-read live USDC balance every N cycles to stay accurate
BALANCE_SYNC_EVERY = 10   # every 10 cycles = every ~5 minutes

# ── Wallet isolation ────────────────────────────────────────────────────────
# The sports bot and crypto bot share the same Polygon wallet.
# WALLET_ALLOCATION is the hard cap on how much USDC this bot will ever use.
# Set this to your intended crypto allocation (e.g. $150 of a $300 wallet).
# The sports bot operates on whatever remains above this line.
# Change before starting — does not affect already-open positions.
WALLET_ALLOCATION = 225.0   # $ max this bot will deploy

# On-chain position check — scan data API every N cycles to catch stuck/resolved positions
API_EXIT_CHECK_EVERY = 5    # every 5 cycles = every ~2.5 minutes

# Series slugs → (asset, timeframe)
TARGET_SERIES = {
    "btc-up-or-down-15m":    ("BTC", "15m"),
    "eth-up-or-down-15m":    ("ETH", "15m"),
    "sol-up-or-down-15m":    ("SOL", "15m"),
    "xrp-up-or-down-15m":    ("XRP", "15m"),
    "btc-up-or-down-hourly": ("BTC", "1h"),
    "eth-up-or-down-hourly": ("ETH", "1h"),
    "sol-up-or-down-hourly": ("SOL", "1h"),
    "xrp-up-or-down-hourly": ("XRP", "1h"),
}

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "data", "crypto_bot_state.json")
LOG_FILE   = os.path.join(BASE_DIR, "crypto_trader.log")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------
_shutdown = False
def _sig(signum, frame):
    global _shutdown
    _shutdown = True
    logger.info("Shutdown signal — finishing cycle...")
signal.signal(signal.SIGTERM, _sig)
signal.signal(signal.SIGINT,  _sig)

# ---------------------------------------------------------------------------
# CLOB setup (mirrors sports bot)
# ---------------------------------------------------------------------------
def setup_clob_client():
    from py_clob_client.client import ClobClient
    pk     = os.getenv("POLYMARKET_PK")
    funder = os.getenv("POLYMARKET_FUNDER")
    if not pk or not funder:
        logger.error("Missing POLYMARKET_PK or POLYMARKET_FUNDER in .env")
        sys.exit(1)
    client = ClobClient(CLOB_API, key=pk, chain_id=137, signature_type=1, funder=funder)
    creds  = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    logger.info(f"CLOB ready — funder: {funder[:10]}...")
    return client, funder

def get_live_balance(client):
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        bal = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=1))
        raw = int(bal.get("balance", 0))
        return raw / 1_000_000  # USDC has 6 decimals
    except Exception as e:
        logger.warning(f"  Balance fetch failed: {e}")
        return None

# ---------------------------------------------------------------------------
# Gamma API helpers
# ---------------------------------------------------------------------------
def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-trader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.warning(f"  HTTP error {url[:70]}: {e}")
        return None

def discover_events():
    """Fetch active 15m and 1h up/down events from Gamma API."""
    now     = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    max_iso = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (f"{GAMMA_API}?limit=200&closed=false"
           f"&end_date_min={now_iso}&end_date_max={max_iso}"
           f"&order=endDate&ascending=true")
    data = fetch_json(url)
    if not data:
        return []

    results = []
    for event in data:
        series = event.get("seriesSlug", "")
        if series not in TARGET_SERIES:
            continue
        asset, tf = TARGET_SERIES[series]

        slug     = event.get("slug", "")
        end_str  = event.get("endDate", "")
        is_active = event.get("active", False)
        is_closed = event.get("closed", False)
        if not is_active or is_closed:
            continue

        try:
            end_dt   = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            mins_to_exp = max(0, (end_dt - now).total_seconds() / 60)
        except (ValueError, TypeError):
            continue

        markets = event.get("markets", [])
        if not markets:
            continue
        market = markets[0]

        try:
            prices    = json.loads(market.get("outcomePrices", "[]"))
            outcomes  = json.loads(market.get("outcomes", "[]"))
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            up_prob   = float(prices[0]) if prices else 0
        except (json.JSONDecodeError, IndexError, ValueError):
            continue

        if not token_ids:
            continue

        results.append({
            "slug":         slug,
            "series":       series,
            "asset":        asset,
            "tf":           tf,
            "up_prob":      up_prob,
            "up_token_id":  token_ids[0],    # Up = index 0
            "mins_to_exp":  mins_to_exp,
            "end_str":      end_str,
            "liquidity":    market.get("liquidityNum", 0) or 0,
        })

    return results

def fetch_event_by_slug(slug):
    """Fetch a single event by slug for exit price check."""
    data = fetch_json(f"{GAMMA_API}?slug={slug}")
    if not data or not isinstance(data, list) or not data:
        return None
    event = data[0]
    markets = event.get("markets", [])
    if not markets:
        return None
    market = markets[0]
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        up_prob = float(prices[0]) if prices else 0
    except (json.JSONDecodeError, IndexError, ValueError):
        up_prob = 0

    now = datetime.now(timezone.utc)
    end_str = event.get("endDate", "")
    try:
        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        mins_to_exp = max(0, (end_dt - now).total_seconds() / 60)
    except (ValueError, TypeError):
        mins_to_exp = 0

    return {
        "up_prob":    up_prob,
        "mins_to_exp": mins_to_exp,
        "active":     event.get("active", False),
        "closed":     event.get("closed", False),
    }

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def load_state(initial_bankroll):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        logger.info(f"  Loaded state: {len(s.get('positions', {}))} open, "
                    f"${s.get('bankroll', initial_bankroll):.2f} bankroll")
        return s
    except (FileNotFoundError, json.JSONDecodeError):
        return {"bankroll": initial_bankroll, "positions": {}, "pnl": 0.0, "wagered": 0.0,
                "wins": 0, "losses": 0}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"  State save error: {e}")

# ---------------------------------------------------------------------------
# The Trader
# ---------------------------------------------------------------------------
class CryptoTrader:

    def __init__(self, dry_run=False, initial_bankroll=300.0):
        self.dry_run = dry_run
        if not dry_run:
            self.client, self.funder = setup_clob_client()
        else:
            self.client, self.funder = None, "DRY_RUN"

        self.state    = load_state(initial_bankroll)
        self.bankroll = self.state["bankroll"]
        # positions: { event_slug: {asset, tf, entry_prob, shares, cost, entry_time, up_token_id} }
        self.positions = self.state.get("positions", {})
        # Token IDs of orphan positions already handled this session — prevents re-processing
        # a stuck position every sweep cycle when _sell() returns "RESOLVED" but the data API
        # keeps showing it (Polymarket auto-redeem lag).
        self._orphan_done: set = set()

        # Sync bankroll from chain on startup (live only)
        # Cap at WALLET_ALLOCATION so sports bot and crypto bot never fight over the same funds.
        if not dry_run:
            live_bal = get_live_balance(self.client)
            if live_bal and live_bal > 0:
                capped = min(live_bal, WALLET_ALLOCATION)
                logger.info(f"  Live USDC: ${live_bal:.2f} | crypto allocation: ${capped:.2f} "
                            f"(cap: ${WALLET_ALLOCATION:.0f})")
                self.bankroll = capped
                self.state["bankroll"] = capped

        logger.info("=" * 60)
        logger.info(f"  Crypto Trader — {'DRY RUN' if dry_run else 'LIVE'}")
        logger.info(f"  Bankroll:   ${self.bankroll:.2f}")
        logger.info(f"  Bet size:   {BET_PCT*100:.0f}% = ${self._bet_size():.2f}/trade")
        logger.info(f"  Max concurrent: {MAX_CONCURRENT}  |  Stop: {STOP_LOSS:.0%}  Target: {TARGET_EXIT:.0%}")
        logger.info(f"  Entry:      >= {ENTRY_THRESHOLD:.0%}  |  15m: {MIN_MINS['15m']}-{MAX_MINS['15m']}min  "
                    f"1h: {MIN_MINS['1h']}-{MAX_MINS['1h']}min")
        logger.info("=" * 60)

    def _bet_size(self):
        return max(BET_MIN, min(BET_CAP, self.bankroll * BET_PCT))

    @property
    def total_exposure(self):
        return sum(p["cost"] for p in self.positions.values())

    def run(self):
        cycle = 0
        while not _shutdown:
            cycle += 1
            try:
                self._cycle(cycle)
            except Exception as e:
                logger.error(f"Cycle {cycle} ERROR: {e}")
                traceback.print_exc()
            for _ in range(POLL_INTERVAL):
                if _shutdown:
                    break
                time.sleep(1)
        save_state(self.state)
        logger.info("Crypto trader stopped.")

    def _sync_balance(self):
        """Re-read live USDC balance from chain. Cap at WALLET_ALLOCATION."""
        if self.dry_run:
            return
        live = get_live_balance(self.client)
        if live is None:
            return  # API error — keep internal value
        capped   = min(live, WALLET_ALLOCATION)
        internal = self.bankroll
        drift    = abs(capped - internal)
        if drift > 1.0:
            logger.info(f"  💰 Balance sync: on-chain ${live:.2f}  allocated ${capped:.2f}  "
                        f"internal ${internal:.2f}  drift ${drift:.2f} — correcting")
        else:
            logger.info(f"  💰 Balance: ${capped:.2f} ✓  (wallet ${live:.2f})")
        self.bankroll = capped
        self.state["bankroll"] = capped

    def _cycle(self, cycle_num):
        now    = datetime.now(timezone.utc)

        # Sync live USDC balance every N cycles (authoritative source of truth)
        if cycle_num % BALANCE_SYNC_EVERY == 0:
            self._sync_balance()

        events = discover_events()

        # Build lookup: slug → event info
        live = {e["slug"]: e for e in events}

        # 1. Check exits for all open positions (Gamma-based)
        exits = self._check_exits(live, now)

        # 1b. On-chain position sweep — catches stuck/resolved positions the CLOB missed
        if cycle_num % API_EXIT_CHECK_EVERY == 0:
            exits += self._check_exits_from_api()

        # 2. Find and enter new opportunities
        entries = self._find_and_enter(events, now)

        # 3. Persist state
        self.state["bankroll"]  = self.bankroll
        self.state["positions"] = self.positions
        save_state(self.state)

        logger.info(f"Cycle {cycle_num}: {len(events)} live  |  "
                    f"{len(self.positions)} open  |  "
                    f"+{entries} entries  -{exits} exits  |  "
                    f"Bank: ${self.bankroll:.2f}  "
                    f"PnL: ${self.state.get('pnl', 0):+.2f}")

    # -----------------------------------------------------------------------
    # Exit logic
    # -----------------------------------------------------------------------
    def _check_exits(self, live_events, now):
        exits = 0
        for slug in list(self.positions.keys()):
            pos = self.positions[slug]

            # Get current market state — use live discovery first, fallback to slug fetch
            if slug in live_events:
                ev = live_events[slug]
                cur_prob  = ev["up_prob"]
                mins_left = ev["mins_to_exp"]
                is_closed = False
            else:
                # Market may have expired — fetch directly
                ev = fetch_event_by_slug(slug)
                if ev is None:
                    logger.warning(f"  ⚠️  Can't find {slug[:40]} — skipping exit check")
                    continue
                cur_prob  = ev["up_prob"]
                mins_left = ev["mins_to_exp"]
                is_closed = ev["closed"]

            action = None
            exit_price = cur_prob

            # Target hit
            if cur_prob >= TARGET_EXIT:
                action     = "TARGET"
                exit_price = min(cur_prob, 0.99)

            # Stop loss
            elif cur_prob <= STOP_LOSS:
                action     = "STOP"
                exit_price = max(cur_prob, 0.01)

            # Market resolved — if we lost, write off immediately (no liquidity for worthless tokens)
            elif is_closed and cur_prob < 0.5:
                cost = pos["cost"]
                self.state["pnl"]    = self.state.get("pnl",    0) - cost
                self.state["losses"] = self.state.get("losses", 0) + 1
                logger.info(f"  💀 WRITE-OFF [closed/lost] {pos['asset']} {pos['tf']} "
                            f"| cost=${cost:.2f} PnL: ${-cost:+.2f}")
                del self.positions[slug]
                exits += 1
                continue

            # Market resolved as win — sell at 99c
            elif is_closed and cur_prob >= 0.5:
                action     = "RESOLVED"
                exit_price = min(cur_prob, 0.99)

            # Expiring in <1 min — exit to avoid limbo
            elif mins_left < 1.0 and mins_left >= 0:
                action     = "EXPIRING"
                exit_price = min(cur_prob, 0.99)

            if action:
                won = (action in ("TARGET", "RESOLVED", "EXPIRING") and exit_price > 0.5)
                success = self._sell(pos, exit_price)

                # If FOK sell at 0.99 fails on a win, retry at 0.98 (stuck-at-99c pattern)
                if not success and won and not self.dry_run:
                    logger.warning(f"  ⏳ Sell failed at {exit_price:.2f}, retrying at {exit_price-0.01:.2f}")
                    success = self._sell(pos, exit_price - 0.01)

                if success == "RESOLVED":
                    # Auto-redeemed on-chain — USDC already credited, don't double-count
                    cost = pos["cost"]
                    pnl  = pos["shares"] - cost if won else -cost
                    icon = "🏁" if won else "💀"
                    self.state["pnl"]    = self.state.get("pnl", 0) + pnl
                    self.state["wins"]   = self.state.get("wins",  0) + (1 if won else 0)
                    self.state["losses"] = self.state.get("losses",0) + (0 if won else 1)
                    logger.info(f"  {icon} AUTO-RESOLVED {pos['asset']} {pos['tf']} "
                                f"| cost=${cost:.2f} PnL: ${pnl:+.2f}")
                    del self.positions[slug]
                    exits += 1
                elif success or self.dry_run:
                    revenue = pos["shares"] * exit_price
                    cost    = pos["cost"]
                    pnl     = revenue - cost
                    won     = pnl > 0
                    icon    = "✅" if won else "❌"
                    self.bankroll += revenue
                    self.state["pnl"]    = self.state.get("pnl", 0) + pnl
                    self.state["wins"]   = self.state.get("wins",  0) + (1 if won else 0)
                    self.state["losses"] = self.state.get("losses",0) + (0 if won else 1)
                    logger.info(f"  {icon} EXIT [{action}] {pos['asset']} {pos['tf']} "
                                f"| entry={pos['entry_prob']:.1%} → exit={exit_price:.1%} "
                                f"| ${cost:.2f} → ${revenue:.2f} (PnL: ${pnl:+.2f}) "
                                f"| Bank: ${self.bankroll:.2f}")
                    del self.positions[slug]
                    exits += 1
                else:
                    logger.warning(f"  ⏳ Sell failed for {pos['asset']} {pos['tf']} — retry next cycle")
        return exits

    def _check_exits_from_api(self):
        """
        Query actual on-chain positions via data API.
        Catches positions stuck at 99c that the CLOB-based check missed,
        and auto-resolved positions where Polymarket already credited USDC.
        Mirrors sports bot's check_exits_from_api().
        """
        if self.dry_run:
            return 0
        try:
            import requests
            resp = requests.get(
                f"https://data-api.polymarket.com/positions?user={self.funder}",
                timeout=10)
            if resp.status_code != 200:
                return 0
            positions = resp.json()
        except Exception as e:
            logger.warning(f"  API position check failed: {e}")
            return 0

        exits = 0
        # Build lookup: token_id → slug for positions we're actively tracking
        our_tokens = {p["up_token_id"]: slug
                      for slug, p in self.positions.items()}

        for p in positions:
            token_id  = p.get("asset", "")
            size      = float(p.get("size", 0))
            cur_price = float(p.get("curPrice", 0))
            avg_price = float(p.get("avgPrice", 0))

            if size < 0.1 or cur_price <= 0:
                continue

            # Is this a position we're tracking, or an orphan (entered outside this session)?
            is_tracked = token_id in our_tokens
            slug = our_tokens.get(token_id)
            pos  = self.positions.get(slug) if slug else None

            # For orphan positions: only act at ≥99c (don't stop-loss things we didn't enter)
            if not is_tracked and cur_price < 0.99:
                continue

            # Skip orphans already handled this session — prevents infinite re-processing loop
            # when _sell() returns RESOLVED but the data API still shows the position
            # (Polymarket's auto-redeem lag). USDC was already credited automatically.
            if not is_tracked and token_id in self._orphan_done:
                continue

            # Build a minimal pos dict for orphans so _sell() has what it needs
            if pos is None:
                pos = {
                    "asset":       "ORPHAN",
                    "tf":          "?",
                    "up_token_id": token_id,
                    "shares":      size,
                    "cost":        size * avg_price,
                }

            action = None
            if cur_price >= 0.99:
                action = "TARGET"
            elif is_tracked and cur_price < 0.05:
                # Tracked position effectively worthless (<5¢) — write off rather than
                # trying to sell into an illiquid market (FOK/GTC will fail with no buyers)
                logger.info(f"  💀 WRITE-OFF (API) {pos['asset']} {pos['tf']} "
                            f"| {size:.1f} shr @ {cur_price:.3f} — writing off")
                self.state["pnl"]    = self.state.get("pnl",    0) - pos["cost"]
                self.state["losses"] = self.state.get("losses", 0) + 1
                del self.positions[slug]
                exits += 1
                continue
            elif is_tracked and cur_price <= STOP_LOSS:
                action = "STOP"

            if not action:
                continue

            sell_price = min(cur_price, 0.99) if action == "TARGET" else cur_price
            label = f"{pos['asset']} {pos['tf']}" + ("" if is_tracked else " [orphan]")
            logger.info(f"  🔄 API-EXIT ({action}) {label} "
                        f"| {size:.1f} shr @ {sell_price:.3f} | avg: {avg_price:.3f}")

            success = self._sell(pos, sell_price)
            if not success and action == "TARGET":
                success = self._sell(pos, sell_price - 0.01)  # retry at 0.98

            if success and success != "RESOLVED":
                revenue = size * sell_price
                cost    = size * avg_price
                pnl     = revenue - cost
                self.bankroll += revenue
                self.state["pnl"]  = self.state.get("pnl", 0) + pnl
                won = action == "TARGET"
                self.state["wins"]   = self.state.get("wins",   0) + (1 if won else 0)
                self.state["losses"] = self.state.get("losses", 0) + (0 if won else 1)
                icon = "✅" if won else "🛑"
                logger.info(f"  {icon} {'WIN' if won else 'STOP'} (API) {label} "
                            f"| ${cost:.2f} → ${revenue:.2f} (PnL: ${pnl:+.2f}) "
                            f"| Bank: ${self.bankroll:.2f}")
                if is_tracked:
                    del self.positions[slug]
                else:
                    self._orphan_done.add(token_id)
                exits += 1
            elif success == "RESOLVED":
                if is_tracked:
                    # Tracked position — calculate PnL (USDC already credited by Polymarket)
                    cost = pos["cost"]
                    pnl  = pos["shares"] - cost if action == "TARGET" else -cost
                    self.state["pnl"]    = self.state.get("pnl",    0) + pnl
                    self.state["wins"]   = self.state.get("wins",    0) + (1 if action == "TARGET" else 0)
                    self.state["losses"] = self.state.get("losses",  0) + (0 if action == "TARGET" else 1)
                    logger.info(f"  🏁 RESOLVED (API) {label} PnL: ${pnl:+.2f}")
                    del self.positions[slug]
                else:
                    # Orphan — USDC already auto-credited by Polymarket, don't count PnL here
                    logger.info(f"  🏁 RESOLVED (API) {label} — already redeemed on-chain, skipping")
                    self._orphan_done.add(token_id)
                exits += 1

        return exits

    # -----------------------------------------------------------------------
    # Entry logic
    # -----------------------------------------------------------------------
    def _find_and_enter(self, events, now):
        entries = 0
        # Sort by probability descending — enter highest-confidence first
        candidates = sorted(events, key=lambda e: e["up_prob"], reverse=True)

        for ev in candidates:
            slug = ev["slug"]
            tf   = ev["tf"]
            prob = ev["up_prob"]
            mins = ev["mins_to_exp"]

            # Already in a position for this event
            if slug in self.positions:
                continue

            # Concurrent position cap
            if len(self.positions) >= MAX_CONCURRENT:
                break

            # Entry threshold
            if prob < ENTRY_THRESHOLD:
                continue

            # Time window check
            if mins < MIN_MINS.get(tf, 3) or mins > MAX_MINS.get(tf, 50):
                continue

            # Bankroll checks
            bet = self._bet_size()
            if self.bankroll < bet:
                logger.warning(f"  ⛔ Bankroll ${self.bankroll:.2f} < bet ${bet:.2f}")
                break

            per_market_cap = (self.bankroll + self.total_exposure) * MAX_PER_MARKET_PCT
            if bet > per_market_cap:
                bet = per_market_cap

            if bet < BET_MIN:
                continue

            # Execute
            shares = bet / prob  # shares to buy
            shares = max(5.0, float(int(shares)))  # Polymarket min 5 shares

            success = self._buy(ev["up_token_id"], prob, shares, ev)
            if success or self.dry_run:
                actual_cost = shares * prob
                self.bankroll -= actual_cost
                self.state["wagered"] = self.state.get("wagered", 0) + actual_cost
                self.positions[slug] = {
                    "asset":       ev["asset"],
                    "tf":          tf,
                    "series":      ev["series"],
                    "up_token_id": ev["up_token_id"],
                    "entry_prob":  prob,
                    "shares":      shares,
                    "cost":        actual_cost,
                    "entry_time":  now.isoformat(),
                    "end_str":     ev["end_str"],
                }
                logger.info(f"  🟢 ENTER {ev['asset']} {tf} "
                            f"| prob={prob:.1%} mins={mins:.1f} "
                            f"| {shares:.0f} shr @ {prob:.3f} = ${actual_cost:.2f} "
                            f"| Bank: ${self.bankroll:.2f}")
                entries += 1

        return entries

    # -----------------------------------------------------------------------
    # Order helpers (mirrored from sports trader)
    # -----------------------------------------------------------------------
    def _buy(self, token_id, price, size, info):
        if self.dry_run:
            logger.info(f"  [DRY] BUY {size:.0f} shr {info['asset']} {info['tf']} @ {price:.3f}")
            return True
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            # Bid at 0.99 to cross any ask — CLOB fills at best available
            order_args = OrderArgs(price=0.99, size=size, side="BUY", token_id=token_id)
            signed     = self.client.create_order(order_args)
            logger.info(f"  💲 Market buy {size:.0f} shr @ 0.99 (imp={price:.3f})")
            resp = self.client.post_order(signed, OrderType.FOK)
            logger.info(f"  📋 Response: {resp}")
            if resp and resp.get("success"):
                order_id = resp.get("orderID", "")
                time.sleep(4)
                try:
                    check   = self.client.get_order(order_id)
                    status  = check.get("status", "").upper() if check else ""
                    matched = float(check.get("size_matched", 0) or 0) if check else 0
                    logger.info(f"  🔄 Verify: {status} matched={matched:.0f}/{size:.0f}")
                    if status == "MATCHED" or matched >= size * 0.9:
                        return True
                    elif status == "LIVE" and matched == 0:
                        try: self.client.cancel(order_id)
                        except: pass
                        return False
                    elif matched > 0:
                        return True  # partial fill — good enough
                    else:
                        try: self.client.cancel(order_id)
                        except: pass
                        return False
                except Exception as e:
                    logger.warning(f"  ⚠️ Verify error: {e}")
                    return False
            # FOK failed — try GTC
            logger.warning("  ⚠️ FOK rejected, trying GTC...")
            resp2 = self.client.post_order(signed, OrderType.GTC)
            if resp2 and resp2.get("success"):
                logger.info("  ✅ GTC accepted")
                return True
            logger.error(f"  ❌ Both FOK+GTC failed: {resp2}")
            return False
        except Exception as e:
            logger.error(f"  ❌ Buy error: {e}")
            return False

    def _sell(self, pos, price):
        if self.dry_run:
            logger.info(f"  [DRY] SELL {pos['shares']:.0f} shr {pos['asset']} {pos['tf']} @ {price:.3f}")
            return True
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            args   = OrderArgs(price=price, size=pos["shares"], side="SELL",
                               token_id=pos["up_token_id"])
            signed = self.client.create_order(args)

            # Try FOK — but catch its exception separately so GTC fallback still runs
            # if FOK throws (e.g. no liquidity at the price level).
            fok_ok = False
            try:
                resp = self.client.post_order(signed, OrderType.FOK)
                if resp and resp.get("success"):
                    return True
                err_fok = str(resp or "").lower()
                if "does not exist" in err_fok or "not enough" in err_fok:
                    logger.info("  ℹ️  Market auto-resolved on-chain — tokens already redeemed")
                    return "RESOLVED"
            except Exception as fok_err:
                err_fok = str(fok_err).lower()
                if "does not exist" in err_fok or "not enough" in err_fok:
                    logger.info("  ℹ️  Market auto-resolved on-chain — tokens already redeemed")
                    return "RESOLVED"
                logger.warning(f"  ⚠️ FOK threw: {fok_err} — falling through to GTC")

            # Fallback GTC
            try:
                resp2 = self.client.post_order(signed, OrderType.GTC)
                if resp2 and resp2.get("success"):
                    logger.info("  ✅ GTC sell accepted")
                    return True
                err_gtc = str(resp2 or "").lower()
                if "does not exist" in err_gtc or "not enough" in err_gtc:
                    logger.info("  ℹ️  Market auto-resolved on-chain — tokens already redeemed")
                    return "RESOLVED"
                logger.error(f"  ❌ Both FOK+GTC failed: {resp2}")
                return False
            except Exception as gtc_err:
                err_gtc = str(gtc_err).lower()
                if "does not exist" in err_gtc or "not enough" in err_gtc:
                    logger.info("  ℹ️  Market auto-resolved on-chain — tokens already redeemed")
                    return "RESOLVED"
                logger.error(f"  ❌ GTC also threw: {gtc_err}")
                return False

        except Exception as e:
            err = str(e).lower()
            if "does not exist" in err or "not enough" in err:
                logger.info("  ℹ️  Market auto-resolved on-chain — tokens already redeemed")
                return "RESOLVED"
            logger.error(f"  ❌ Sell error: {e}")
            return False

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    dry_run    = "--dry-run" in sys.argv
    no_confirm = "--no-confirm" in sys.argv

    mode = "DRY RUN" if dry_run else "⚠️  LIVE TRADING"
    print(f"\n{'='*60}")
    print(f"  Crypto Trader — {mode}")
    print(f"  Targets: 15m + 1h Up/Down | BTC ETH SOL XRP")
    print(f"  Entry: >= {ENTRY_THRESHOLD:.0%} | Stop: {STOP_LOSS:.0%} | Target: {TARGET_EXIT:.0%}")
    print(f"  Bet sizing: {BET_PCT*100:.0f}% of bankroll, ${BET_MIN:.0f}-${BET_CAP:.0f}/trade")
    print(f"  State: {STATE_FILE}")
    print(f"{'='*60}\n")

    if not dry_run and not no_confirm:
        resp = input("Type GO to start live trading: ").strip().upper()
        if resp != "GO":
            print("Aborted.")
            return

    trader = CryptoTrader(dry_run=dry_run)
    trader.run()

if __name__ == "__main__":
    main()
