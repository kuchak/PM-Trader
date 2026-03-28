#!/usr/bin/env python3
"""
Polymarket Sports Monitor v2 — Comprehensive Data Collection Pipeline

Discovers in-progress game-bet events via three Gamma API queries:
  1. live=true — esports with real-time score/period data
  2. event_date=today (US Eastern) — today's matches
  3. event_date=yesterday (US Eastern) — catches late-night US matches
     that span the UTC midnight boundary

Then client-side filters: live=true OR startTime <= now. This catches
tennis, soccer, table tennis, etc. that never get the live=true flag.

For every outcome on every in-progress market, logs a snapshot every 30 seconds
to market_snapshots.csv.  CLOB buy+sell prices are fetched for outcomes with
implied_prob >= 0.40 (capped at 300 calls/cycle, prioritised by probability).

When a market disappears for 3 consecutive cycles, queries Gamma for resolution
status and logs to resolutions.csv with full historical context.

Usage:
    python3 polymarket_monitor.py              # foreground
    nohup python3 polymarket_monitor.py &      # background
"""

import csv
import json
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GAMMA_EVENTS_API = "https://gamma-api.polymarket.com/events"
GAMMA_MARKET_API = "https://gamma-api.polymarket.com/markets"
CLOB_PRICE_API = "https://clob.polymarket.com/price"
WS_HOST = "sports-api.polymarket.com"
WS_PATH = "/ws"
GAME_BETS_TAG_ID = 100639
PAGE_SIZE = 200
CYCLE_INTERVAL = 30  # seconds between scans
CLOB_MIN_IMPLIED = 0.40  # fetch CLOB for outcomes >= this
CLOB_MAX_IMPLIED = 0.95  # skip outcomes above this (already decided)
CLOB_MAX_PER_CYCLE = 300  # max CLOB API calls per cycle
MISSING_CYCLES_TO_RESOLVE = 3  # consecutive absent cycles before resolution
HISTORY_MAX_ENTRIES = 60  # ~30 min of history at 30s intervals
GIT_PUSH_EVERY_N_CYCLES = 50  # auto-push data to GitHub (~25 min)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOTS_CSV = os.path.join(DATA_DIR, "market_snapshots.csv")
RESOLUTIONS_CSV = os.path.join(DATA_DIR, "resolutions.csv")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat.txt")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.log")

SNAPSHOT_FIELDS = [
    "timestamp", "event_name", "league", "game_id", "market_type",
    "outcome_name", "implied_prob", "clob_buy_price", "clob_sell_price",
    "spread", "best_bid", "best_ask", "volume", "liquidity",
    "game_score", "game_period", "game_elapsed",
    "token_id", "market_id", "event_id",
]

