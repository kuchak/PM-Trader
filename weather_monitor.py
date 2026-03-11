#!/usr/bin/env python3
"""
Polymarket Weather Monitor — Temperature Market Data Collection

Discovers daily temperature prediction markets via Gamma API tag_id=103040
(Daily Temperature). Tracks probability movements for all active city/date
markets and logs snapshots + resolutions for backtesting.

Markets are structured as: "Highest temperature in {City} on {Date}?"
with bucket outcomes like "41F or below", "42-43F", "56F or higher".

Usage:
    python3 weather_monitor.py              # foreground
    nohup python3 weather_monitor.py > weather_monitor.log 2>&1 &
"""

import csv
import json
import os
import signal
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GAMMA_EVENTS_API = "https://gamma-api.polymarket.com/events"
CLOB_PRICE_API = "https://clob.polymarket.com/price"
TEMPERATURE_TAG_ID = 103040
PAGE_SIZE = 200
CYCLE_INTERVAL = 60  # seconds between scans (temperature moves slowly)
CLOB_MIN_IMPLIED = 0.10  # fetch CLOB for outcomes >= this (lower than sports — more buckets)
CLOB_MAX_IMPLIED = 0.95
CLOB_MAX_PER_CYCLE = 200
MISSING_CYCLES_TO_RESOLVE = 5  # 5 min at 60s intervals

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOTS_CSV = os.path.join(DATA_DIR, "weather_snapshots.csv")
RESOLUTIONS_CSV = os.path.join(DATA_DIR, "weather_resolutions.csv")
STATE_FILE = os.path.join(DATA_DIR, "weather_state.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_monitor.log")

SNAPSHOT_FIELDS = [
    "timestamp", "event_name", "city", "target_date", "series_slug",
    "question", "outcome_name", "implied_prob",
    "clob_buy_price", "clob_sell_price", "spread",
    "best_bid", "best_ask", "volume", "liquidity",
    "token_id", "market_id", "event_id",
]

