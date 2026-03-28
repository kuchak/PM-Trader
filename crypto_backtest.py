#!/usr/bin/env python3
"""
Crypto Backtest — Phase 2 Analysis
Reads crypto_snapshots.csv + crypto_resolutions.csv and simulates trading strategies.

Usage:
    python3 crypto_backtest.py               # full report
    python3 crypto_backtest.py --csv         # also write results to data/backtest_results.csv
    python3 crypto_backtest.py --focus 15m   # filter to one timeframe
"""

import csv
import sys
import os
from collections import defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
SNAPSHOTS_CSV   = os.path.join(DATA, "crypto_snapshots.csv")
RESOLUTIONS_CSV = os.path.join(DATA, "crypto_resolutions.csv")
RESULTS_CSV     = os.path.join(DATA, "backtest_results.csv")

# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------
WRITE_CSV   = "--csv"   in sys.argv
FOCUS_TF    = None
if "--focus" in sys.argv:
    idx = sys.argv.index("--focus")
    if idx + 1 < len(sys.argv):
        FOCUS_TF = sys.argv[idx + 1]

# ---------------------------------------------------------------------------
# Strategy parameters to sweep
# ---------------------------------------------------------------------------
ENTRY_THRESHOLDS   = [0.70, 0.75, 0.80, 0.85, 0.90]
MIN_MINS_REMAINING = [0, 2, 5, 10]        # must have at least N minutes left at entry
MAX_MINS_REMAINING = 240                   # don't enter with >4h left (stale pre-market)
STOP_LOSS          = 0.40                  # exit if prob drops here (loss)
TARGET_EXIT        = 0.99                  # exit if prob reaches here (win)
BET_SIZE           = 100.0                 # $ per trade (for P&L simulation)

# ---------------------------------------------------------------------------
# Load resolutions
# ---------------------------------------------------------------------------
def load_resolutions():
    res_updown = {}    # event_slug → "Up" | "Down"
    res_daily  = {}    # (event_slug, threshold_int) → "YES" | "NO"
    try:
        with open(RESOLUTIONS_CSV) as f:
            for row in csv.DictReader(f):
                mt   = row.get("market_type", "")
                slug = row.get("event_slug", "")
                if mt == "up_down":
                    res_updown[slug] = row["winning_outcome"]
                elif mt == "daily_above":
                    try:
                        t = int(row["threshold_price"])
                        res_daily[(slug, t)] = row["winning_outcome"]
                    except (ValueError, KeyError):
                        pass
    except FileNotFoundError:
        print(f"ERROR: {RESOLUTIONS_CSV} not found")
        sys.exit(1)
    return res_updown, res_daily

# ---------------------------------------------------------------------------
# Load snapshots (v2 only — 13 columns)
# ---------------------------------------------------------------------------
def load_snapshots(res_updown, res_daily):
    """
    Returns dict:  event_slug → sorted list of snapshot dicts.
    Each dict has: ts, asset, tf, market_type, threshold, prob, mins, liq, resolution
    """
    events = defaultdict(list)
    skipped = 0

    try:
        with open(SNAPSHOTS_CSV) as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            for row in reader:
                if len(row) != 13:
                    skipped += 1
                    continue
                (ts, event_slug, series_slug, asset, tf, market_type,
                 threshold_price, outcome, implied_prob,
                 liquidity, volume_24h, minutes_to_expiry, price_approx) = row

                if FOCUS_TF and tf != FOCUS_TF:
                    continue

                try:
                    prob = float(implied_prob)
                    mins = float(minutes_to_expiry)
                    liq  = float(liquidity) if liquidity else 0.0
                except ValueError:
                    continue

                # Attach resolution
                if market_type == "up_down":
                    resolution = res_updown.get(event_slug)
                elif market_type == "daily_above":
                    try:
                        t = int(threshold_price) if threshold_price else None
                    except ValueError:
                        t = None
                    resolution = res_daily.get((event_slug, t)) if t else None
                    threshold_price = t
                else:
                    continue

                if resolution is None:
                    continue  # unresolved — skip

                events[event_slug].append({
                    "ts":          ts,
                    "asset":       asset,
                    "tf":          tf,
                    "market_type": market_type,
                    "threshold":   threshold_price,
                    "prob":        prob,
                    "mins":        mins,
                    "liq":         liq,
                    "resolution":  resolution,
                })
    except FileNotFoundError:
        print(f"ERROR: {SNAPSHOTS_CSV} not found")
        sys.exit(1)

    # Sort each event's snapshots by timestamp
    for slug in events:
        events[slug].sort(key=lambda x: x["ts"])

    return dict(events), skipped