RESOLUTION_FIELDS = [
    "resolved_timestamp", "event_name", "league", "game_id", "market_type",
    "outcome_name", "won", "max_implied_prob", "max_clob_buy_price",
    "first_seen_timestamp", "last_seen_timestamp", "minutes_tracked",
]

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log("Shutdown signal received, finishing current cycle...")


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _api_get(url, timeout=30):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "polymarket-monitor/2.0")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _parse_json_field(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(raw, list):
        return raw
    return []


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _is_market_resolved(mkt):
    """Check if a market is already resolved (closed or prices snapped)."""
    if mkt.get("closed"):
        return True
    prices = _parse_json_field(mkt.get("outcomePrices"))
    if prices:
        try:
            vals = [float(p) for p in prices]
            if all(v <= 0.001 or v >= 0.999 for v in vals):
                return True
        except (ValueError, TypeError):
            pass
    return False


def _is_event_finished(event):
    """Check if all markets in an event are resolved → match is over."""
    markets = event.get("markets", [])
    if not markets:
        return True
    return all(_is_market_resolved(m) for m in markets)


# ---------------------------------------------------------------------------
# Heartbeat & auto-push
# ---------------------------------------------------------------------------


def write_heartbeat(cycle):
    """Append a heartbeat timestamp before each cycle."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with open(HEARTBEAT_FILE, "a") as f:
        f.write(f"cycle={cycle}  {now}\n")


def auto_git_push(cycle):
    """Push data files to GitHub every N cycles."""
    if cycle % GIT_PUSH_EVERY_N_CYCLES != 0:
        return
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        subprocess.run(
            ["git", "add", "polymarket-monitor/data/"],
            cwd=repo_root, capture_output=True, timeout=30,
        )
        result = subprocess.run(
            ["git", "commit", "-m", f"Auto-push data snapshot (cycle {cycle})"],
            cwd=repo_root, capture_output=True, timeout=30,
        )
        if result.returncode == 0:
            subprocess.run(
                ["git", "push"],
                cwd=repo_root, capture_output=True, timeout=60,
            )
            log(f"  Git auto-push: committed and pushed at cycle {cycle}")
        else:
            log(f"  Git auto-push: nothing to commit at cycle {cycle}")
    except Exception as e:
        log(f"  Git auto-push failed: {e}")


# ---------------------------------------------------------------------------
# Market type classification
# ---------------------------------------------------------------------------


def parse_market_type(question):
    """Classify market type from the question string."""
    q = question.lower()
    if re.search(r"spread|handicap|[+-]\d+\.5\s*(maps?|rounds?|games?|points?)", q):
        return "spread"
    if re.search(r"\bover\b|\bunder\b|\btotal\b", q):
        return "over_under"
    return "moneyline"


# ---------------------------------------------------------------------------
# Game state parsing from Gamma event data
# ---------------------------------------------------------------------------


def parse_game_score(event):
    """Extract score from event. Format: '000-000|2-2|Bo5' → '2-2'."""
    raw = event.get("score", "")
    if not raw:
        return ""
    parts = raw.split("|")
    if len(parts) >= 2:
        return parts[1]
    return raw


def parse_game_period(event):
    """Extract period string, e.g. '5/5' or 'Q2'."""
    return event.get("period", "")


def parse_game_elapsed(event):
    """Estimate elapsed minutes from startTime."""
    st = event.get("startTime")
    if not st:
        return ""
    start = _parse_iso(st)
    if not start:
        return ""
    elapsed = datetime.now(timezone.utc) - start
    mins = int(elapsed.total_seconds() / 60)
    return f"{mins}m"


# ---------------------------------------------------------------------------
# WebSocket probe (one-shot at startup)
# ---------------------------------------------------------------------------


def try_websocket_connection():
    """Try connecting to Sports WebSocket. Returns True on success."""
    try:
        ctx = ssl.create_default_context()
        sock = socket.create_connection((WS_HOST, 443), timeout=5)
        ssock = ctx.wrap_socket(sock, server_hostname=WS_HOST)
        import base64
        key = base64.b64encode(os.urandom(16)).decode()
        upgrade = (
            f"GET {WS_PATH} HTTP/1.1\r\n"
            f"Host: {WS_HOST}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Origin: https://polymarket.com\r\n"
            f"\r\n"
        )
        ssock.send(upgrade.encode())
        resp = ssock.recv(4096).decode()
        ssock.close()
        status_line = resp.split("\r\n")[0]
        if "101" in status_line:
            return True
        log(f"  WebSocket rejected: {status_line}")
        return False
    except Exception as e:
        log(f"  WebSocket connection failed: {e}")
        return False


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


def _get_et_dates():
    """Return (today_ET, yesterday_ET) as 'YYYY-MM-DD' strings.

    Polymarket uses US Eastern time for eventDate. We compute the current
    ET date and also yesterday's ET date to catch matches that span the
    midnight boundary.
    """
    now_utc = datetime.now(timezone.utc)
    # US Eastern = UTC-5 (EST) or UTC-4 (EDT).
    # Approximate: March second Sunday to November first Sunday is EDT.
    year = now_utc.year
    # Second Sunday of March
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    dst_start = dst_start.replace(hour=7)  # 2 AM ET = 7 AM UTC
    # First Sunday of November
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = dst_end.replace(hour=6)  # 2 AM ET = 6 AM UTC
    if dst_start <= now_utc < dst_end:
        et_offset = timedelta(hours=-4)
    else:
        et_offset = timedelta(hours=-5)
    now_et = now_utc + et_offset
    today_et = now_et.strftime("%Y-%m-%d")
    yesterday_et = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")
    return today_et, yesterday_et


def _fetch_events_page(extra_params, label=""):
    """Fetch paginated events with given extra query params."""
    all_events = []
    offset = 0
    while True:
        url = (
            f"{GAMMA_EVENTS_API}"
            f"?tag_id={GAME_BETS_TAG_ID}"
            f"&active=true&closed=false"
            f"{extra_params}"
            f"&limit={PAGE_SIZE}&offset={offset}"
        )
        try:
            page = _api_get(url)
        except Exception as e:
            log(f"  WARN: {label} fetch failed at offset={offset}: {e}")
            break
        if not page:
            break
        all_events.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_events


def fetch_all_live_events():
    """Fetch in-progress game-bet events via three targeted queries:

      1. live=true — esports with score data
      2. event_date=today (ET) — today's matches
      3. event_date=yesterday (ET) — catches late-night US matches

    Then client-side filter: include if live=true OR startTime <= now.
    Deduplicates by event ID (live=true takes priority).
    """
    now = datetime.now(timezone.utc)
    today_et, yesterday_et = _get_et_dates()

    live_events = _fetch_events_page("&live=true", "live")
    today_events = _fetch_events_page(f"&event_date={today_et}", f"date={today_et}")
    yesterday_events = _fetch_events_page(f"&event_date={yesterday_et}", f"date={yesterday_et}")

    # Deduplicate: live=true events take priority (have score/period data)
    merged = {}
    for ev in live_events:
        merged[ev["id"]] = ev
    for ev in today_events + yesterday_events:
        if ev["id"] not in merged:
            merged[ev["id"]] = ev

    # Client-side filter: live=true OR (startTime in past AND not finished)
    live_flag = []
    started = []
    for ev in merged.values():
        if ev.get("live"):
            live_flag.append(ev)
            continue
        st = ev.get("startTime")
        if st:
            start = _parse_iso(st)
            if start and start <= now and not _is_event_finished(ev):
                started.append(ev)

    events = live_flag + started
    return events, len(live_flag), len(started), len(merged)


def fetch_clob_price(token_id, side="BUY"):
    """Fetch CLOB price for a token. Returns float or None."""
    url = f"{CLOB_PRICE_API}?token_id={token_id}&side={side}"
    try:
        data = _api_get(url, timeout=10)
        return _safe_float(data.get("price"))
    except Exception:
        return None



# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------


def _ensure_csv(path, fields):
    if not os.path.exists(path):
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(fields)


def append_snapshots(rows):
    _ensure_csv(SNAPSHOTS_CSV, SNAPSHOT_FIELDS)
    with open(SNAPSHOTS_CSV, "a", newline="") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)


def append_resolutions(rows):
    _ensure_csv(RESOLUTIONS_CSV, RESOLUTION_FIELDS)
    with open(RESOLUTIONS_CSV, "a", newline="") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)



# ---------------------------------------------------------------------------
# Core cycle
# ---------------------------------------------------------------------------


def run_cycle(state):
    """Run one scan cycle. Returns updated state dict."""
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()

    # ── 1. Fetch all in-progress events ──────────────────────────────────
    events, n_live, n_started, n_total = fetch_all_live_events()

    # ── 2. Extract outcomes ─────────────────────────────────────────────
    # Log snapshots for ALL outcomes between 0.01 and 0.99.
    # Only skip true 0/1 (< 0.01 or > 0.99).
    outcome_list = []  # (implied_float, outcome_dict)

    for ev in events:
        event_id = str(ev.get("id", ""))
        event_name = ev.get("title", "")
        league = ev.get("seriesSlug", "")
        game_id = str(ev.get("gameId", ""))
        game_score = parse_game_score(ev)
        game_period = parse_game_period(ev)
        game_elapsed = parse_game_elapsed(ev)

        for mkt in ev.get("markets", []):
            if mkt.get("closed"):
                continue

            market_id = str(mkt.get("id", ""))
            question = mkt.get("question", "")
            market_type = parse_market_type(question)
            best_bid = mkt.get("bestBid", "")
            best_ask = mkt.get("bestAsk", "")
            volume = mkt.get("volume", "")
            liquidity = mkt.get("liquidity", "")

            outcomes = _parse_json_field(mkt.get("outcomes"))
            outcome_prices = _parse_json_field(mkt.get("outcomePrices"))
            clob_ids = _parse_json_field(mkt.get("clobTokenIds"))

            for i, outcome_name in enumerate(outcomes):
                implied_str = outcome_prices[i] if i < len(outcome_prices) else ""
                token_id = clob_ids[i] if i < len(clob_ids) else ""
                try:
                    implied = float(implied_str)
                except (ValueError, TypeError):
                    implied = 0.0

                # Skip true 0/1 prices only
                if implied < 0.01 or implied > 0.99:
                    continue

                outcome_list.append((implied, {
                    "event_id": event_id,
                    "event_name": event_name,
                    "league": league,
                    "game_id": game_id,
                    "market_id": market_id,
                    "market_type": market_type,
                    "question": question,
                    "outcome_name": outcome_name,
                    "implied_prob": implied_str,
                    "implied_float": implied,
                    "token_id": token_id,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "volume": volume,
                    "liquidity": liquidity,
                    "game_score": game_score,
                    "game_period": game_period,
                    "game_elapsed": game_elapsed,
                }))

    # ── 3. Fetch CLOB prices (buy + sell) ────────────────────────────────
    # CLOB only for competitive outcomes: 0.40 <= implied <= 0.95.
    # Skip illiquid books (bid<0.05 & ask>0.95).
    def _clob_eligible(imp, d):
        if not d["token_id"]:
            return False
        if imp < CLOB_MIN_IMPLIED or imp > CLOB_MAX_IMPLIED:
            return False
        bid = _safe_float(d["best_bid"])
        ask = _safe_float(d["best_ask"])
        if bid is not None and ask is not None and bid < 0.05 and ask > 0.95:
            return False
        return True

    clob_candidates = sorted(
        [(imp, d) for imp, d in outcome_list if _clob_eligible(imp, d)],
        key=lambda x: -x[0],
    )

    clob_budget = CLOB_MAX_PER_CYCLE
    clob_results = {}  # token_id → (buy_price, sell_price)
    clob_calls = 0

    for _, d in clob_candidates:
        if clob_budget < 2:
            break
        tid = d["token_id"]
        if tid in clob_results:
            continue
        buy = fetch_clob_price(tid, "BUY")
        sell = fetch_clob_price(tid, "SELL")
        clob_results[tid] = (buy, sell)
        clob_calls += 2
        clob_budget -= 2

    # ── 4. Build snapshot rows & update state ────────────────────────────
    snapshot_rows = []
    current_keys = set()  # outcomes present this cycle

    for _, d in outcome_list:
        tid = d["token_id"]
        buy_price, sell_price = clob_results.get(tid, (None, None))

        buy_str = f"{buy_price}" if buy_price is not None else ""
        sell_str = f"{sell_price}" if sell_price is not None else ""
        spread_str = ""
        if buy_price is not None and sell_price is not None:
            spread_str = f"{buy_price - sell_price:.6f}"

        snapshot_rows.append([
            now_str, d["event_name"], d["league"], d["game_id"],
            d["market_type"], d["outcome_name"], d["implied_prob"],
            buy_str, sell_str, spread_str, d["best_bid"], d["best_ask"],
            d["volume"], d["liquidity"], d["game_score"], d["game_period"],
            d["game_elapsed"], d["token_id"], d["market_id"], d["event_id"],
        ])

        key = f"{d['market_id']}:{d['outcome_name']}"
        current_keys.add(key)

        # Initialise state for new outcomes
        if key not in state:
            state[key] = {
                "first_seen": now_str,
                "max_implied": d["implied_float"],
                "max_clob_buy": buy_price if buy_price else 0,
                "event_name": d["event_name"],
                "league": d["league"],
                "game_id": d["game_id"],
                "market_type": d["market_type"],
                "market_id": d["market_id"],
                "outcome_name": d["outcome_name"],
            }

        s = state[key]
        s["last_seen"] = now_str
        s.pop("missing_cycles", None)

        # Track maximums
        if d["implied_float"] > (s.get("max_implied") or 0):
            s["max_implied"] = d["implied_float"]
        if buy_price and buy_price > (s.get("max_clob_buy") or 0):
            s["max_clob_buy"] = buy_price

    # ── 5. Write snapshots ───────────────────────────────────────────────
    append_snapshots(snapshot_rows)
    log(f"  {len(events)} events ({n_live} live-flag, {n_started} started) "
        f"from {n_total} total | {len(snapshot_rows)} outcomes | {clob_calls} CLOB calls")

    # ── 6. Detect resolutions ────────────────────────────────────────────
    # An outcome is "resolved" when:
    #   (a) max_implied >= 0.99 at any point during tracking, AND
    #   (b) it has been absent from the live feed for 3 consecutive cycles.
    # "won" = max_implied >= 0.99 (always true for resolved outcomes).
    # Outcomes that disappear without ever hitting 0.99 are silently dropped.
    resolution_rows = []
    resolved_keys = []
    dropped_keys = []
    newly_missing = 0

    for key, info in list(state.items()):
        if key in current_keys:
            continue

        missing = info.get("missing_cycles", 0) + 1
        info["missing_cycles"] = missing

        if missing < MISSING_CYCLES_TO_RESOLVE:
            newly_missing += 1
            continue

        max_imp = info.get("max_implied", 0)
        if max_imp >= 0.99:
            # This outcome hit 0.99+ → it won. Log resolution.
            first_seen = _parse_iso(info.get("first_seen"))
            last_seen = _parse_iso(info.get("last_seen"))
            minutes = 0
            if first_seen and last_seen:
                minutes = round((last_seen - first_seen).total_seconds() / 60, 1)

            resolution_rows.append([
                now_str,
                info.get("event_name", ""),
                info.get("league", ""),
                info.get("game_id", ""),
                info.get("market_type", ""),
                info.get("outcome_name", ""),
                "true",
                max_imp,
                info.get("max_clob_buy", ""),
                info.get("first_seen", ""),
                info.get("last_seen", ""),
                minutes,
            ])
            resolved_keys.append(key)
        else:
            # Never hit 0.99 — silently drop (market removed/restructured)
            dropped_keys.append(key)

    if resolution_rows:
        append_resolutions(resolution_rows)
        log(f"  Resolved {len(resolution_rows)} outcomes")
    for k in resolved_keys + dropped_keys:
        del state[k]
    if dropped_keys:
        log(f"  Dropped {len(dropped_keys)} outcomes (never hit 0.99)")
    if newly_missing:
        log(f"  {newly_missing} outcomes missing this cycle (watching)")

    # ── 7. Persist state ─────────────────────────────────────────────────
    save_state(state)
    return state


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # Back up old-schema CSVs if they exist
    for path in [SNAPSHOTS_CSV, RESOLUTIONS_CSV, STATE_FILE]:
        if os.path.exists(path):
            bak = path + ".v1.bak"
            if not os.path.exists(bak):
                os.rename(path, bak)
                log(f"Backed up {os.path.basename(path)} → {os.path.basename(bak)}")
            else:
                os.remove(path)

    log("=" * 60)
    log("Polymarket Monitor v2 — Comprehensive Data Collection")
    log("=" * 60)
    log(f"  snapshots  → {SNAPSHOTS_CSV}")
    log(f"  resolutions → {RESOLUTIONS_CSV}")
    log(f"  cycle interval: {CYCLE_INTERVAL}s")
    log(f"  CLOB threshold: implied >= {CLOB_MIN_IMPLIED}")
    log(f"  CLOB cap: {CLOB_MAX_PER_CYCLE}/cycle (buy+sell)")
    log(f"  detection: live=true + event_date today/yesterday ET")
    log("")

    # Probe WebSocket
    log("Probing Sports WebSocket...")
    ws_ok = try_websocket_connection()
    if ws_ok:
        log("  WebSocket connected — using live game state feed")
    else:
        log("  WebSocket unavailable — using Gamma API polling (score/period from event data)")
    log("")

    state = {}
    cycle = 0
    while not _shutdown:
        cycle += 1
        write_heartbeat(cycle)
        log(f"=== Cycle {cycle} ===")
        t0 = time.time()
        try:
            state = run_cycle(state)
        except Exception as e:
            log(f"ERROR in cycle: {e}")
            traceback.print_exc()
        elapsed = time.time() - t0
        log(f"  Cycle {cycle} done in {elapsed:.1f}s")
        auto_git_push(cycle)
        log("")

        deadline = t0 + CYCLE_INTERVAL
        while not _shutdown and time.time() < deadline:
            time.sleep(1)

    log("Shutting down — saving state...")
    save_state(state)
    log(f"State saved ({len(state)} tracked outcomes). Goodbye.")


if __name__ == "__main__":
    main()
