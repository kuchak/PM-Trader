#!/usr/bin/env python3
"""
Crypto Polymarket Monitor v2 — All Timeframes

Monitors BTC/ETH/SOL/XRP markets across 5m, 15m, 1h, 4h, and daily timeframes.
Logs probability snapshots to crypto_snapshots.csv and resolution outcomes to
crypto_resolutions.csv for backtesting.

Market types:
  - Up/Down (5m, 15m, 1h, 4h): Binary bet on price direction
  - Daily Above: Ladder of strike prices, resolves at noon ET

Usage:
    python3 crypto_monitor.py
    nohup python3 crypto_monitor.py > crypto_monitor.log 2>&1 &
"""

import csv
import json
import os
import re
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
CYCLE_INTERVAL = 60  # 1 minute — captures ~5 snapshots per 5-min market
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAPSHOTS_CSV = os.path.join(DATA_DIR, "crypto_snapshots.csv")
RESOLUTIONS_CSV = os.path.join(DATA_DIR, "crypto_resolutions.csv")
STATE_FILE = os.path.join(DATA_DIR, "crypto_state.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crypto_monitor.log")

# Up/Down series slugs we track
UPDOWN_SERIES = set()
for _asset in ["btc", "eth", "sol", "xrp"]:
    for _tf in ["5m", "15m", "4h"]:
        UPDOWN_SERIES.add(f"{_asset}-up-or-down-{_tf}")
    UPDOWN_SERIES.add(f"{_asset}-up-or-down-hourly")

# Daily above assets
DAILY_ABOVE_ASSETS = ["bitcoin", "ethereum"]

ASSET_MAP = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "xrp": "XRP",
}

SNAPSHOT_FIELDS = [
    "timestamp", "event_slug", "series_slug", "asset", "timeframe",
    "market_type", "threshold_price", "outcome", "implied_prob",
    "liquidity", "volume_24h", "minutes_to_expiry", "price_approx",
]

RESOLUTION_FIELDS = [
    "resolved_timestamp", "event_slug", "series_slug", "asset", "timeframe",
    "market_type", "threshold_price", "winning_outcome",
]

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown = False

def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log("Shutdown signal received, finishing current cycle...")

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ---------------------------------------------------------------------------
# State: track events pending resolution
# ---------------------------------------------------------------------------
# { event_slug: { end_time, series, asset, timeframe, market_type, thresholds? } }
_pending = {}
_resolved = set()

def load_state():
    global _pending, _resolved
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        _pending = data.get("pending", {})
        _resolved = set(data.get("resolved", []))
        log(f"  Loaded state: {len(_pending)} pending, {len(_resolved)} resolved")
    except (FileNotFoundError, json.JSONDecodeError):
        _pending = {}
        _resolved = set()

def save_state():
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=48)).isoformat()
    # Prune old entries
    pending_clean = {
        k: v for k, v in _pending.items()
        if k not in _resolved and v.get("end_time", "") > cutoff
    }
    # Cap resolved set
    resolved_recent = sorted(_resolved)[-10000:]
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"pending": pending_clean, "resolved": resolved_recent}, f)
    except Exception as e:
        log(f"  State save error: {e}")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-monitor/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log(f"  HTTP error: {url[:80]}... → {e}")
        return None

# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------
def ensure_csvs():
    os.makedirs(DATA_DIR, exist_ok=True)
    for path, fields in [(SNAPSHOTS_CSV, SNAPSHOT_FIELDS), (RESOLUTIONS_CSV, RESOLUTION_FIELDS)]:
        if not os.path.exists(path):
            with open(path, "w", newline="") as f:
                csv.writer(f).writerow(fields)

def append_snapshots(rows):
    with open(SNAPSHOTS_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)

