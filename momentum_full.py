#!/usr/bin/env python3
"""Full momentum analysis: every entry level from 50-95%, every exit delta."""
import csv
from collections import defaultdict

SNAPSHOTS = "data/crypto_snapshots.csv"
TRADE_ASSETS = {"BTC", "ETH", "XRP"}
TRADE_TFS = {"15m", "1h"}

LEVELS = list(range(50, 100))  # 50% through 99%, 1% increments

def main():
    # Build trajectories
    trajectories = defaultdict(list)
    with open(SNAPSHOTS) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 12:
                continue
            if row[4] not in TRADE_TFS:
                continue
            asset = row[3].upper()
            if asset not in TRADE_ASSETS:
                continue
            try:
                prob = float(row[8])
                minutes = float(row[11]) if row[11] else None
            except (ValueError, IndexError):
                continue
            trajectories[row[1]].append({
                "ts": row[0], "outcome": row[7], "prob": prob,
                "minutes": minutes, "asset": asset, "tf": row[4]
            })

    # Build side trajectories
    sides = []
    for slug, points in trajectories.items():
        for side in ("Up", "Down"):
            pts = sorted([p for p in points if p["outcome"] == side], key=lambda x: x["ts"])
            if len(pts) >= 2:
                sides.append({"slug": slug, "side": side, "points": pts,
                              "asset": pts[0]["asset"], "tf": pts[0]["tf"]})

    print(f"Loaded {len(sides)} side-trajectories from {len(trajectories)} markets")

    for tf in ["1h", "15m"]:
        tf_sides = [s for s in sides if s["tf"] == tf]
        print(f"\n{'='*100}")
        print(f"  {tf.upper()} MARKETS — {len(tf_sides)} trajectories")
        print(f"{'='*100}")

        # For each entry level, compute: how many trajectories cross it,
        # and for those that do, what's the max prob reached after
        # Then show hit rates to various exit levels

        # Precompute: for each side, the sequence of probs
        prob_sequences = []
        for s in tf_sides:
            probs = [p["prob"] for p in s["points"]]
            prob_sequences.append(probs)

        # Build matrix: hit_rate[entry][exit] = (reached, total)
        hit_rate = {}
        for entry_pct in range(50, 96):
            entry = entry_pct / 100.0
            reached_counts = {}
            total = 0
            for probs in prob_sequences:
                # Find first index where prob >= entry
                first = None
                for idx, p in enumerate(probs):
                    if p >= entry:
                        first = idx
                        break
                if first is None:
                    continue
                total += 1
                max_after = max(probs[first:])
                for exit_pct in range(entry_pct + 1, 100):
                    exit_val = exit_pct / 100.0
                    if exit_val not in reached_counts:
                        reached_counts[exit_val] = 0
                    if max_after >= exit_val:
                        reached_counts[exit_val] += 1

            hit_rate[entry] = {"total": total, "reached": reached_counts}

        # Print condensed table: every 5% entry, show exit columns
        # First: the full low-range analysis the user wants
        print(f"\n  ENTRY 50-75% → EXIT targets (hit rate %)")
        print(f"  {'Entry':>6} {'n':>4}", end="")
        for ex in range(55, 100, 5):
            print(f"  →{ex}%", end="")
        print()
        print(f"  {'-'*120}")

        for entry_pct in range(50, 76):
            entry = entry_pct / 100.0
            data = hit_rate.get(entry)
            if not data or data["total"] < 10:
                continue
            print(f"  {entry_pct:4d}% {data['total']:4d}", end="")
            for ex in range(55, 100, 5):
                exit_val = ex / 100.0
                if exit_val <= entry:
                    print(f"   -- ", end="")
                else:
                    r = data["reached"].get(exit_val, 0)
                    pct = r / data["total"] * 100
                    print(f" {pct:4.0f}%", end="")
            print()

        # Mid-range: 75-95%
        print(f"\n  ENTRY 75-95% → EXIT targets (hit rate %)")
        print(f"  {'Entry':>6} {'n':>4}", end="")
        for ex in range(78, 100, 2):
            print(f"  →{ex}%", end="")
        print()
        print(f"  {'-'*120}")

        for entry_pct in range(75, 96):
            entry = entry_pct / 100.0
            data = hit_rate.get(entry)
            if not data or data["total"] < 10:
                continue
            print(f"  {entry_pct:4d}% {data['total']:4d}", end="")
            for ex in range(78, 100, 2):
                exit_val = ex / 100.0
                if exit_val <= entry:
                    print(f"   -- ", end="")
                else:
                    r = data["reached"].get(exit_val, 0)
                    pct = r / data["total"] * 100
                    print(f" {pct:4.0f}%", end="")
            print()

        # Best combos: expected value analysis
        # Buy at entry, sell at exit. If exit not reached, assume loss.
        # EV = hit_rate * (exit - entry) / entry - (1 - hit_rate) * 1.0
        print(f"\n  TOP 30 ENTRY→EXIT COMBOS BY EXPECTED VALUE (miss = total loss)")
        print(f"  {'Entry':>6} {'Exit':>6} {'Hit%':>6} {'Gain':>7} {'EV/trade':>9} {'n':>5}")
        print(f"  {'-'*50}")

        combos = []
        for entry_pct in range(50, 96):
            entry = entry_pct / 100.0
            data = hit_rate.get(entry)
            if not data or data["total"] < 15:
                continue
            for exit_pct in range(entry_pct + 2, min(entry_pct + 25, 100)):
                exit_val = exit_pct / 100.0
                r = data["reached"].get(exit_val, 0)
                hr = r / data["total"]
                gain = (exit_val - entry) / entry
                ev = hr * gain - (1 - hr) * 1.0
                combos.append({
                    "entry": entry_pct, "exit": exit_pct,
                    "hr": hr, "gain": gain, "ev": ev, "n": data["total"]
                })

        for c in sorted(combos, key=lambda x: -x["ev"])[:30]:
            print(f"  {c['entry']:4d}% {c['exit']:4d}% {c['hr']*100:5.1f}% "
                  f"{c['gain']*100:6.1f}% {c['ev']*100:8.1f}%  {c['n']:5d}")

        # Same but with miss = hold to resolution (94% WR proxy for high entries, lower for low)
        print(f"\n  TOP 30 ENTRY→EXIT COMBOS BY EV (miss = hold to resolution, ~94% WR)")
        print(f"  {'Entry':>6} {'Exit':>6} {'Hit%':>6} {'Gain':>7} {'EV/trade':>9} {'n':>5}")
        print(f"  {'-'*50}")

        HOLD_WR = 0.94
        combos_a = []
        for c in combos:
            entry = c["entry"] / 100.0
            exit_val = c["exit"] / 100.0
            miss_rate = 1 - c["hr"]
            # If we miss target, we hold to resolution
            # Win at resolution: profit = (1/entry - 1), lose: -1
            hold_ev = HOLD_WR * (1.0 / entry - 1) - (1 - HOLD_WR)
            ev_a = c["hr"] * c["gain"] + miss_rate * hold_ev
            combos_a.append({**c, "ev_a": ev_a})

        for c in sorted(combos_a, key=lambda x: -x["ev_a"])[:30]:
            print(f"  {c['entry']:4d}% {c['exit']:4d}% {c['hr']*100:5.1f}% "
                  f"{c['gain']*100:6.1f}% {c['ev_a']*100:8.1f}%  {c['n']:5d}")

    # Per-asset breakdown for the best combos
    print(f"\n{'='*100}")
    print(f"  PER-ASSET BREAKDOWN (1h markets)")
    print(f"{'='*100}")

    for asset in sorted(TRADE_ASSETS):
        asset_sides = [s for s in sides if s["tf"] == "1h" and s["asset"] == asset]
        print(f"\n  {asset} — {len(asset_sides)} trajectories")
        prob_sequences = [[p["prob"] for p in s["points"]] for s in asset_sides]

        # Show key combos
        key_entries = [55, 60, 65, 70, 75, 80, 85, 90]
        for entry_pct in key_entries:
            entry = entry_pct / 100.0
            total = 0
            reached = defaultdict(int)
            for probs in prob_sequences:
                first = None
                for idx, p in enumerate(probs):
                    if p >= entry:
                        first = idx
                        break
                if first is None:
                    continue
                total += 1
                max_after = max(probs[first:])
                for delta in [3, 5, 8, 10, 15, 20]:
                    target = entry_pct + delta
                    if target <= 99:
                        if max_after >= target / 100.0:
                            reached[delta] += 1

            if total < 5:
                continue
            print(f"    {entry_pct}% (n={total}):", end="")
            for delta in [3, 5, 8, 10, 15, 20]:
                target = entry_pct + delta
                if target <= 99:
                    r = reached[delta]
                    print(f"  →{target}%={r/total*100:.0f}%", end="")
            print()

    # Same for 15m
    print(f"\n{'='*100}")
    print(f"  PER-ASSET BREAKDOWN (15m markets)")
    print(f"{'='*100}")

    for asset in sorted(TRADE_ASSETS):
        asset_sides = [s for s in sides if s["tf"] == "15m" and s["asset"] == asset]
        print(f"\n  {asset} — {len(asset_sides)} trajectories")
        prob_sequences = [[p["prob"] for p in s["points"]] for s in asset_sides]

        key_entries = [55, 60, 65, 70, 75, 80, 85, 90]
        for entry_pct in key_entries:
            entry = entry_pct / 100.0
            total = 0
            reached = defaultdict(int)
            for probs in prob_sequences:
                first = None
                for idx, p in enumerate(probs):
                    if p >= entry:
                        first = idx
                        break
                if first is None:
                    continue
                total += 1
                max_after = max(probs[first:])
                for delta in [3, 5, 8, 10, 15, 20]:
                    target = entry_pct + delta
                    if target <= 99:
                        if max_after >= target / 100.0:
                            reached[delta] += 1

            if total < 5:
                continue
            print(f"    {entry_pct}% (n={total}):", end="")
            for delta in [3, 5, 8, 10, 15, 20]:
                target = entry_pct + delta
                if target <= 99:
                    r = reached[delta]
                    print(f"  →{target}%={r/total*100:.0f}%", end="")
            print()

if __name__ == "__main__":
    main()