# ---------------------------------------------------------------------------
# Simulate one strategy: entry_thresh × min_mins
# ---------------------------------------------------------------------------
def simulate(events, entry_thresh, min_mins):
    """
    For each event, find the FIRST snapshot that meets entry criteria.
    Then walk forward through remaining snapshots to determine exit:
      - Stop loss:  prob drops to STOP_LOSS  → LOSS
      - Target:     prob reaches TARGET_EXIT  → WIN (early exit)
      - Resolution: use final outcome         → WIN or LOSS
    """
    trades = []

    for slug, snaps in events.items():
        if not snaps:
            continue

        market_type = snaps[0]["market_type"]
        asset       = snaps[0]["asset"]
        tf          = snaps[0]["tf"]
        resolution  = snaps[0]["resolution"]  # same for all snaps of this event

        # Determine win condition based on market type
        # up_down: we always bet "Up" (the monitored outcome)
        # daily_above: we always bet "Yes"
        if market_type == "up_down":
            win_resolution = "Up"
        else:
            win_resolution = "YES"

        # Find entry: first snap at or above threshold with enough time left
        entry_snap = None
        entry_idx  = None
        for i, s in enumerate(snaps):
            if (s["prob"] >= entry_thresh
                    and s["mins"] >= min_mins
                    and s["mins"] <= MAX_MINS_REMAINING):
                entry_snap = s
                entry_idx  = i
                break

        if entry_snap is None:
            continue

        entry_prob = entry_snap["prob"]
        entry_mins = entry_snap["mins"]

        # Walk forward to find exit
        exit_type = "resolution"   # default: hold to resolution
        exit_prob = entry_prob
        hold_mins = entry_mins

        for s in snaps[entry_idx + 1:]:
            # Stop loss
            if s["prob"] <= STOP_LOSS:
                exit_type = "stop_loss"
                exit_prob = s["prob"]
                hold_mins = entry_mins - s["mins"]
                break
            # Target
            if s["prob"] >= TARGET_EXIT:
                exit_type = "target"
                exit_prob = s["prob"]
                hold_mins = entry_mins - s["mins"]
                break

        if exit_type == "resolution":
            hold_mins = entry_mins  # held to end

        # Determine win/loss
        if exit_type == "stop_loss":
            won = False
        elif exit_type == "target":
            won = True  # locked in profit early
        else:
            won = (resolution == win_resolution)

        # P&L: simple fixed-odds on market price
        # Profit on win = BET_SIZE * (1 - entry_prob) / entry_prob
        # Loss on loss  = -BET_SIZE
        if won:
            pnl = BET_SIZE * (1.0 - entry_prob) / entry_prob
        else:
            pnl = -BET_SIZE

        trades.append({
            "slug":        slug,
            "asset":       asset,
            "tf":          tf,
            "market_type": market_type,
            "entry_prob":  entry_prob,
            "entry_mins":  entry_mins,
            "exit_type":   exit_type,
            "exit_prob":   exit_prob,
            "hold_mins":   hold_mins,
            "won":         won,
            "pnl":         pnl,
            "resolution":  resolution,
        })

    return trades

