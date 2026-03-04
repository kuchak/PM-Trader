#!/usr/bin/env python3
"""
Polymarket Data Analysis — Post-Collection Analytics

Reads resolutions.csv (and optionally market_snapshots.csv) after data
collection and produces six analysis tables:

  1. Calibration by implied_prob
  2. Calibration by clob_buy_price
  3. Spread analysis (buy-sell spread and implied-vs-clob gap by bucket)
  4. Time to resolution by league
  5. Price stability (outcomes that reached 90%+)
  6. Expected value by clob_buy_price bucket

Usage:
    python3 analyze_data.py
    python3 analyze_data.py --data-dir /path/to/data
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ---------------------------------------------------------------------------
# Probability buckets (per spec)
# ---------------------------------------------------------------------------
BUCKETS = [
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.85),
    (0.85, 0.90),
    (0.90, 0.95),
    (0.95, 1.01),  # 1.01 to include 1.0
]


def bucket_label(lo, hi):
    hi_display = min(hi, 1.0)
    return f"{lo:.0%}-{hi_display:.0%}"


def find_bucket(prob):
    for lo, hi in BUCKETS:
        if lo <= prob < hi:
            return bucket_label(lo, hi)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def load_csv(path, label):
    if not os.path.exists(path):
        print(f"  {label}: not found at {path}")
        return []
    with open(path) as f:
        rows = list(csv.DictReader(f))
    print(f"  {label}: {len(rows)} rows")
    return rows


def print_divider(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


# ---------------------------------------------------------------------------
# Table 1: Calibration by implied_prob
# ---------------------------------------------------------------------------


def table_calibration_implied(records):
    print_divider("TABLE 1: Calibration by Implied Probability")
    _calibration_table(records, "final_implied", "implied_prob (outcomePrices)")


# ---------------------------------------------------------------------------
# Table 2: Calibration by clob_buy_price
# ---------------------------------------------------------------------------


def table_calibration_clob(records):
    print_divider("TABLE 2: Calibration by CLOB Buy Price")
    print("  This shows the REAL cost vs actual win rate.")
    print("  The edge (or lack of) lives here.\n")
    _calibration_table(records, "final_clob_buy", "clob_buy_price")


def _calibration_table(records, field, label):
    bucket_wins = defaultdict(int)
    bucket_total = defaultdict(int)
    skipped = 0

    for rec in records:
        prob = rec.get(field)
        if prob is None or prob < 0.50:
            skipped += 1
            continue
        bl = find_bucket(prob)
        if bl:
            bucket_total[bl] += 1
            if rec["won"]:
                bucket_wins[bl] += 1

    print(f"  Field: {label}")
    if skipped:
        print(f"  ({skipped} outcomes skipped — no data or < 50%)\n")
    print(f"  {'Bucket':<12} {'Count':>6} {'Wins':>6} {'Win%':>8} "
          f"{'Expected':>10} {'Delta':>8}")
    print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*8} {'-'*10} {'-'*8}")

    total_n = 0
    for lo, hi in BUCKETS:
        bl = bucket_label(lo, hi)
        n = bucket_total.get(bl, 0)
        w = bucket_wins.get(bl, 0)
        total_n += n
        expected = (lo + min(hi, 1.0)) / 2
        if n == 0:
            print(f"  {bl:<12} {n:>6} {w:>6} {'n/a':>8} "
                  f"{expected:>10.1%} {'n/a':>8}")
        else:
            rate = w / n
            delta = rate - expected
            sign = "+" if delta >= 0 else ""
            print(f"  {bl:<12} {n:>6} {w:>6} {rate:>8.1%} "
                  f"{expected:>10.1%} {sign}{delta:>7.1%}")

    print(f"  {'TOTAL':<12} {total_n:>6}")
    return total_n


# ---------------------------------------------------------------------------
# Table 3: Spread analysis
# ---------------------------------------------------------------------------


def table_spread_analysis(snapshots):
    print_divider("TABLE 3: Spread Analysis by Implied Prob Bucket")
    print("  Shows the 'tax' on each trade: avg spread and avg gap\n")

    bucket_spreads = defaultdict(list)
    bucket_gaps = defaultdict(list)

    for row in snapshots:
        implied = safe_float(row.get("implied_prob"))
        spread = safe_float(row.get("spread"))
        clob_buy = safe_float(row.get("clob_buy_price"))

        if implied is None or implied < 0.50:
            continue

        bl = find_bucket(implied)
        if not bl:
            continue

        if spread is not None:
            bucket_spreads[bl].append(spread)
        if clob_buy is not None and implied is not None:
            bucket_gaps[bl].append(clob_buy - implied)

    print(f"  {'Bucket':<12} {'N(spread)':>10} {'Avg Spread':>12} "
          f"{'N(gap)':>8} {'Avg Gap':>10} {'Gap = clob_buy - implied':>26}")
    print(f"  {'-'*12} {'-'*10} {'-'*12} {'-'*8} {'-'*10} {'-'*26}")

    for lo, hi in BUCKETS:
        bl = bucket_label(lo, hi)
        spreads = bucket_spreads.get(bl, [])
        gaps = bucket_gaps.get(bl, [])
        avg_sp = sum(spreads) / len(spreads) if spreads else 0
        avg_gap = sum(gaps) / len(gaps) if gaps else 0
        print(f"  {bl:<12} {len(spreads):>10} {avg_sp:>12.4f} "
              f"{len(gaps):>8} {avg_gap:>10.4f}")


# ---------------------------------------------------------------------------
# Table 4: Time to resolution by league
# ---------------------------------------------------------------------------


def table_time_to_resolution(records):
    print_divider("TABLE 4: Time to Resolution by League")
    print("  Shows capital velocity by sport\n")

    league_times = defaultdict(list)
    for rec in records:
        mins = rec.get("minutes_tracked")
        league = rec.get("league", "unknown") or "unknown"
        if mins is not None:
            league_times[league].append(mins)

    print(f"  {'League':<25} {'Count':>6} {'Avg':>8} {'Median':>8} "
          f"{'Min':>8} {'Max':>8}")
    print(f"  {'-'*25} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")

    for league in sorted(league_times.keys(),
                         key=lambda x: -len(league_times[x])):
        times = sorted(league_times[league])
        n = len(times)
        avg = sum(times) / n
        median = times[n // 2] if n % 2 == 1 else (times[n // 2 - 1] + times[n // 2]) / 2
        print(f"  {league:<25} {n:>6} {avg:>8.1f} {median:>8.1f} "
              f"{min(times):>8.1f} {max(times):>8.1f}")


# ---------------------------------------------------------------------------
# Table 5: Price stability
# ---------------------------------------------------------------------------


def table_price_stability(records):
    print_divider("TABLE 5: Price Stability (Outcomes That Reached 90%+ Implied)")
    print("  Shows signal reliability for high-confidence outcomes\n")

    reached_90 = [r for r in records if r.get("max_implied") is not None
                  and r["max_implied"] >= 0.90]

    if not reached_90:
        print("  No outcomes reached 90% implied probability yet.")
        return

    stayed_90 = sum(1 for r in reached_90
                    if r.get("final_implied") is not None
                    and r["final_implied"] >= 0.90)
    dropped_85 = sum(1 for r in reached_90
                     if r.get("final_implied") is not None
                     and r["final_implied"] < 0.85)
    won = sum(1 for r in reached_90 if r["won"])

    print(f"  Outcomes that ever reached 90%:     {len(reached_90)}")
    print(f"  Stayed above 90% at resolution:     {stayed_90} "
          f"({stayed_90/len(reached_90):.1%})")
    print(f"  Dropped below 85% at resolution:    {dropped_85} "
          f"({dropped_85/len(reached_90):.1%})")
    print(f"  Actually won:                       {won} "
          f"({won/len(reached_90):.1%})")


# ---------------------------------------------------------------------------
# Table 6: Expected value by clob_buy_price bucket
# ---------------------------------------------------------------------------


def table_expected_value(records):
    print_divider("TABLE 6: Expected Value by CLOB Buy Price Bucket")
    print("  EV = (win_rate × $1.00) − clob_buy_price")
    print("  Positive EV = profitable strategy. THIS IS THE MONEY TABLE.\n")

    bucket_data = defaultdict(lambda: {"wins": 0, "total": 0, "costs": []})

    for rec in records:
        cost = rec.get("final_clob_buy")
        if cost is None or cost < 0.50:
            continue
        bl = find_bucket(cost)
        if not bl:
            continue
        bucket_data[bl]["total"] += 1
        bucket_data[bl]["costs"].append(cost)
        if rec["won"]:
            bucket_data[bl]["wins"] += 1

    print(f"  {'Bucket':<12} {'Count':>6} {'Win%':>8} {'Avg Cost':>10} "
          f"{'EV/trade':>10} {'Signal':>10}")
    print(f"  {'-'*12} {'-'*6} {'-'*8} {'-'*10} {'-'*10} {'-'*10}")

    for lo, hi in BUCKETS:
        bl = bucket_label(lo, hi)
        d = bucket_data.get(bl)
        if not d or d["total"] == 0:
            print(f"  {bl:<12} {'0':>6} {'n/a':>8} {'n/a':>10} "
                  f"{'n/a':>10} {'':>10}")
            continue

        n = d["total"]
        win_rate = d["wins"] / n
        avg_cost = sum(d["costs"]) / n
        ev = (win_rate * 1.0) - avg_cost
        signal = "+EV" if ev > 0.005 else ("-EV" if ev < -0.005 else "~FAIR")

        print(f"  {bl:<12} {n:>6} {win_rate:>8.1%} {avg_cost:>10.4f} "
              f"{'$':>1}{ev:>+9.4f} {signal:>10}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(records, snapshots):
    print_divider("DATASET SUMMARY")

    total = len(records)
    wins = sum(1 for r in records if r["won"])
    leagues = defaultdict(int)
    for r in records:
        leagues[r.get("league", "unknown") or "unknown"] += 1

    print(f"  Resolved outcomes:  {total}")
    print(f"  Winners:            {wins}")
    print(f"  Losers:             {total - wins}")
    if total:
        print(f"  Overall win rate:   {wins/total:.1%}")
    print()
    print(f"  By league:")
    for league, cnt in sorted(leagues.items(), key=lambda x: -x[1]):
        print(f"    {league}: {cnt}")
    print()
    if snapshots:
        print(f"  Snapshots collected: {len(snapshots)}")


# ---------------------------------------------------------------------------
# Build calibration records from resolutions.csv
# ---------------------------------------------------------------------------


def build_records(resolutions):
    """Convert resolution CSV rows into analysis-ready dicts."""
    records = []
    for row in resolutions:
        won_str = row.get("won", "")
        if won_str not in ("true", "false"):
            continue  # skip unknown/unresolved

        records.append({
            "event_name": row.get("event_name", ""),
            "league": row.get("league", ""),
            "game_id": row.get("game_id", ""),
            "market_type": row.get("market_type", ""),
            "outcome_name": row.get("outcome_name", ""),
            "won": won_str == "true",
            "final_implied": safe_float(row.get("final_implied_prob")),
            "final_clob_buy": safe_float(row.get("final_clob_buy_price")),
            "first_implied": safe_float(row.get("first_seen_implied_prob")),
            "first_clob_buy": safe_float(row.get("first_seen_clob_buy_price")),
            "max_implied": safe_float(row.get("max_implied_prob")),
            "max_clob_buy": safe_float(row.get("max_clob_buy_price")),
            "minutes_tracked": safe_float(row.get("minutes_tracked")),
        })
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Polymarket data analysis")
    parser.add_argument("--data-dir", default=DATA_DIR,
                        help="Directory containing CSV files")
    args = parser.parse_args()

    print("=" * 70)
    print("  Polymarket Data Analysis")
    print("=" * 70)
    print()

    snapshots_path = os.path.join(args.data_dir, "market_snapshots.csv")
    resolutions_path = os.path.join(args.data_dir, "resolutions.csv")

    snapshots = load_csv(snapshots_path, "market_snapshots.csv")
    resolutions = load_csv(resolutions_path, "resolutions.csv")

    if not resolutions:
        print("\n  No resolutions to analyze yet.")
        print("  Let the monitor run until games finish, then re-run.")
        if snapshots:
            unique_events = set(r.get("event_name", "") for r in snapshots)
            unique_markets = set(r.get("market_id", "") for r in snapshots)
            ts_min = min(r["timestamp"] for r in snapshots)[:19]
            ts_max = max(r["timestamp"] for r in snapshots)[:19]
            print(f"\n  Data collected so far:")
            print(f"    {len(snapshots)} snapshots")
            print(f"    {len(unique_events)} unique events")
            print(f"    {len(unique_markets)} unique markets")
            print(f"    Time range: {ts_min} — {ts_max}")
        return

    records = build_records(resolutions)
    print(f"\n  Analysis records (verified won/lost): {len(records)}")

    if not records:
        print("\n  No verified resolutions with known winners yet.")
        print("  Markets may still be open or winners undetermined.")
        return

    # Summary
    print_summary(records, snapshots)

    # Table 1: Calibration by implied_prob
    table_calibration_implied(records)

    # Table 2: Calibration by clob_buy_price
    table_calibration_clob(records)

    # Table 3: Spread analysis
    if snapshots:
        table_spread_analysis(snapshots)
    else:
        print("\n  [Table 3 skipped — no snapshots data]")

    # Table 4: Time to resolution
    table_time_to_resolution(records)

    # Table 5: Price stability
    table_price_stability(records)

    # Table 6: Expected value
    table_expected_value(records)

    # Footer
    print(f"\n{'='*70}")
    print("  NOTES")
    print(f"{'='*70}")
    print("  - Only outcomes with prob >= 50% are bucketed (the favored side)")
    print("  - Delta > 0 → market underestimates win probability")
    print("  - Delta < 0 → market overestimates win probability")
    print("  - Spread = clob_buy − clob_sell (round-trip cost)")
    print("  - Gap = clob_buy − implied_prob (price premium over mid)")
    print("  - +EV buckets with count < 50 are NOT statistically reliable")
    if len(records) < 200:
        print(f"\n  WARNING: Only {len(records)} resolved outcomes.")
        print("  Need 200+ for meaningful calibration, 1000+ for EV analysis.")
        print("  Let the monitor run for 24+ hours of active games.")


if __name__ == "__main__":
    main()
