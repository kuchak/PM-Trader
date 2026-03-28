#!/usr/bin/env python3
"""
Comprehensive backtest of the crypto trading strategy using monitor data.
Uses crypto_snapshots.csv (302k rows) and crypto_resolutions.csv (9,771 resolutions).
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime

SNAPSHOTS_FILE = "data/crypto_snapshots.csv"
RESOLUTIONS_FILE = "data/crypto_resolutions.csv"

# Current strategy parameters
ENTRY_THRESHOLD = 0.90
SKIP_ABOVE = 0.99
STOP_LOSS = 0.85
TARGET_EXIT = 0.99
MAX_CONCURRENT = 6

# Time windows (minutes remaining)
TIME_WINDOWS = {
    "15m": (3, 13),
    "1h": (2, 50),
}

# Bet sizes as fraction of bankroll
BET_SIZES = {
    ("BTC", "1h"): 0.30,
    ("XRP", "1h"): 0.30,
    ("ETH", "1h"): 0.15,
    ("BTC", "15m"): 0.10,
    ("ETH", "15m"): 0.10,
    ("XRP", "15m"): 0.08,
}

# Markets we trade (no SOL 15m, no 5m, no 4h, no daily)
TRADED_MARKETS = set(BET_SIZES.keys())


def load_resolutions():
    """Load resolutions CSV and build a lookup: event_slug -> winning_outcome."""
    resolutions = {}
    with open(RESOLUTIONS_FILE) as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if len(row) < 8:
                continue
            event_slug = row[1]
            timeframe = row[4]
            market_type = row[5]
            winning_outcome = row[7]
            if market_type == "up_down" and timeframe in ("15m", "1h"):
                resolutions[event_slug] = winning_outcome
    return resolutions


def load_snapshots():
    """Load snapshots CSV, filter to up_down 15m/1h markets.

    Returns dict: event_slug -> list of (timestamp, prob_up, minutes_to_expiry)
    sorted by timestamp.
    """
    markets = defaultdict(list)
    market_info = {}  # event_slug -> (asset, timeframe)

    with open(SNAPSHOTS_FILE) as f:
        reader = csv.reader(f)
        header = next(reader)

        for row in reader:
            # Up/down rows have 13 fields:
            # ts, slug, series, asset, tf, "up_down", "", "Up", prob, liq, vol, mins, ""
            # Daily above rows have 11 fields (different schema)
            if len(row) < 13:
                continue

            market_type = row[5]
            if market_type != "up_down":
                continue

            tf = row[4]
            if tf not in ("15m", "1h"):
                continue

            asset = row[3]
            if (asset, tf) not in TRADED_MARKETS:
                continue

            slug = row[1]
            outcome_label = row[7]  # "Up" or "Down" - this is always "Up" prob

            try:
                prob = float(row[8])
                mins = float(row[11])
                ts = row[0]
            except (ValueError, IndexError):
                continue

            markets[slug].append((ts, prob, mins))
            if slug not in market_info:
                market_info[slug] = (asset, tf)

    # Sort each market's snapshots by timestamp
    for slug in markets:
        markets[slug].sort(key=lambda x: x[0])

    return markets, market_info


def simulate_trades(markets, market_info, resolutions,
                    entry_threshold=0.90, stop_loss=0.85,
                    target_exit=0.99, skip_above=0.99,
                    time_windows=None, max_concurrent=6,
                    track_concurrent=True):
    """
    Simulate trading on all markets.

    For each market (unique slug), we look at snapshots over time.
    Entry: first snapshot where prob >= entry_threshold, prob < skip_above,
           and minutes_to_expiry is in the valid window for that timeframe.
    After entry, track prob: did it hit target_exit (win) or drop below stop_loss?
    If neither by expiry, check resolution.

    Returns list of trade dicts.
    """
    if time_windows is None:
        time_windows = TIME_WINDOWS

    trades = []
    active_positions = []  # list of (slug, entry_time) for concurrent tracking

    # Process markets in chronological order of their first eligible entry
    # First, find potential entry time for each market
    market_entries = []
    for slug, snapshots in markets.items():
        asset, tf = market_info[slug]
        tw_min, tw_max = time_windows[tf]

        for ts, prob, mins in snapshots:
            if tw_min <= mins <= tw_max and prob >= entry_threshold and prob < skip_above:
                market_entries.append((ts, slug))
                break

    market_entries.sort(key=lambda x: x[0])

    # Now simulate in order
    for entry_ts_candidate, slug in market_entries:
        asset, tf = market_info[slug]
        tw_min, tw_max = time_windows[tf]
        snapshots = markets[slug]

        # Clean up expired positions
        if track_concurrent:
            # Remove positions for markets that have expired
            active_positions = [
                (s, t) for s, t in active_positions
                if s in markets and markets[s][-1][2] > 0  # still has time
            ]

            if len(active_positions) >= max_concurrent:
                continue

        # Find entry point
        entry_idx = None
        entry_prob = None
        entry_ts = None

        for i, (ts, prob, mins) in enumerate(snapshots):
            if tw_min <= mins <= tw_max and prob >= entry_threshold and prob < skip_above:
                entry_idx = i
                entry_prob = prob
                entry_ts = ts
                break

        if entry_idx is None:
            continue

        # Track what happens after entry
        outcome = None
        exit_prob = None
        exit_reason = None
        max_prob_after = entry_prob
        min_prob_after = entry_prob

        for i in range(entry_idx + 1, len(snapshots)):
            ts, prob, mins = snapshots[i]
            max_prob_after = max(max_prob_after, prob)
            min_prob_after = min(min_prob_after, prob)

            if prob >= target_exit:
                outcome = "WIN"
                exit_prob = target_exit
                exit_reason = "target"
                break
            elif prob <= stop_loss:
                outcome = "LOSS"
                exit_prob = prob
                exit_reason = "stop_loss"
                break

        # If neither hit, check resolution
        if outcome is None:
            resolution = resolutions.get(slug)
            if resolution:
                # For Up outcome at entry: win if resolution is "Up"
                # We always track "Up" probability
                # If entry_prob >= threshold, we're betting on "Up"
                if entry_prob >= 0.5:
                    # Betting Up
                    if resolution == "Up":
                        outcome = "WIN"
                        exit_prob = 1.0
                        exit_reason = "resolution_win"
                    else:
                        outcome = "LOSS"
                        exit_prob = 0.0
                        exit_reason = "resolution_loss"
                else:
                    # Betting Down (prob of Up < 0.5, so Down prob > 0.5)
                    if resolution == "Down":
                        outcome = "WIN"
                        exit_prob = 1.0
                        exit_reason = "resolution_win"
                    else:
                        outcome = "LOSS"
                        exit_prob = 0.0
                        exit_reason = "resolution_loss"
            else:
                # No resolution found - skip this trade
                outcome = "UNKNOWN"
                exit_prob = snapshots[-1][1] if snapshots else entry_prob
                exit_reason = "no_resolution"

        # Calculate PnL
        # We buy at entry_prob, each share pays $1 if win
        # shares = bet_amount / entry_prob
        # Win: profit = shares * (exit_prob - entry_prob)
        # Loss: profit = shares * (exit_prob - entry_prob) [negative]
        bet_pct = BET_SIZES.get((asset, tf), 0.10)
        bet_amount = 150.0 * bet_pct  # Using $150 wallet
        shares = bet_amount / entry_prob

        if outcome == "WIN":
            pnl = shares * (exit_prob - entry_prob)
        elif outcome == "LOSS":
            pnl = shares * (exit_prob - entry_prob)
        else:
            pnl = 0

        trades.append({
            "slug": slug,
            "asset": asset,
            "timeframe": tf,
            "entry_prob": entry_prob,
            "entry_ts": entry_ts,
            "exit_prob": exit_prob,
            "exit_reason": exit_reason,
            "outcome": outcome,
            "pnl": pnl,
            "bet_amount": bet_amount,
            "max_prob": max_prob_after,
            "min_prob": min_prob_after,
        })

        if track_concurrent:
            active_positions.append((slug, entry_ts))

    return trades


def print_report(trades, label=""):
    """Print a detailed report of backtested trades."""
    if label:
        print(f"\n{'='*80}")
        print(f"  {label}")
        print(f"{'='*80}")

    # Filter out unknowns
    known_trades = [t for t in trades if t["outcome"] != "UNKNOWN"]
    unknown_count = len(trades) - len(known_trades)

    if not known_trades:
        print("  No trades found.")
        return

    # Overall stats
    wins = [t for t in known_trades if t["outcome"] == "WIN"]
    losses = [t for t in known_trades if t["outcome"] == "LOSS"]
    total_pnl = sum(t["pnl"] for t in known_trades)

    print(f"\n  Total trades: {len(known_trades)} ({unknown_count} skipped - no resolution)")
    print(f"  Wins: {len(wins)}, Losses: {len(losses)}")
    print(f"  Overall WR: {len(wins)/len(known_trades)*100:.1f}%")
    print(f"  Total PnL: ${total_pnl:.2f}")

    if wins:
        avg_win = sum(t["pnl"] for t in wins) / len(wins)
        print(f"  Avg profit per win: ${avg_win:.2f}")
    if losses:
        avg_loss = sum(t["pnl"] for t in losses) / len(losses)
        print(f"  Avg loss per loss: ${avg_loss:.2f}")

    # Per-market breakdown
    print(f"\n  {'Market':<12} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'Avg Win':>9} {'Avg Loss':>10} {'Net PnL':>10}")
    print(f"  {'-'*72}")

    market_keys = sorted(set((t["asset"], t["timeframe"]) for t in known_trades),
                         key=lambda x: (x[1], x[0]))

    for asset, tf in market_keys:
        mt = [t for t in known_trades if t["asset"] == asset and t["timeframe"] == tf]
        mw = [t for t in mt if t["outcome"] == "WIN"]
        ml = [t for t in mt if t["outcome"] == "LOSS"]
        mpnl = sum(t["pnl"] for t in mt)
        wr = len(mw) / len(mt) * 100 if mt else 0
        avg_w = sum(t["pnl"] for t in mw) / len(mw) if mw else 0
        avg_l = sum(t["pnl"] for t in ml) / len(ml) if ml else 0

        print(f"  {asset+' '+tf:<12} {len(mt):>7} {len(mw):>6} {len(ml):>7} {wr:>6.1f}% ${avg_w:>7.2f} ${avg_l:>9.2f} ${mpnl:>9.2f}")

    # Loss details
    if losses:
        print(f"\n  Loss details (exit reason breakdown):")
        reasons = defaultdict(int)
        for t in losses:
            reasons[t["exit_reason"]] += 1
        for reason, count in sorted(reasons.items()):
            print(f"    {reason}: {count}")


def run_sensitivity(markets, market_info, resolutions):
    """Run sensitivity analysis on entry threshold and stop-loss."""

    print("\n" + "="*80)
    print("  SENSITIVITY ANALYSIS: ENTRY THRESHOLD")
    print("="*80)

    entry_thresholds = [0.90, 0.92, 0.93, 0.94, 0.95]

    print(f"\n  {'Threshold':>10} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>10}")
    print(f"  {'-'*50}")

    for et in entry_thresholds:
        trades = simulate_trades(markets, market_info, resolutions,
                                 entry_threshold=et, stop_loss=0.85,
                                 track_concurrent=False)
        known = [t for t in trades if t["outcome"] != "UNKNOWN"]
        wins = len([t for t in known if t["outcome"] == "WIN"])
        losses = len([t for t in known if t["outcome"] == "LOSS"])
        pnl = sum(t["pnl"] for t in known)
        wr = wins / len(known) * 100 if known else 0
        print(f"  {et*100:>9.0f}% {len(known):>7} {wins:>6} {losses:>7} {wr:>6.1f}% ${pnl:>9.2f}")

    # Per-market sensitivity for entry threshold
    print(f"\n  Per-market breakdown by entry threshold:")
    for asset, tf in sorted(TRADED_MARKETS, key=lambda x: (x[1], x[0])):
        print(f"\n  --- {asset} {tf} ---")
        print(f"  {'Threshold':>10} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>10}")
        print(f"  {'-'*50}")
        for et in entry_thresholds:
            trades = simulate_trades(markets, market_info, resolutions,
                                     entry_threshold=et, stop_loss=0.85,
                                     track_concurrent=False)
            known = [t for t in trades if t["outcome"] != "UNKNOWN"
                     and t["asset"] == asset and t["timeframe"] == tf]
            wins = len([t for t in known if t["outcome"] == "WIN"])
            losses = len([t for t in known if t["outcome"] == "LOSS"])
            pnl = sum(t["pnl"] for t in known)
            wr = wins / len(known) * 100 if known else 0
            print(f"  {et*100:>9.0f}% {len(known):>7} {wins:>6} {losses:>7} {wr:>6.1f}% ${pnl:>9.2f}")

    print("\n" + "="*80)
    print("  SENSITIVITY ANALYSIS: STOP-LOSS")
    print("="*80)

    stop_losses = [0.80, 0.82, 0.85, 0.88, 0.90]

    print(f"\n  {'Stop-Loss':>10} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>10}")
    print(f"  {'-'*50}")

    for sl in stop_losses:
        trades = simulate_trades(markets, market_info, resolutions,
                                 entry_threshold=0.90, stop_loss=sl,
                                 track_concurrent=False)
        known = [t for t in trades if t["outcome"] != "UNKNOWN"]
        wins = len([t for t in known if t["outcome"] == "WIN"])
        losses = len([t for t in known if t["outcome"] == "LOSS"])
        pnl = sum(t["pnl"] for t in known)
        wr = wins / len(known) * 100 if known else 0
        print(f"  {sl*100:>9.0f}% {len(known):>7} {wins:>6} {losses:>7} {wr:>6.1f}% ${pnl:>9.2f}")

    # Per-market sensitivity for stop-loss
    print(f"\n  Per-market breakdown by stop-loss:")
    for asset, tf in sorted(TRADED_MARKETS, key=lambda x: (x[1], x[0])):
        print(f"\n  --- {asset} {tf} ---")
        print(f"  {'Stop-Loss':>10} {'Trades':>7} {'Wins':>6} {'Losses':>7} {'WR':>7} {'PnL':>10}")
        print(f"  {'-'*50}")
        for sl in stop_losses:
            trades = simulate_trades(markets, market_info, resolutions,
                                     entry_threshold=0.90, stop_loss=sl,
                                     track_concurrent=False)
            known = [t for t in trades if t["outcome"] != "UNKNOWN"
                     and t["asset"] == asset and t["timeframe"] == tf]
            wins = len([t for t in known if t["outcome"] == "WIN"])
            losses = len([t for t in known if t["outcome"] == "LOSS"])
            pnl = sum(t["pnl"] for t in known)
            wr = wins / len(known) * 100 if known else 0
            print(f"  {sl*100:>9.0f}% {len(known):>7} {wins:>6} {losses:>7} {wr:>6.1f}% ${pnl:>9.2f}")

    # Combined grid: best combo per market
    print("\n" + "="*80)
    print("  OPTIMAL PARAMETERS PER MARKET (Entry x Stop-Loss grid)")
    print("="*80)

    for asset, tf in sorted(TRADED_MARKETS, key=lambda x: (x[1], x[0])):
        best_pnl = -999999
        best_params = None

        print(f"\n  --- {asset} {tf} ---")
        header = f"  {'':>10}"
        for sl in stop_losses:
            header += f"  SL={sl*100:.0f}%"
        print(header)

        for et in entry_thresholds:
            row_str = f"  ET={et*100:.0f}%"
            for sl in stop_losses:
                trades = simulate_trades(markets, market_info, resolutions,
                                         entry_threshold=et, stop_loss=sl,
                                         track_concurrent=False)
                known = [t for t in trades if t["outcome"] != "UNKNOWN"
                         and t["asset"] == asset and t["timeframe"] == tf]
                pnl = sum(t["pnl"] for t in known)
                wr = len([t for t in known if t["outcome"] == "WIN"]) / len(known) * 100 if known else 0
                n = len(known)
                row_str += f"  {pnl:>+6.1f}"

                if pnl > best_pnl:
                    best_pnl = pnl
                    best_params = (et, sl, wr, n)
            print(row_str)

        if best_params:
            et, sl, wr, n = best_params
            print(f"  BEST: entry={et*100:.0f}%, stop={sl*100:.0f}%, WR={wr:.1f}%, n={n}, PnL=${best_pnl:.2f}")


def main():
    print("Loading resolutions...")
    resolutions = load_resolutions()
    print(f"  Loaded {len(resolutions)} up_down 15m/1h resolutions")

    print("Loading snapshots...")
    markets, market_info = load_snapshots()
    print(f"  Loaded {len(markets)} unique market slugs")
    print(f"  Total snapshots: {sum(len(v) for v in markets.values())}")

    # Show market distribution
    dist = defaultdict(int)
    for slug, (asset, tf) in market_info.items():
        dist[(asset, tf)] += 1
    print("\n  Markets per type:")
    for (asset, tf), count in sorted(dist.items()):
        print(f"    {asset} {tf}: {count} unique markets")

    # Check resolution coverage
    resolved_count = sum(1 for slug in markets if slug in resolutions)
    print(f"\n  Markets with resolutions: {resolved_count}/{len(markets)}")

    # Run baseline backtest (no concurrent limit for full picture)
    print("\n" + "="*80)
    print("  BASELINE BACKTEST (no concurrent limit)")
    print("="*80)
    trades_no_limit = simulate_trades(markets, market_info, resolutions,
                                       track_concurrent=False)
    print_report(trades_no_limit, "Entry=90%, Stop=85%, Skip>=99%")

    # Run with concurrent limit
    trades_with_limit = simulate_trades(markets, market_info, resolutions,
                                         track_concurrent=True)
    print_report(trades_with_limit, "Same + MAX_CONCURRENT=6")

    # Sensitivity analysis
    run_sensitivity(markets, market_info, resolutions)

    # Detailed loss analysis
    print("\n" + "="*80)
    print("  DETAILED LOSS ANALYSIS")
    print("="*80)
    losses = [t for t in trades_no_limit if t["outcome"] == "LOSS"]
    losses.sort(key=lambda t: t["pnl"])
    print(f"\n  {'Slug':<50} {'Asset':>5} {'TF':>4} {'Entry':>6} {'Exit':>6} {'MinP':>6} {'Reason':<16} {'PnL':>9}")
    print(f"  {'-'*105}")
    for t in losses:
        print(f"  {t['slug']:<50} {t['asset']:>5} {t['timeframe']:>4} {t['entry_prob']:>5.1%} {t['exit_prob']:>5.1%} {t['min_prob']:>5.1%} {t['exit_reason']:<16} ${t['pnl']:>8.2f}")

    # Final recommendations
    print("\n" + "="*80)
    print("  RECOMMENDATIONS")
    print("="*80)
    print("\n  (See OPTIMAL PARAMETERS grid above for per-market recommendations)")
    print("  Note: Results based on ~6 days of data (Mar 6-12). Small sample sizes")
    print("  for 1h markets. Verify with more data before making parameter changes.")


if __name__ == "__main__":
    main()