# ---------------------------------------------------------------------------
# Summarise a trade list
# ---------------------------------------------------------------------------
def summarise(trades, label=""):
    if not trades:
        return None

    n        = len(trades)
    wins     = sum(1 for t in trades if t["won"])
    losses   = n - wins
    win_rate = wins / n
    net_pnl  = sum(t["pnl"] for t in trades)
    avg_pnl  = net_pnl / n
    avg_ep   = sum(t["entry_prob"] for t in trades) / n
    avg_mins = sum(t["entry_mins"] for t in trades) / n

    stop_exits   = sum(1 for t in trades if t["exit_type"] == "stop_loss")
    target_exits = sum(1 for t in trades if t["exit_type"] == "target")
    res_exits    = sum(1 for t in trades if t["exit_type"] == "resolution")

    pnls = sorted(t["pnl"] for t in trades)
    # Max drawdown: worst consecutive losing streak P&L
    max_streak = 0
    streak_pnl = 0.0
    worst_streak_pnl = 0.0
    for t in trades:
        if not t["won"]:
            max_streak += 1
            streak_pnl -= BET_SIZE
            worst_streak_pnl = min(worst_streak_pnl, streak_pnl)
        else:
            max_streak = 0
            streak_pnl = 0.0

    return {
        "label":        label,
        "n":            n,
        "wins":         wins,
        "losses":       losses,
        "win_rate":     win_rate,
        "net_pnl":      net_pnl,
        "avg_pnl":      avg_pnl,
        "avg_ep":       avg_ep,
        "avg_mins":     avg_mins,
        "stop_exits":   stop_exits,
        "target_exits": target_exits,
        "res_exits":    res_exits,
        "worst_streak_pnl": worst_streak_pnl,
    }

# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------
SEP = "=" * 72

def hdr(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)

