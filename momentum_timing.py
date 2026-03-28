#!/usr/bin/env python3
"""Timing analysis: WHEN do 1h dips happen, and how long do recoveries take?"""
import csv
from collections import defaultdict

SNAPSHOTS = "data/crypto_snapshots.csv"
TRADE_ASSETS = {"BTC", "ETH", "XRP"}

def main():
    # Build 1h trajectories only
    trajectories = defaultdict(list)
    with open(SNAPSHOTS) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 12 or row[4] != "1h":
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
                "minutes": minutes, "asset": asset
            })

    sides = []
    for slug, points in trajectories.items():
        for side in ("Up", "Down"):
            pts = sorted([p for p in points if p["outcome"] == side], key=lambda x: x["ts"])
            if len(pts) >= 2:
                sides.append({"slug": slug, "side": side, "points": pts, "asset": pts[0]["asset"]})

    print(f"1h side-trajectories: {len(sides)}\n")

    # Analysis 1: When do dips to various levels occur (minutes to expiry)?
    print(f"{'='*100}")
    print(f"WHEN DO DIPS HAPPEN? (minutes to expiry when price first drops to/below level)")
    print(f"{'='*100}")

    for dip_pct in [50, 55, 60, 65, 70, 75, 80]:
        dip = dip_pct / 100.0
        dip_times = []
        for s in sides:
            probs = [p["prob"] for p in s["points"]]
            mins = [p["minutes"] for p in s["points"]]
            for i in range(1, len(probs)):
                if probs[i-1] > dip and probs[i] <= dip and mins[i] is not None:
                    dip_times.append(mins[i])
                    break
        if len(dip_times) < 5:
            continue
        dip_times.sort()
        # Bucket by time windows
        buckets = {"50-60m": 0, "40-50m": 0, "30-40m": 0, "20-30m": 0, "10-20m": 0, "0-10m": 0}
        for t in dip_times:
            if t >= 50: buckets["50-60m"] += 1
            elif t >= 40: buckets["40-50m"] += 1
            elif t >= 30: buckets["30-40m"] += 1
            elif t >= 20: buckets["20-30m"] += 1
            elif t >= 10: buckets["10-20m"] += 1
            else: buckets["0-10m"] += 1

        print(f"\n  Dips to {dip_pct}% (n={len(dip_times)}, median={dip_times[len(dip_times)//2]:.0f}min):")
        for label, count in buckets.items():
            bar = "█" * count
            print(f"    {label}: {count:3d} {bar}")

    # Analysis 2: For dips at different times, what's the recovery rate?
    print(f"\n{'='*100}")
    print(f"RECOVERY RATE BY DIP TIMING (dip to 50-70%, does it recover +15pp?)")
    print(f"{'='*100}")

    print(f"\n  {'Dip to':>8} {'Window':>10} {'n':>5} {'Recov +10pp':>12} {'Recov +15pp':>12} {'Recov +20pp':>12} {'Med recov':>10}")

    for dip_pct in [50, 55, 60, 65, 70]:
        dip = dip_pct / 100.0
        for window_min, window_max, label in [(40, 60, "40-60m"), (20, 40, "20-40m"), (5, 20, " 5-20m")]:
            recoveries = []
            for s in sides:
                probs = [p["prob"] for p in s["points"]]
                mins = [p["minutes"] for p in s["points"]]
                for i in range(1, len(probs)):
                    if probs[i-1] > dip and probs[i] <= dip and mins[i] is not None:
                        if window_min <= mins[i] < window_max:
                            max_after = max(probs[i:])
                            recoveries.append(max_after - probs[i])
                        break
            if len(recoveries) < 3:
                continue
            recoveries.sort()
            med = recoveries[len(recoveries)//2]
            r10 = sum(1 for r in recoveries if r >= 0.10) / len(recoveries) * 100
            r15 = sum(1 for r in recoveries if r >= 0.15) / len(recoveries) * 100
            r20 = sum(1 for r in recoveries if r >= 0.20) / len(recoveries) * 100
            print(f"  {dip_pct:5d}% {label:>10} {len(recoveries):5d} {r10:11.0f}% {r15:11.0f}% {r20:11.0f}% {med*100:9.1f}pp")

    # Analysis 3: Time from dip to recovery peak
    print(f"\n{'='*100}")
    print(f"HOW LONG DOES RECOVERY TAKE? (minutes from dip to peak)")
    print(f"{'='*100}")

    for dip_pct in [50, 55, 60, 65, 70]:
        dip = dip_pct / 100.0
        recovery_times = []
        for s in sides:
            probs = [p["prob"] for p in s["points"]]
            mins = [p["minutes"] for p in s["points"]]
            for i in range(1, len(probs)):
                if probs[i-1] > dip and probs[i] <= dip and mins[i] is not None:
                    # Find the peak after dip
                    max_after = max(probs[i:])
                    if max_after - probs[i] >= 0.10:  # only count meaningful recoveries
                        max_idx = i + probs[i:].index(max_after)
                        if mins[max_idx] is not None:
                            elapsed = mins[i] - mins[max_idx]
                            if elapsed >= 0:
                                recovery_times.append({"elapsed": elapsed, "dip_min": mins[i],
                                                       "peak_min": mins[max_idx], "gain": max_after - probs[i]})
                    break
        if len(recovery_times) < 5:
            continue
        times = sorted([r["elapsed"] for r in recovery_times])
        print(f"\n  Dip to {dip_pct}% (n={len(recovery_times)} with +10pp recovery):")
        print(f"    Time to peak: median={times[len(times)//2]:.1f}min, mean={sum(times)/len(times):.1f}min")
        print(f"    Fastest: {times[0]:.1f}min, Slowest: {times[-1]:.1f}min")
        # What % recover within X minutes?
        for mins_limit in [2, 5, 10, 15, 20, 30]:
            pct = sum(1 for t in times if t <= mins_limit) / len(times) * 100
            print(f"    Within {mins_limit:2d}min: {pct:.0f}%")

    # Analysis 4: Optimal sell timing — sell at fixed time after dip vs sell at target
    print(f"\n{'='*100}")
    print(f"SELL STRATEGY: fixed target vs time-based exit")
    print(f"{'='*100}")

    for dip_pct in [50, 60, 65, 70]:
        dip = dip_pct / 100.0
        print(f"\n  Buy at dip to {dip_pct}%:")
        print(f"  {'Strategy':>25} {'n':>5} {'Avg gain':>10} {'Win%':>6} {'Med gain':>10}")

        # Strategy A: sell at fixed targets
        for target_delta in [5, 10, 15, 20, 25]:
            target = dip + target_delta / 100.0
            gains = []
            for s in sides:
                probs = [p["prob"] for p in s["points"]]
                mins = [p["minutes"] for p in s["points"]]
                for i in range(1, len(probs)):
                    if probs[i-1] > dip and probs[i] <= dip and mins[i] is not None:
                        # Did it hit target?
                        hit = False
                        for j in range(i, len(probs)):
                            if probs[j] >= target:
                                # Gain = (target - buy_price) / buy_price
                                gains.append((target - probs[i]) / probs[i])
                                hit = True
                                break
                        if not hit:
                            # Hold to end — use last known prob
                            final = probs[-1]
                            gains.append((final - probs[i]) / probs[i])
                        break

            if len(gains) < 5:
                continue
            avg = sum(gains) / len(gains)
            med = sorted(gains)[len(gains)//2]
            wr = sum(1 for g in gains if g > 0) / len(gains) * 100
            print(f"  {'Target +' + str(target_delta) + 'pp':>25} {len(gains):5d} {avg*100:9.1f}% {wr:5.0f}% {med*100:9.1f}%")

        # Strategy B: sell at fixed time after dip
        for hold_mins in [5, 10, 15, 20, 30]:
            gains = []
            for s in sides:
                probs = [p["prob"] for p in s["points"]]
                mins = [p["minutes"] for p in s["points"]]
                for i in range(1, len(probs)):
                    if probs[i-1] > dip and probs[i] <= dip and mins[i] is not None:
                        target_time = mins[i] - hold_mins
                        # Find closest snapshot to target_time
                        best_j = None
                        best_diff = 999
                        for j in range(i, len(probs)):
                            if mins[j] is not None:
                                diff = abs(mins[j] - target_time)
                                if diff < best_diff:
                                    best_diff = diff
                                    best_j = j
                        if best_j is not None:
                            gains.append((probs[best_j] - probs[i]) / probs[i])
                        break

            if len(gains) < 5:
                continue
            avg = sum(gains) / len(gains)
            med = sorted(gains)[len(gains)//2]
            wr = sum(1 for g in gains if g > 0) / len(gains) * 100
            print(f"  {'Hold ' + str(hold_mins) + 'min':>25} {len(gains):5d} {avg*100:9.1f}% {wr:5.0f}% {med*100:9.1f}%")

    # Analysis 5: per-asset dip behavior
    print(f"\n{'='*100}")
    print(f"PER-ASSET DIP RECOVERY (dip to 50-70%, recovery +15pp)")
    print(f"{'='*100}")

    for asset in sorted(TRADE_ASSETS):
        asset_sides = [s for s in sides if s["asset"] == asset]
        print(f"\n  {asset} ({len(asset_sides)} trajectories):")
        for dip_pct in [50, 55, 60, 65, 70]:
            dip = dip_pct / 100.0
            recoveries = []
            for s in asset_sides:
                probs = [p["prob"] for p in s["points"]]
                for i in range(1, len(probs)):
                    if probs[i-1] > dip and probs[i] <= dip:
                        max_after = max(probs[i:])
                        recoveries.append(max_after - probs[i])
                        break
            if len(recoveries) < 3:
                continue
            r15 = sum(1 for r in recoveries if r >= 0.15) / len(recoveries) * 100
            r20 = sum(1 for r in recoveries if r >= 0.20) / len(recoveries) * 100
            med = sorted(recoveries)[len(recoveries)//2]
            print(f"    Dip to {dip_pct}%: n={len(recoveries):3d}, +15pp={r15:.0f}%, +20pp={r20:.0f}%, med={med*100:.0f}pp")

if __name__ == "__main__":
    main()