def append_resolutions(rows):
    with open(RESOLUTIONS_CSV, "a", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def parse_series_info(series_slug):
    """Extract asset and timeframe from a series slug like 'btc-up-or-down-5m'."""
    parts = series_slug.split("-")
    asset = ASSET_MAP.get(parts[0], parts[0].upper())
    if "hourly" in series_slug:
        tf = "1h"
    elif "15m" in series_slug:
        tf = "15m"
    elif "5m" in series_slug:
        tf = "5m"
    elif "4h" in series_slug:
        tf = "4h"
    else:
        tf = "unknown"
    return asset, tf

def parse_threshold(market):
    """Extract numeric threshold from a daily-above market."""
    git = market.get("groupItemTitle", "")
    if git:
        try:
            return int(git.replace(",", "").replace("$", "").strip())
        except (ValueError, TypeError):
            pass
    question = market.get("question", "")
    m = re.search(r'\$([0-9,]+)', question)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except (ValueError, TypeError):
            pass
    return None

def estimate_price(thresholds_and_probs):
    """Find the threshold where Yes prob is closest to 0.50."""
    best_diff, best_price = 1.0, None
    for threshold, yes_prob in thresholds_and_probs:
        diff = abs(yes_prob - 0.50)
        if diff < best_diff:
            best_diff = diff
            best_price = threshold
    return best_price

# ---------------------------------------------------------------------------
# Discovery: Up/Down markets (5m, 15m, 1h, 4h)
# ---------------------------------------------------------------------------
def discover_updown_events():
    """Fetch active up/down crypto events via broad query, filter by seriesSlug."""
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    max_iso = (now + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    url = (
        f"{GAMMA_EVENTS_API}?limit=200&closed=false"
        f"&end_date_min={now_iso}&end_date_max={max_iso}"
        f"&order=endDate&ascending=true"
    )
    data = fetch_json(url)
    if not data:
        return []

    events = []
    for event in data:
        series = event.get("seriesSlug", "")
        if series in UPDOWN_SERIES:
            events.append(event)
    return events

# ---------------------------------------------------------------------------
# Discovery: Daily Above markets
# ---------------------------------------------------------------------------
def get_daily_above_slugs():
    """Generate slugs for yesterday/today/tomorrow daily-above markets."""
    now = datetime.now(timezone.utc)
    slugs = []
    for offset, label in [(-1, "yesterday"), (0, "today"), (1, "tomorrow")]:
        dt = now + timedelta(days=offset)
        month = dt.strftime("%B").lower()
        day = dt.day
        for asset_name in DAILY_ABOVE_ASSETS:
            slugs.append({
                "slug": f"{asset_name}-above-on-{month}-{day}",
                "asset": "BTC" if asset_name == "bitcoin" else "ETH",
                "label": label,
            })
    return slugs

# ---------------------------------------------------------------------------
# Snapshot: process Up/Down events
# ---------------------------------------------------------------------------
def snapshot_updown(events, now):
    """Log probability snapshots for up/down events. Returns rows."""
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []

    for event in events:
        series = event.get("seriesSlug", "")
        slug = event.get("slug", "")
        asset, tf = parse_series_info(series)

        # Parse minutes to expiry
        end_str = event.get("endDate", "")
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            mins_to_exp = max(0, (end_dt - now).total_seconds() / 60)
        except (ValueError, TypeError):
            mins_to_exp = 0

        markets = event.get("markets", [])
        if not markets:
            continue
        market = markets[0]  # Up/Down events have 1 market

        try:
            prices = json.loads(market.get("outcomePrices", "[]"))
            up_prob = float(prices[0]) if prices else 0
        except (json.JSONDecodeError, IndexError, ValueError):
            up_prob = 0

        liq = market.get("liquidityNum", 0) or 0
        vol = market.get("volume24hr", 0) or 0

        rows.append([
            ts, slug, series, asset, tf,
            "up_down", "",  # no threshold for up/down
            "Up", round(up_prob, 6),
            round(liq, 2), round(vol, 2),
            round(mins_to_exp, 1), "",  # no price_approx
        ])

        # Track for resolution
        if slug not in _resolved:
            _pending[slug] = {
                "end_time": end_str,
                "series": series,
                "asset": asset,
                "timeframe": tf,
                "market_type": "up_down",
            }

    return rows

# ---------------------------------------------------------------------------
# Snapshot: process Daily Above events
# ---------------------------------------------------------------------------
def snapshot_daily_above(now):
    """Fetch and log snapshots for daily above markets. Returns rows."""
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = []
    slug_entries = get_daily_above_slugs()

    for entry in slug_entries:
        slug = entry["slug"]
        asset = entry["asset"]
        label = entry["label"]

        url = f"{GAMMA_EVENTS_API}?slug={slug}"
        data = fetch_json(url)
        if not data or not isinstance(data, list) or len(data) == 0:
            continue

        event = data[0]
        is_closed = event.get("closed", False)
        is_active = event.get("active", False)

        # Resolution for yesterday
        if is_closed and label == "yesterday":
            resolve_daily_above(slug, event, now)
            continue

        if not is_active:
            continue

        # Minutes to expiry
        end_str = event.get("endDate", "")
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            mins_to_exp = max(0, (end_dt - now).total_seconds() / 60)
        except (ValueError, TypeError):
            mins_to_exp = 0

        # Parse all strike markets
        threshold_probs = []  # for price estimation
        market_rows = []
        for market in event.get("markets", []):
            threshold = parse_threshold(market)
            if threshold is None:
                continue
            try:
                prices = json.loads(market.get("outcomePrices", "[]"))
                yes_prob = float(prices[0]) if prices else 0
            except (json.JSONDecodeError, IndexError, ValueError):
                yes_prob = 0

            liq = market.get("liquidityNum", 0) or 0
            vol = market.get("volume24hr", 0) or 0
            threshold_probs.append((threshold, yes_prob))
            market_rows.append((threshold, yes_prob, round(liq, 2), round(vol, 2)))

        price_approx = estimate_price(threshold_probs)

        for threshold, yes_prob, liq, vol in market_rows:
            rows.append([
                ts, slug, "", asset, "daily",
                "daily_above", threshold,
                "Yes", round(yes_prob, 6),
                liq, vol,
                round(mins_to_exp, 1), price_approx or "",
            ])

        # Track for resolution
        if slug not in _resolved:
            _pending[slug] = {
                "end_time": end_str,
                "series": "",
                "asset": asset,
                "timeframe": "daily",
                "market_type": "daily_above",
            }

    return rows

# ---------------------------------------------------------------------------
# Resolution: Up/Down markets (batch query for recently closed)
# ---------------------------------------------------------------------------
def resolve_updown(now):
    """Check recently closed events and resolve matching up/down markets."""
    # Find which pending events should be expired
    pending_updown = {
        slug: info for slug, info in _pending.items()
        if info.get("market_type") == "up_down" and slug not in _resolved
    }
    if not pending_updown:
        return 0

    # Query recently closed events (last 20 min window)
    min_dt = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    max_dt = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        f"{GAMMA_EVENTS_API}?limit=200&closed=true"
        f"&end_date_min={min_dt}&end_date_max={max_dt}"
        f"&order=endDate&ascending=true"
    )
    data = fetch_json(url)
    if not data:
        return 0

    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    resolution_rows = []
    resolved_count = 0

    for event in data:
        slug = event.get("slug", "")
        if slug not in pending_updown:
            continue

        markets = event.get("markets", [])
        if not markets:
            _resolved.add(slug)
            continue

        market = markets[0]
        info = pending_updown[slug]

        try:
            prices = json.loads(market.get("outcomePrices", "[]"))
            outcomes = json.loads(market.get("outcomes", "[]"))
            if float(prices[0]) >= 0.99:
                winner = outcomes[0]  # "Up"
            elif len(prices) > 1 and float(prices[1]) >= 0.99:
                winner = outcomes[1]  # "Down"
            else:
                winner = "UNKNOWN"
        except (json.JSONDecodeError, IndexError, ValueError):
            winner = "UNKNOWN"

        resolution_rows.append([
            ts, slug, info.get("series", ""), info["asset"], info["timeframe"],
            "up_down", "", winner,
        ])
        _resolved.add(slug)
        resolved_count += 1

    # Fallback: individually query events expired >10 min ago that we missed
    stale_cutoff = (now - timedelta(minutes=10)).isoformat()
    stale = [
        slug for slug, info in pending_updown.items()
        if slug not in _resolved and info.get("end_time", "Z") < stale_cutoff
    ]
    for slug in stale[:10]:  # cap individual queries
        url = f"{GAMMA_EVENTS_API}?slug={slug}"
        event_data = fetch_json(url)
        if not event_data or not isinstance(event_data, list) or len(event_data) == 0:
            _resolved.add(slug)
            continue

        event = event_data[0]
        if not event.get("closed", False):
            continue

        markets = event.get("markets", [])
        if not markets:
            _resolved.add(slug)
            continue

        market = markets[0]
        info = pending_updown[slug]

        try:
            prices = json.loads(market.get("outcomePrices", "[]"))
            outcomes = json.loads(market.get("outcomes", "[]"))
            if float(prices[0]) >= 0.99:
                winner = outcomes[0]
            elif len(prices) > 1 and float(prices[1]) >= 0.99:
                winner = outcomes[1]
            else:
                winner = "UNKNOWN"
        except (json.JSONDecodeError, IndexError, ValueError):
            winner = "UNKNOWN"

        resolution_rows.append([
            ts, slug, info.get("series", ""), info["asset"], info["timeframe"],
            "up_down", "", winner,
        ])
        _resolved.add(slug)
        resolved_count += 1

    if resolution_rows:
        append_resolutions(resolution_rows)

    return resolved_count

# ---------------------------------------------------------------------------
# Resolution: Daily Above markets
# ---------------------------------------------------------------------------
def resolve_daily_above(slug, event, now):
    """Resolve a closed daily-above event — one resolution row per threshold."""
    if slug in _resolved:
        return 0

    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    asset = "BTC" if "bitcoin" in slug else "ETH" if "ethereum" in slug else ""
    resolution_rows = []

    for market in event.get("markets", []):
        threshold = parse_threshold(market)
        if threshold is None:
            continue
        try:
            prices = json.loads(market.get("outcomePrices", "[]"))
            yes_price = float(prices[0]) if prices else 0
            if yes_price >= 0.99:
                winner = "YES"
            elif yes_price <= 0.01:
                winner = "NO"
            else:
                continue  # not yet resolved
        except (json.JSONDecodeError, IndexError, ValueError):
            continue

        resolution_rows.append([
            ts, slug, "", asset, "daily",
            "daily_above", threshold, winner,
        ])

    if resolution_rows:
        append_resolutions(resolution_rows)
        _resolved.add(slug)
        log(f"  Resolved {slug}: {len(resolution_rows)} thresholds")

    return len(resolution_rows)

# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------
def run_cycle():
    """One full monitoring cycle."""
    now = datetime.now(timezone.utc)
    all_rows = []

    # 1. Discover and snapshot up/down markets
    updown_events = discover_updown_events()
    updown_rows = snapshot_updown(updown_events, now)
    all_rows.extend(updown_rows)

    # 2. Snapshot daily above markets (+ resolve yesterday's)
    daily_rows = snapshot_daily_above(now)
    all_rows.extend(daily_rows)

    # 3. Write all snapshots
    if all_rows:
        append_snapshots(all_rows)

    # 4. Check resolutions for expired up/down events
    resolved = resolve_updown(now)

    # 5. Persist state
    save_state()

    return len(all_rows), resolved

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log("=" * 60)
    log("Crypto Polymarket Monitor v2 — All Timeframes")
    log(f"  Cycle interval: {CYCLE_INTERVAL}s")
    log(f"  Snapshots: {SNAPSHOTS_CSV}")
    log(f"  Resolutions: {RESOLUTIONS_CSV}")
    log(f"  Tracking: {len(UPDOWN_SERIES)} up/down series + daily above (BTC/ETH)")
    log("=" * 60)

    ensure_csvs()
    load_state()
    cycle = 0

    while not _shutdown:
        cycle += 1
        try:
            t0 = time.time()
            snap_count, res_count = run_cycle()
            elapsed = time.time() - t0
            updown_active = sum(
                1 for slug in _pending
                if slug not in _resolved and _pending[slug].get("market_type") == "up_down"
            )
            log(f"Cycle {cycle}: {snap_count} snapshots, {res_count} resolved, "
                f"{updown_active} pending | {elapsed:.1f}s")
        except Exception as e:
            log(f"Cycle {cycle} ERROR: {e}")
            traceback.print_exc()

        for _ in range(CYCLE_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    save_state()
    log("Crypto monitor stopped.")

if __name__ == "__main__":
    main()
