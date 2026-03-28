#!/usr/bin/env python3
"""Analyze probability momentum: if a market hits X%, how often does it later hit Y%?"""
import csv
from collections import defaultdict

SNAPSHOTS = "data/crypto_snapshots.csv"
TRADE_ASSETS = {"BTC", "ETH", "XRP"}
TRADE_TFS = {"15m", "1h"}

# Probability levels to analyze
LEVELS = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.82, 0.85, 0.88, 0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99]

def main():
    # Build trajectories: slug -> [(ts, outcome, prob, minutes_to_expiry)]
    trajectories = defaultdict(list)

    with open(SNAPSHOTS) as f:
        r = csv.reader(f)
        next(r)  # skip header
        for row in r:
            if len(row) < 12:
                continue
            if row[4] not in TRADE_TFS:
                continue
            asset = row[3].upper()
            if asset not in TRADE_ASSETS:
                continue
            slug = row[1]
            tf = row[4]
            outcome = row[7]  # "Up" or "Down"
            try:
                prob = float(row[8])
            except (ValueError, IndexError):
                continue
            try:
                minutes = float(row[11])
            except (ValueError, IndexError):
                minutes = None
            trajectories[slug].append({
                "ts": row[0], "outcome": outcome, "prob": prob,
                "minutes": minutes, "asset": asset, "tf": tf
            })

    print(f"Loaded {len(trajectories)} market trajectories")

    # For each market, separate Up and Down sides, sort by time
    # Then analyze: once prob crosses level X, does it ever reach level Y later?

    # We analyze EACH SIDE independently (Up trajectory, Down trajectory)
    sides = []
    for slug, points in trajectories.items():
        for side in ("Up", "Down"):
            pts = sorted([p for p in points if p["outcome"] == side], key=lambda x: x["ts"])
            if len(pts) >= 2:
                sides.append({"slug": slug, "side": side, "points": pts,
                              "asset": pts[0]["asset"], "tf": pts[0]["tf"]})

    print(f"Total side-trajectories: {len(sides)}")

    # Analysis 1: "If it hits X%, what % of the time does it later hit Y%?"
    # For each side-trajectory, find the FIRST time it crosses each level,
    # then check if it EVER reaches higher levels afterward

    # Matrix: hit_and_reached[entry_level][exit_level] = (count_reached, count_total)
    hit_and_reached = defaultdict(lambda: defaultdict(lambda: [0, 0]))

    # Also track by timeframe
    hit_and_reached_by_tf = {
        "15m": defaultdict(lambda: defaultdict(lambda: [0, 0])),
        "1h": defaultdict(lambda: defaultdict(lambda: [0, 0])),
    }

    # Also track by asset
    hit_and_reached_by_asset = {
        a: defaultdict(lambda: defaultdict(lambda: [0, 0])) for a in TRADE_ASSETS
    }

    # Track max prob reached and timing
    for s in sides:
        pts = s["points"]
        tf = s["tf"]
        asset = s["asset"]
        probs = [p["prob"] for p in pts]
        minutes_list = [p["minutes"] for p in pts]

        for i, entry_level in enumerate(LEVELS):
            # Find first index where prob >= entry_level
            first_cross = None
            for idx, p in enumerate(probs):
                if p >= entry_level:
                    first_cross = idx
                    break
            if first_cross is None:
                continue

            # What's the max prob AFTER this crossing?
            remaining = probs[first_cross:]
            max_after = max(remaining)

            for j, exit_level in enumerate(LEVELS):
                if exit_level <= entry_level:
                    continue
                hit_and_reached[entry_level][exit_level][1] += 1
                hit_and_reached_by_tf[tf][entry_level][exit_level][1] += 1
                hit_and_reached_by_asset[asset][entry_level][exit_level][1] += 1
                if max_after >= exit_level:
                    hit_and_reached[entry_level][exit_level][0] += 1
                    hit_and_reached_by_tf[tf][entry_level][exit_level][0] += 1
                    hit_and_reached_by_asset[asset][entry_level][exit_level][0] += 1

    # Print the matrix
    print(f"\n{'='*90}")
    print("IF IT HITS X% → HOW OFTEN DOES IT LATER HIT Y%? (all markets)")
    print(f"{'='*90}")
    print(f"{'Entry':>7}", end="")
    for el in LEVELS:
        if el >= 0.80:
            print(f" →{el*100:4.0f}%", end="")
    print(f"   (n)")
    print("-" * 90)
    for entry_level in LEVELS:
        if entry_level >= 0.98:
            continue
        print(f"{entry_level*100:5.0f}%  ", end="")
        n = 0
        for exit_level in LEVELS:
            if exit_level < 0.80:
                continue
            if exit_level <= entry_level:
                print(f"   -- ", end="")
            else:
                reached, total = hit_and_reached[entry_level][exit_level]
                n = total
                pct = reached / total * 100 if total > 0 else 0
                print(f" {pct:4.0f}%", end="")
        # Print sample size
        first_valid = None
        for exit_level in LEVELS:
            if exit_level > entry_level:
                first_valid = exit_level
                break
        if first_valid:
            _, total = hit_and_reached[entry_level][first_valid]
            print(f"   n={total}")
        else:
            print()

    # Per-timeframe breakdown
    for tf in ["15m", "1h"]:
        data = hit_and_reached_by_tf[tf]
        print(f"\n{'='*90}")
        print(f"SAME ANALYSIS — {tf.upper()} MARKETS ONLY")
        print(f"{'='*90}")
        print(f"{'Entry':>7}", end="")
        for el in LEVELS:
            if el >= 0.80:
                print(f" →{el*100:4.0f}%", end="")
        print(f"   (n)")
        print("-" * 90)
        for entry_level in LEVELS:
            if entry_level >= 0.98:
                continue
            print(f"{entry_level*100:5.0f}%  ", end="")
            for exit_level in LEVELS:
                if exit_level < 0.80:
                    continue
                if exit_level <= entry_level:
                    print(f"   -- ", end="")
                else:
                    reached, total = data[entry_level][exit_level]
                    pct = reached / total * 100 if total > 0 else 0
                    print(f" {pct:4.0f}%", end="")
            first_valid = None
            for exit_level in LEVELS:
                if exit_level > entry_level:
                    first_valid = exit_level
                    break
            if first_valid:
                _, total = data[entry_level][first_valid]
                print(f"   n={total}")
            else:
                print()

    # Analysis 2: Profit simulation
    # If we buy at X% and sell at Y%, what's the expected profit per $1 bet?
    # Buy cost per share = X/100. Sell revenue = Y/100. Profit = (Y-X)/X per dollar.
    # But if it never reaches Y, we hold to resolution — could be 100% (win) or 0% (lose).
    # For simplicity, assume if it doesn't reach Y, we lose the bet (worst case).

    print(f"\n{'='*90}")
    print("EXPECTED PROFIT PER $1 BET: buy at X%, sell at Y% (if not reached → assume total loss)")
    print(f"{'='*90}")

    best_combos = []
    for entry_level in LEVELS:
        if entry_level < 0.75 or entry_level >= 0.98:
            continue
        for exit_level in LEVELS:
            if exit_level <= entry_level or exit_level > 0.99:
                continue
            reached, total = hit_and_reached[entry_level][exit_level]
            if total < 20:
                continue
            hit_rate = reached / total
            profit_per_hit = (exit_level - entry_level) / entry_level
            # If not reached, worst case: total loss
            loss_per_miss = 1.0
            ev = hit_rate * profit_per_hit - (1 - hit_rate) * loss_per_miss
            best_combos.append({
                "entry": entry_level, "exit": exit_level,
                "hit_rate": hit_rate, "profit": profit_per_hit,
                "ev": ev, "n": total
            })

    # But that's too pessimistic — if it doesn't reach exit, it might still win at resolution
    # Let's also compute with a more realistic miss outcome

    print("\nScenario A: if target not reached → hold to resolution (use overall WR as proxy)")
    print("Scenario B: if target not reached → total loss (worst case)")
    print()

    # We know overall WR is ~94% from our earlier analysis
    HOLD_WR = 0.94

    print(f"{'Entry':>6} {'Exit':>6} {'Hit%':>6} {'Profit':>8} {'EV(A)':>8} {'EV(B)':>8} {'n':>5}")
    print("-" * 55)

    best_combos_a = []
    for c in sorted(best_combos, key=lambda x: -x["ev"]):
        # Scenario A: miss → hold, win HOLD_WR of time (get 1/entry - 1 profit), lose (1-HOLD_WR)
        miss_rate = 1 - c["hit_rate"]
        hold_ev = HOLD_WR * ((1.0 / c["entry"]) - 1) - (1 - HOLD_WR) * 1.0
        ev_a = c["hit_rate"] * c["profit"] + miss_rate * hold_ev
        ev_b = c["ev"]
        best_combos_a.append({**c, "ev_a": ev_a, "ev_b": ev_b})

    for c in sorted(best_combos_a, key=lambda x: -x["ev_a"])[:30]:
        print(f"{c['entry']*100:5.0f}% {c['exit']*100:5.0f}% {c['hit_rate']*100:5.1f}% "
              f"{c['profit']*100:7.1f}% {c['ev_a']*100:7.1f}% {c['ev_b']*100:7.1f}% {c['n']:5d}")

    # Analysis 3: Pure momentum — buy at X, sell at X+delta
    # For each entry, what's the MEDIAN and MEAN max prob reached?
    print(f"\n{'='*90}")
    print("MAX PROB REACHED AFTER ENTRY (distribution)")
    print(f"{'='*90}")

    for tf in ["15m", "1h"]:
        print(f"\n  {tf.upper()} markets:")
        print(f"  {'Entry':>6} {'Median Max':>10} {'Mean Max':>10} {'Min Max':>10} {'Went to 99+':>12} {'n':>5}")
        for entry_level in [0.75, 0.80, 0.82, 0.85, 0.88, 0.90, 0.92, 0.94, 0.95]:
            maxes = []
            for s in sides:
                if s["tf"] != tf:
                    continue
                probs = [p["prob"] for p in s["points"]]
                first_cross = None
                for idx, p in enumerate(probs):
                    if p >= entry_level:
                        first_cross = idx
                        break
                if first_cross is None:
                    continue
                max_after = max(probs[first_cross:])
                maxes.append(max_after)

            if len(maxes) < 5:
                continue
            maxes.sort()
            median = maxes[len(maxes) // 2]
            mean = sum(maxes) / len(maxes)
            min_max = min(maxes)
            pct_99 = sum(1 for m in maxes if m >= 0.99) / len(maxes) * 100
            print(f"  {entry_level*100:5.0f}% {median*100:9.1f}% {mean*100:9.1f}% {min_max*100:9.1f}% {pct_99:10.1f}%  {len(maxes):5d}")

    # Analysis 4: Time-based — how quickly does it go from entry to target?
    print(f"\n{'='*90}")
    print("TIME TO REACH TARGET (minutes after entry)")
    print(f"{'='*90}")

    for tf in ["15m", "1h"]:
        print(f"\n  {tf.upper()} markets:")
        for entry_level, exit_level in [(0.80, 0.85), (0.80, 0.90), (0.85, 0.90),
                                          (0.85, 0.95), (0.88, 0.92), (0.88, 0.95),
                                          (0.90, 0.95), (0.90, 0.97), (0.92, 0.96)]:
            times = []
            for s in sides:
                if s["tf"] != tf:
                    continue
                pts = s["points"]
                # Find first cross of entry
                entry_idx = None
                for idx, p in enumerate(pts):
                    if p["prob"] >= entry_level and p["minutes"] is not None:
                        entry_idx = idx
                        break
                if entry_idx is None:
                    continue
                entry_min = pts[entry_idx]["minutes"]
                # Find first cross of exit AFTER entry
                for p in pts[entry_idx:]:
                    if p["prob"] >= exit_level and p["minutes"] is not None:
                        elapsed = entry_min - p["minutes"]  # minutes decrease toward expiry
                        if elapsed >= 0:
                            times.append(elapsed)
                        break
            if len(times) < 5:
                continue
            times.sort()
            median = times[len(times) // 2]
            mean = sum(times) / len(times)
            print(f"  {entry_level*100:.0f}%→{exit_level*100:.0f}%: "
                  f"median {median:.1f}min, mean {mean:.1f}min, "
                  f"reached {len(times)} times")

if __name__ == "__main__":
    main()
