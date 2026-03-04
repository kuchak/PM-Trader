#!/usr/bin/env python3
"""
Polymarket Calibration Analysis

Reads market_snapshots.csv and resolutions.csv, then queries the Gamma API
to verify which markets actually resolved and who won. Calculates win rate
by implied_prob bucket and clob_buy_price bucket.

A market is "truly resolved" when Gamma shows closed=True and outcomePrices
has snapped to 1/0. Pagination flickers (market disappears then reappears)
are excluded by verifying against the API.

Usage:
    python3 analyze_calibration.py
    python3 analyze_calibration.py --data-dir /path/to/data
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _api_get(url):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "polymarket-monitor/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
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


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_snapshots(data_dir):
    """Load market_snapshots.csv. Returns list of dicts."""
    path = os.path.join(data_dir, "market_snapshots.csv")
    if not os.path.exists(path):
        print(f"ERROR: {path} not found")
        sys.exit(1)
    with open(path) as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} snapshot rows")
    return rows


def load_resolutions(data_dir):
    """Load resolutions.csv. Returns list of dicts."""
    path = os.path.join(data_dir, "resolutions.csv")
    if not os.path.exists(path):
        print(f"No resolutions.csv found — no markets have resolved yet")
        return []
    with open(path) as f:
        rows = list(csv.DictReader(f))
    print(f"Loaded {len(rows)} resolution rows")
    return rows


# ---------------------------------------------------------------------------
# Resolution verification via Gamma API
# ---------------------------------------------------------------------------


def verify_resolutions(resolutions):
    """Query Gamma API for each resolved market to check if truly closed
    and determine the actual winner.

    Returns dict: market_id -> {closed, winning_outcome, outcomes, outcome_prices}
    """
    market_ids = set(r["market_id"] for r in resolutions)
    print(f"Verifying {len(market_ids)} unique markets against Gamma API...")

    results = {}
    for i, mid in enumerate(sorted(market_ids)):
        try:
            mkt = _api_get(f"https://gamma-api.polymarket.com/markets/{mid}")
        except Exception as e:
            print(f"  WARN: failed to fetch market {mid}: {e}")
            continue

        closed = mkt.get("closed", False)
        outcomes = _parse_json_field(mkt.get("outcomes"))
        prices = _parse_json_field(mkt.get("outcomePrices"))

        winning_outcome = None
        if closed and prices:
            for j, p in enumerate(prices):
                try:
                    if float(p) >= 0.99 and j < len(outcomes):
                        winning_outcome = outcomes[j]
                        break
                except (ValueError, TypeError):
                    pass

        results[mid] = {
            "closed": closed,
            "winning_outcome": winning_outcome,
            "outcomes": outcomes,
            "outcome_prices": prices,
        }

        # Rate limit: 0.1s between calls
        if i < len(market_ids) - 1:
            time.sleep(0.1)

    truly_closed = sum(1 for v in results.values() if v["closed"])
    with_winner = sum(1 for v in results.values() if v["winning_outcome"])
    flickers = sum(1 for v in results.values() if not v["closed"])
    print(f"  Truly closed: {truly_closed}")
    print(f"  Winner determined: {with_winner}")
    print(f"  Pagination flickers (still open): {flickers}")
    return results


# ---------------------------------------------------------------------------
# Build outcome-level records for calibration
# ---------------------------------------------------------------------------


def build_calibration_records(snapshots, resolutions, api_results):
    """For each resolved outcome, find the earliest snapshot and pair it
    with the actual result.

    Returns list of dicts with:
        market_id, outcome_name, question, league,
        first_implied_prob, first_clob_buy_price,
        last_implied_prob, last_clob_buy_price,
        won (bool)
    """
    # Index: earliest snapshot per (market_id, outcome_name)
    earliest = {}  # (mid, outcome) -> snapshot row
    latest = {}
    for row in snapshots:
        key = (row["market_id"], row["outcome_name"])
        ts = row["timestamp"]
        if key not in earliest or ts < earliest[key]["timestamp"]:
            earliest[key] = row
        if key not in latest or ts > latest[key]["timestamp"]:
            latest[key] = row

    records = []
    for res in resolutions:
        mid = res["market_id"]
        outcome = res["outcome_name"]
        api = api_results.get(mid)
        if not api or not api["closed"] or not api["winning_outcome"]:
            continue

        won = (outcome == api["winning_outcome"])

        first_snap = earliest.get((mid, outcome))
        last_snap = latest.get((mid, outcome))

        def safe_float(val):
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        records.append({
            "market_id": mid,
            "outcome_name": outcome,
            "question": res.get("question", ""),
            "league": res.get("league", ""),
            "first_implied": safe_float(first_snap["implied_prob"]) if first_snap else None,
            "first_clob": safe_float(first_snap["clob_buy_price"]) if first_snap else None,
            "last_implied": safe_float(res.get("last_implied_prob")),
            "last_clob": safe_float(res.get("last_clob_buy_price")),
            "won": won,
            "minutes_tracked": safe_float(res.get("minutes_tracked")),
        })

    return records


# ---------------------------------------------------------------------------
# Calibration analysis
# ---------------------------------------------------------------------------


def bucket_label(lo, hi):
    return f"{lo:.0%}-{hi:.0%}"


BUCKETS = [
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.01),  # 1.01 to include 1.0
]


def compute_calibration(records, field, label):
    """Compute win rate by bucket for a given probability field."""
    bucket_wins = defaultdict(int)
    bucket_total = defaultdict(int)
    skipped = 0

    for rec in records:
        prob = rec.get(field)
        if prob is None:
            skipped += 1
            continue
        # Only analyze the favored side (prob >= 0.50)
        if prob < 0.50:
            continue
        for lo, hi in BUCKETS:
            if lo <= prob < hi:
                bucket_total[bucket_label(lo, hi)] += 1
                if rec["won"]:
                    bucket_wins[bucket_label(lo, hi)] += 1
                break

    print(f"\n{'='*60}")
    print(f"CALIBRATION: {label}")
    print(f"{'='*60}")
    print(f"  (only outcomes with {field} >= 0.50)")
    if skipped:
        print(f"  ({skipped} outcomes skipped — no {field} data)")
    print()
    print(f"  {'Bucket':<12} {'Won':>5} {'Total':>7} {'Win Rate':>10} {'Expected':>10} {'Delta':>8}")
    print(f"  {'-'*12} {'-'*5} {'-'*7} {'-'*10} {'-'*10} {'-'*8}")

    total_n = 0
    for lo, hi in BUCKETS:
        bl = bucket_label(lo, hi)
        n = bucket_total.get(bl, 0)
        w = bucket_wins.get(bl, 0)
        total_n += n
        if n == 0:
            print(f"  {bl:<12} {w:>5} {n:>7} {'n/a':>10} {(lo+min(hi,1))/2:>10.1%} {'n/a':>8}")
        else:
            rate = w / n
            expected = (lo + min(hi, 1.0)) / 2
            delta = rate - expected
            sign = "+" if delta >= 0 else ""
            print(f"  {bl:<12} {w:>5} {n:>7} {rate:>10.1%} {expected:>10.1%} {sign}{delta:>7.1%}")

    print(f"  {'':12} {'':5} {total_n:>7}")
    return total_n


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------


def print_summary(records):
    """Print overall summary stats."""
    print(f"\n{'='*60}")
    print("DATASET SUMMARY")
    print(f"{'='*60}")
    total = len(records)
    wins = sum(1 for r in records if r["won"])
    leagues = defaultdict(int)
    for r in records:
        leagues[r["league"]] += 1

    print(f"  Resolved outcomes: {total}")
    print(f"  Winners: {wins}")
    print(f"  Losers: {total - wins}")
    if total:
        print(f"  Overall win rate (all outcomes): {wins/total:.1%}")
    print()
    print(f"  By league:")
    for league, cnt in sorted(leagues.items(), key=lambda x: -x[1]):
        print(f"    {league or 'unknown'}: {cnt}")

    # Avg tracking time
    times = [r["minutes_tracked"] for r in records if r["minutes_tracked"]]
    if times:
        print(f"\n  Avg tracking time: {sum(times)/len(times):.1f} min")
        print(f"  Min: {min(times):.1f} min, Max: {max(times):.1f} min")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Polymarket calibration analysis")
    parser.add_argument("--data-dir", default=DATA_DIR,
                        help="Directory containing CSV files")
    args = parser.parse_args()

    print("Polymarket Calibration Analysis")
    print("=" * 60)
    print()

    snapshots = load_snapshots(args.data_dir)
    resolutions = load_resolutions(args.data_dir)

    if not resolutions:
        print("\nNo resolutions to analyze yet. Let the monitor run until")
        print("games finish and markets close, then re-run this script.")

        # Show what we're currently tracking
        unique_markets = set((r["market_id"], r["question"]) for r in snapshots)
        unique_events = set(r["event_id"] for r in snapshots)
        ts_range = (min(r["timestamp"] for r in snapshots)[:19],
                    max(r["timestamp"] for r in snapshots)[:19])
        print(f"\nData collected so far:")
        print(f"  {len(snapshots)} snapshots")
        print(f"  {len(unique_events)} unique events")
        print(f"  {len(unique_markets)} unique markets")
        print(f"  Time range: {ts_range[0]} to {ts_range[1]}")
        return

    # Verify resolutions against API
    api_results = verify_resolutions(resolutions)

    # Build calibration dataset
    records = build_calibration_records(snapshots, resolutions, api_results)
    print(f"\nCalibration records (verified resolved outcomes): {len(records)}")

    if not records:
        print("\nNo verified resolved markets yet.")
        print("Markets may have disappeared due to pagination flickers")
        print("rather than actual resolution. Let the monitor run longer.")
        return

    # Summary
    print_summary(records)

    # Calibration: first snapshot implied prob
    n1 = compute_calibration(records, "first_implied", "First Implied Probability (outcomePrices)")

    # Calibration: last snapshot implied prob
    compute_calibration(records, "last_implied", "Last Implied Probability (at resolution)")

    # Calibration: first CLOB buy price
    n2 = compute_calibration(records, "first_clob", "First CLOB Buy Price")

    # Calibration: last CLOB buy price
    compute_calibration(records, "last_clob", "Last CLOB Buy Price (at resolution)")

    print(f"\n{'='*60}")
    print("NOTES")
    print(f"{'='*60}")
    print("  - 'First' = earliest snapshot captured (may not be game start)")
    print("  - 'Last' = final snapshot before market disappeared")
    print("  - Only outcomes with prob >= 0.50 are bucketed (the favored side)")
    print("  - CLOB prices require implied >= 0.50 to be fetched (by design)")
    print("  - Delta > 0 means market underestimates win probability")
    print("  - Delta < 0 means market overestimates win probability")
    print("  - A well-calibrated market has Delta near 0 in every bucket")
    if n1 < 100:
        print(f"\n  WARNING: Only {n1} favored outcomes analyzed.")
        print("  Need 100+ per bucket for statistical significance.")
        print("  Let the monitor run longer to collect more data.")


if __name__ == "__main__":
    main()