RESOLUTION_FIELDS = [
    "resolved_timestamp", "event_name", "city", "target_date", "series_slug",
    "question", "outcome_name", "won",
    "max_implied_prob", "max_clob_buy_price",
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
    req.add_header("User-Agent", "polymarket-weather-monitor/1.0")
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


def _safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _extract_city(event_name):
    """Extract city from event title like 'Highest temperature in NYC on March 11?'"""
    name = event_name or ""
    if " in " in name and " on " in name:
        return name.split(" in ", 1)[1].split(" on ", 1)[0].strip()
    return ""


def _extract_target_date(event_name, end_date_str):
    """Extract target date from event title or endDate field."""
    # Try parsing from endDate first
    if end_date_str:
        dt = _parse_iso(end_date_str)
        if dt:
            return dt.strftime("%Y-%m-%d")
    return ""


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


def _fetch_events_page(extra_params, label=""):
    """Fetch paginated events with given extra query params."""
    all_events = []
    offset = 0
    while True:
        url = (
            f"{GAMMA_EVENTS_API}"
            f"?tag_id={TEMPERATURE_TAG_ID}"
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


def fetch_temperature_events():
    """Fetch all active temperature markets.

    Temperature markets don't have live=true or event_date filtering —
    they're active from creation until resolution (end of target day).
    Just fetch all active, unclosed events under the temperature tag.
    """
    events = _fetch_events_page("", "temperature")

    # Filter: only include events that haven't fully resolved
    active = []
    for ev in events:
        markets = ev.get("markets", [])
        if not markets:
            continue
        # Keep if at least one market is not closed
        if any(not m.get("closed") for m in markets):
            active.append(ev)

    return active


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

    # ── 1. Fetch temperature events ───────────────────────────────────────
    events = fetch_temperature_events()

    # ── 2. Extract outcomes ───────────────────────────────────────────────
    outcome_list = []

    for ev in events:
        event_id = str(ev.get("id", ""))
        event_name = ev.get("title", "")
        series_slug = ev.get("seriesSlug", "")
        city = _extract_city(event_name)
        end_date = ev.get("endDate", "")
        target_date = _extract_target_date(event_name, end_date)

        for mkt in ev.get("markets", []):
            if mkt.get("closed"):
                continue

            market_id = str(mkt.get("id", ""))
            question = mkt.get("question", "")
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
                if implied < 0.005 or implied > 0.995:
                    continue

                outcome_list.append((implied, {
                    "event_id": event_id,
                    "event_name": event_name,
                    "city": city,
                    "target_date": target_date,
                    "series_slug": series_slug,
                    "market_id": market_id,
                    "question": question,
                    "outcome_name": outcome_name,
                    "implied_prob": implied_str,
                    "implied_float": implied,
                    "token_id": token_id,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "volume": volume,
                    "liquidity": liquidity,
                }))

    # ── 3. Fetch CLOB prices ──────────────────────────────────────────────
    def _clob_eligible(imp, d):
        if not d["token_id"]:
            return False
        if imp < CLOB_MIN_IMPLIED or imp > CLOB_MAX_IMPLIED:
            return False
        return True

    clob_candidates = sorted(
        [(imp, d) for imp, d in outcome_list if _clob_eligible(imp, d)],
        key=lambda x: -x[0],
    )

    clob_budget = CLOB_MAX_PER_CYCLE
    clob_results = {}
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

    # ── 4. Build snapshot rows & update state ─────────────────────────────
    snapshot_rows = []
    current_keys = set()

    # Collect cities for summary
    cities_seen = set()

    for _, d in outcome_list:
        tid = d["token_id"]
        buy_price, sell_price = clob_results.get(tid, (None, None))

        buy_str = f"{buy_price}" if buy_price is not None else ""
        sell_str = f"{sell_price}" if sell_price is not None else ""
        spread_str = ""
        if buy_price is not None and sell_price is not None:
            spread_str = f"{buy_price - sell_price:.6f}"

        snapshot_rows.append([
            now_str, d["event_name"], d["city"], d["target_date"],
            d["series_slug"], d["question"], d["outcome_name"],
            d["implied_prob"], buy_str, sell_str, spread_str,
            d["best_bid"], d["best_ask"], d["volume"], d["liquidity"],
            d["token_id"], d["market_id"], d["event_id"],
        ])

        cities_seen.add(d["city"])

        key = f"{d['market_id']}:{d['outcome_name']}"
        current_keys.add(key)

        if key not in state:
            state[key] = {
                "first_seen": now_str,
                "max_implied": d["implied_float"],
                "max_clob_buy": buy_price if buy_price else 0,
                "event_name": d["event_name"],
                "city": d["city"],
                "target_date": d["target_date"],
                "series_slug": d["series_slug"],
                "question": d["question"],
                "market_id": d["market_id"],
                "outcome_name": d["outcome_name"],
            }

        s = state[key]
        s["last_seen"] = now_str
        s.pop("missing_cycles", None)

        if d["implied_float"] > (s.get("max_implied") or 0):
            s["max_implied"] = d["implied_float"]
        if buy_price and buy_price > (s.get("max_clob_buy") or 0):
            s["max_clob_buy"] = buy_price

    # ── 5. Write snapshots ────────────────────────────────────────────────
    append_snapshots(snapshot_rows)
    log(f"  {len(events)} events | {len(snapshot_rows)} outcomes | "
        f"{len(cities_seen)} cities | {clob_calls} CLOB calls")

    # ── 6. Detect resolutions ─────────────────────────────────────────────
    resolution_rows = []
    resolved_keys = []
    dropped_keys = []

    for key, info in list(state.items()):
        if key in current_keys:
            continue

        missing = info.get("missing_cycles", 0) + 1
        info["missing_cycles"] = missing

        if missing < MISSING_CYCLES_TO_RESOLVE:
            continue

        max_imp = info.get("max_implied", 0)
        if max_imp >= 0.90:  # temperature buckets resolve decisively
            first_seen = _parse_iso(info.get("first_seen"))
            last_seen = _parse_iso(info.get("last_seen"))
            minutes = 0
            if first_seen and last_seen:
                minutes = round((last_seen - first_seen).total_seconds() / 60, 1)

            resolution_rows.append([
                now_str,
                info.get("event_name", ""),
                info.get("city", ""),
                info.get("target_date", ""),
                info.get("series_slug", ""),
                info.get("question", ""),
                info.get("outcome_name", ""),
                "true" if max_imp >= 0.99 else "likely",
                max_imp,
                info.get("max_clob_buy", ""),
                info.get("first_seen", ""),
                info.get("last_seen", ""),
                minutes,
            ])
            resolved_keys.append(key)
        else:
            dropped_keys.append(key)

    if resolution_rows:
        append_resolutions(resolution_rows)
        log(f"  Resolved {len(resolution_rows)} outcomes")
    for k in resolved_keys + dropped_keys:
        del state[k]
    if dropped_keys:
        log(f"  Dropped {len(dropped_keys)} outcomes (never hit 0.90)")

    # ── 7. Persist state ──────────────────────────────────────────────────
    save_state(state)
    return state


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    log("=" * 60)
    log("Polymarket Weather Monitor v1 — Temperature Data Collection")
    log("=" * 60)
    log(f"  tag_id:       {TEMPERATURE_TAG_ID} (Daily Temperature)")
    log(f"  snapshots  -> {SNAPSHOTS_CSV}")
    log(f"  resolutions-> {RESOLUTIONS_CSV}")
    log(f"  cycle:        {CYCLE_INTERVAL}s")
    log(f"  CLOB range:   {CLOB_MIN_IMPLIED} - {CLOB_MAX_IMPLIED}")
    log(f"  CLOB cap:     {CLOB_MAX_PER_CYCLE}/cycle")
    log("")

    state = load_state()
    log(f"  Loaded state: {len(state)} tracked outcomes")
    log("")

    cycle = 0
    while not _shutdown:
        cycle += 1
        log(f"=== Cycle {cycle} ===")
        t0 = time.time()
        try:
            state = run_cycle(state)
        except Exception as e:
            log(f"ERROR in cycle: {e}")
            traceback.print_exc()
        elapsed = time.time() - t0
        log(f"  Cycle {cycle} done in {elapsed:.1f}s")
        log("")

        deadline = t0 + CYCLE_INTERVAL
        while not _shutdown and time.time() < deadline:
            time.sleep(1)

    log("Shutting down — saving state...")
    save_state(state)
    log(f"State saved ({len(state)} tracked outcomes). Goodbye.")


if __name__ == "__main__":
    main()