def row_fmt(r):
    return (
        f"  {r['label']:<28}  n={r['n']:>4}  "
        f"win={r['win_rate']:>5.1%}  "
        f"net=${r['net_pnl']:>+8.2f}  "
        f"avg=${r['avg_pnl']:>+6.2f}  "
        f"stops={r['stop_exits']:>3}  "
        f"avgE={r['avg_ep']:>5.1%}  "
        f"avgMins={r['avg_mins']:>5.1f}"
    )

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(SEP)
    print("  Crypto Backtest — Phase 2")
    print(f"  Snapshots: {SNAPSHOTS_CSV}")
    print(f"  Bet size:  ${BET_SIZE:.0f} per trade (fixed)")
    print(f"  Stop loss: {STOP_LOSS:.0%}  |  Target: {TARGET_EXIT:.0%}")
    if FOCUS_TF:
        print(f"  Filter:    timeframe = {FOCUS_TF}")
    print(SEP)

    # Load data
    print("\nLoading data...")
    res_updown, res_daily = load_resolutions()
    events, skipped = load_snapshots(res_updown, res_daily)
    total_snaps = sum(len(v) for v in events.values())
    print(f"  {len(events):,} resolved events  |  {total_snaps:,} snapshots  |  {skipped} v1 rows skipped")
    print(f"  Resolutions: {len(res_updown):,} up/down + {len(res_daily):,} daily-above thresholds")

    # ---------------------------------------------------------------------------
    # SECTION 1: Strategy grid — all combos of entry_thresh × min_mins
    # ---------------------------------------------------------------------------
    hdr("SECTION 1: Full Strategy Grid (entry_threshold × min_mins_remaining)")
    print(f"\n  {'Strategy':<28}  {'n':>5}  {'WinRate':>7}  {'NetPnL':>9}  "
          f"{'AvgPnL':>7}  {'Stops':>5}  {'AvgEntryP':>9}  {'AvgMins':>7}")
    print("  " + "-" * 70)

    grid_results = []
    for et in ENTRY_THRESHOLDS:
        for mm in MIN_MINS_REMAINING:
            trades = simulate(events, et, mm)
            s = summarise(trades, f"e>={et:.0%} min>={mm:>3}m")
            if s and s["n"] >= 5:
                grid_results.append((et, mm, s))
                print(row_fmt(s))
        print()

    # ---------------------------------------------------------------------------
    # SECTION 2: Best configurations (≥20 trades, sorted by avg P&L)
    # ---------------------------------------------------------------------------
    hdr("SECTION 2: Top Configurations (≥20 trades, sorted by avg P&L per trade)")
    qualified = [(et, mm, s) for et, mm, s in grid_results if s["n"] >= 20]
    qualified.sort(key=lambda x: x[2]["avg_pnl"], reverse=True)
    for et, mm, s in qualified[:10]:
        print(row_fmt(s))

    # ---------------------------------------------------------------------------
    # SECTION 3: Best entry = 80% with ≥10 min — breakdown by asset/timeframe
    # ---------------------------------------------------------------------------
    hdr("SECTION 3: Breakdown by Asset & Timeframe  (entry≥80%, mins≥10)")
    trades_80_10 = simulate(events, 0.80, 10)
    by_group = defaultdict(list)
    for t in trades_80_10:
        by_group[(t["asset"], t["tf"])].append(t)

    print(f"\n  {'Group':<14}  {'n':>4}  {'WinRate':>7}  {'NetPnL':>9}  "
          f"{'AvgPnL':>7}  {'Stops':>5}  {'AvgEntryP':>9}  {'AvgMins':>7}")
    print("  " + "-" * 65)
    group_sums = []
    for key in sorted(by_group.keys()):
        s = summarise(by_group[key], f"{key[0]} {key[1]}")
        if s and s["n"] >= 3:
            group_sums.append(s)
            print(row_fmt(s))
    # Total
    s_all = summarise(trades_80_10, "ALL COMBINED")
    if s_all:
        print("  " + "-" * 65)
        print(row_fmt(s_all))

    # ---------------------------------------------------------------------------
    # SECTION 4: Exit type breakdown — how often does stop/target/resolution fire?
    # ---------------------------------------------------------------------------
    hdr("SECTION 4: Exit Type Breakdown  (entry≥80%, mins≥10)")
    if trades_80_10:
        n     = len(trades_80_10)
        stops  = sum(1 for t in trades_80_10 if t["exit_type"] == "stop_loss")
        targs  = sum(1 for t in trades_80_10 if t["exit_type"] == "target")
        ress   = sum(1 for t in trades_80_10 if t["exit_type"] == "resolution")
        print(f"\n  Held to resolution : {ress:>4}  ({ress/n:.1%})")
        print(f"  Hit target (99%)   : {targs:>4}  ({targs/n:.1%})")
        print(f"  Hit stop loss (40%): {stops:>4}  ({stops/n:.1%})")
        won_by_exit = defaultdict(lambda: [0, 0])  # exit_type -> [wins, total]
        for t in trades_80_10:
            won_by_exit[t["exit_type"]][1] += 1
            if t["won"]:
                won_by_exit[t["exit_type"]][0] += 1
        print(f"\n  Win rate by exit type:")
        for et, (w, tot) in won_by_exit.items():
            print(f"    {et:<20}: {w}/{tot}  ({w/tot:.1%})")

    # ---------------------------------------------------------------------------
    # SECTION 5: Minimum mins sweep — isolate time-remaining effect
    # ---------------------------------------------------------------------------
    hdr("SECTION 5: Effect of Min Time Remaining  (entry≥80%, sweep min_mins)")
    trades_by_mins = []
    for mm in [0, 1, 2, 3, 5, 7, 10, 15, 20, 30]:
        trades = simulate(events, 0.80, mm)
        s = summarise(trades, f"mins >= {mm:>2}")
        if s:
            trades_by_mins.append(s)
            print(row_fmt(s))

    # ---------------------------------------------------------------------------
    # SECTION 6: Minimum mins sweep — at 85% entry
    # ---------------------------------------------------------------------------
    hdr("SECTION 6: Effect of Min Time Remaining  (entry≥85%, sweep min_mins)")
    for mm in [0, 1, 2, 3, 5, 7, 10, 15, 20, 30]:
        trades = simulate(events, 0.85, mm)
        s = summarise(trades, f"mins >= {mm:>2}")
        if s:
            print(row_fmt(s))

    # ---------------------------------------------------------------------------
    # SECTION 7: Daily Above separately
    # ---------------------------------------------------------------------------
    hdr("SECTION 7: Daily Above Markets  (entry threshold sweep)")
    daily_events = {
        k: v for k, v in events.items()
        if v and v[0]["market_type"] == "daily_above"
    }
    print(f"\n  {len(daily_events)} resolved daily-above events (threshold-markets)")
    for et in [0.80, 0.85, 0.90, 0.93, 0.95, 0.97]:
        for mm in [30, 60, 120]:
            trades = simulate(daily_events, et, mm)
            s = summarise(trades, f"e>={et:.0%} min>={mm}m")
            if s and s["n"] >= 3:
                print(row_fmt(s))

    # ---------------------------------------------------------------------------
    # SECTION 8: Worst-case losing streak and drawdown
    # ---------------------------------------------------------------------------
    hdr("SECTION 8: Risk / Drawdown Analysis")
    best_strats = [
        ("e>=80% min>=10m",  0.80, 10),
        ("e>=85% min>=10m",  0.85, 10),
        ("e>=80% min>=5m",   0.80,  5),
        ("e>=85% min>=5m",   0.85,  5),
        ("e>=90% min>=0m",   0.90,  0),
    ]
    print(f"\n  {'Strategy':<22}  {'n':>4}  {'WinRate':>7}  {'NetPnL':>9}  "
          f"{'WorstStreak$':>13}  {'MaxLossRun':>11}")
    print("  " + "-" * 72)
    for label, et, mm in best_strats:
        trades = simulate(events, et, mm)
        if not trades:
            continue
        n    = len(trades)
        wins = sum(1 for t in trades if t["won"])
        wr   = wins / n
        net  = sum(t["pnl"] for t in trades)

        # Max consecutive losses
        max_consec = cur_consec = 0
        max_streak_pnl = cur_streak_pnl = 0.0
        for t in trades:
            if not t["won"]:
                cur_consec += 1
                cur_streak_pnl -= BET_SIZE
                max_consec = max(max_consec, cur_consec)
                max_streak_pnl = min(max_streak_pnl, cur_streak_pnl)
            else:
                cur_consec = 0
                cur_streak_pnl = 0.0

        print(f"  {label:<22}  {n:>4}  {wr:>6.1%}  ${net:>+8.2f}  "
              f"  ${max_streak_pnl:>+10.2f}  {max_consec:>6} in a row")

    # ---------------------------------------------------------------------------
    # SECTION 9: Recommended parameters
    # ---------------------------------------------------------------------------
    hdr("SECTION 9: Parameter Recommendations")

    # Find the best single strategy with ≥30 trades and ≥90% win rate
    best = None
    for et, mm, s in grid_results:
        if s["n"] >= 30 and s["win_rate"] >= 0.90 and s["avg_pnl"] > 0:
            if best is None or s["avg_pnl"] > best[2]["avg_pnl"]:
                best = (et, mm, s)

    if best:
        et, mm, s = best
        print(f"""
  RECOMMENDED ENTRY PARAMETERS (based on {s['n']} qualifying trades):

    entry_threshold   : {et:.0%}    (only enter when prob >= this)
    min_mins_remaining: {mm}       (must have this many minutes left)
    max_mins_remaining: {MAX_MINS_REMAINING}     (don't enter pre-market noise)
    stop_loss         : {STOP_LOSS:.0%}    (exit if prob drops here)
    target_exit       : {TARGET_EXIT:.0%}    (exit early if locked in)

  Simulated performance (${BET_SIZE:.0f}/trade):
    Win rate : {s['win_rate']:.1%}
    Net P&L  : ${s['net_pnl']:+.2f}  over {s['n']} trades
    Avg P&L  : ${s['avg_pnl']:+.2f} per trade
    Stop hits: {s['stop_exits']}
""")
    else:
        print("\n  No strategy met ≥30 trades + ≥90% win rate — gather more data.")

    # ---------------------------------------------------------------------------
    # Write CSV results
    # ---------------------------------------------------------------------------
    if WRITE_CSV and grid_results:
        rows = []
        for et, mm, s in grid_results:
            rows.append({
                "entry_threshold": et,
                "min_mins":        mm,
                **s,
            })
        with open(RESULTS_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n  Results written to {RESULTS_CSV}")

    print(f"\n{SEP}")
    print("  Done.")
    print(SEP)

if __name__ == "__main__":
    main()
