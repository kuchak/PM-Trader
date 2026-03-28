#!/usr/bin/env python3
"""Min-min strategy: buy at the market's lowest point, sell at its peak after."""
import csv
from collections import defaultdict

SNAPSHOTS = "data/crypto_snapshots.csv"
TRADE_ASSETS = {"BTC", "ETH", "XRP"}
TRADE_TFS = {"15m", "1h"}

def main():
    # Build trajectories
    trajectories = defaultdict(list)
    with open(SNAPSHOTS) as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            if len(row) < 12 or row[4] not in TRADE_TFS:
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

    print(f"Loaded {len(sides)} side-trajectories\n")

    for tf in ["1h", "15m"]:
        tf_sides = [s for s in sides if s["tf"] == tf]
        print(f"{'='*100}")
        print(f"  {tf.upper()} MARKETS — {len(tf_sides)} trajectories")
        print(f"{'='*100}")

        # For each trajectory: find the overall min, then the max AFTER that min
        records = []
        for s in tf_sides:
            probs = [p["prob"] for p in s["points"]]
            mins = [p["minutes"] for p in s["points"]]

            overall_min = min(probs)
            min_idx = probs.index(overall_min)

            # Max after the min
            after_min = probs[min_idx:]
            max_after_min = max(after_min)

            # Also: overall max
            overall_max = max(probs)

            # Min-to-max spread
            spread = max_after_min - overall_min

            # Time at min and max
            min_minutes = mins[min_idx]

            # Find max_after index
            max_after_idx = min_idx + after_min.index(max_after_min)
            max_minutes = mins[max_after_idx]

            records.append({
                "slug": s["slug"], "side": s["side"], "asset": s["asset"],
                "min": overall_min, "max_after": max_after_min,
                "overall_max": overall_max, "spread": spread,
                "min_minutes": min_minutes, "max_minutes": max_minutes,
                "n_points": len(probs),
                "first_prob": probs[0], "last_prob": probs[-1],
            })

        # Distribution of minimums
        print(f"\n  DISTRIBUTION OF MARKET MINIMUMS (lowest prob each side ever hits)")
        buckets = defaultdict(int)
        for r in records:
            b = int(r["min"] * 100 // 5) * 5  # 5% buckets
            buckets[b] += 1
        for b in sorted(buckets.keys()):
            bar = "█" * buckets[b]
            print(f"    {b:3d}-{b+4}%: {buckets[b]:4d} {bar}")

        # Distribution of spreads (max_after - min)
        print(f"\n  DISTRIBUTION OF MIN→MAX SPREAD (how much it recovers after hitting bottom)")
        spread_buckets = defaultdict(int)
        for r in records:
            b = int(r["spread"] * 100 // 5) * 5
            spread_buckets[b] += 1
        for b in sorted(spread_buckets.keys()):
            bar = "█" * spread_buckets[b]
            print(f"    {b:3d}-{b+4}%: {spread_buckets[b]:4d} {bar}")

        # What if we buy at the min? What's the realistic gain?
        print(f"\n  IF YOU BOUGHT AT THE EXACT MINIMUM:")
        print(f"    Median min: {sorted([r['min'] for r in records])[len(records)//2]*100:.1f}%")
        print(f"    Median max after min: {sorted([r['max_after'] for r in records])[len(records)//2]*100:.1f}%")
        print(f"    Median spread: {sorted([r['spread'] for r in records])[len(records)//2]*100:.1f}pp")
        print(f"    Mean spread: {sum(r['spread'] for r in records)/len(records)*100:.1f}pp")

        # More practical: buy when price DROPS to a level from above
        # i.e., it was above X, drops to X or below, then recovers to Y
        print(f"\n  PRACTICAL DIP-BUY: price drops FROM above X TO below X, then recovers")
        print(f"  {'Dip to':>8} {'Count':>6} {'Recovers +5pp':>14} {'Recovers +10pp':>15} "
              f"{'Recovers +15pp':>15} {'Recovers +20pp':>15} {'Med recovery':>13}")

        for dip_level_pct in range(30, 90, 5):
            dip_level = dip_level_pct / 100.0
            dip_count = 0
            recoveries = []
            for s in tf_sides:
                probs = [p["prob"] for p in s["points"]]
                # Find instances where price was above dip_level then drops to/below it
                for i in range(1, len(probs)):
                    if probs[i-1] > dip_level and probs[i] <= dip_level:
                        # Found a dip. What's the max after?
                        max_after = max(probs[i:])
                        recovery = max_after - probs[i]
                        dip_count += 1
                        recoveries.append(recovery)
                        break  # only count first dip per trajectory

            if dip_count < 5:
                continue
            recoveries.sort()
            med = recoveries[len(recoveries)//2]
            r5 = sum(1 for r in recoveries if r >= 0.05) / len(recoveries) * 100
            r10 = sum(1 for r in recoveries if r >= 0.10) / len(recoveries) * 100
            r15 = sum(1 for r in recoveries if r >= 0.15) / len(recoveries) * 100
            r20 = sum(1 for r in recoveries if r >= 0.20) / len(recoveries) * 100
            print(f"  {dip_level_pct:5d}%  {dip_count:6d} {r5:13.0f}% {r10:14.0f}% "
                  f"{r15:14.0f}% {r20:14.0f}% {med*100:11.1f}pp")

        # The REAL min-min: for markets where BOTH sides exist,
        # buy whichever side is cheapest (close to 0%) — it's essentially
        # buying the underdog. Does the underdog ever swing up?
        print(f"\n  UNDERDOG STRATEGY: buy the LOW side (prob < 50%) — does it ever swing up?")
        underdog_records = []
        for s in tf_sides:
            probs = [p["prob"] for p in s["points"]]
            # Only look at sides that start or go below 50%
            below_50 = [(i, p) for i, p in enumerate(probs) if p < 0.50]
            if not below_50:
                continue
            # Find the lowest point below 50
            min_idx, min_prob = min(below_50, key=lambda x: x[1])
            # Max after that point
            max_after = max(probs[min_idx:])
            underdog_records.append({
                "asset": s["asset"], "min": min_prob, "max_after": max_after,
                "spread": max_after - min_prob
            })

        if underdog_records:
            print(f"    Total underdog entries: {len(underdog_records)}")
            print(f"    Median min (buy point): {sorted([r['min'] for r in underdog_records])[len(underdog_records)//2]*100:.1f}%")
            print(f"    Median max after: {sorted([r['max_after'] for r in underdog_records])[len(underdog_records)//2]*100:.1f}%")
            print(f"    Median spread: {sorted([r['spread'] for r in underdog_records])[len(underdog_records)//2]*100:.1f}pp")

            # Bucket by entry price
            print(f"\n    {'Buy at':>8} {'n':>5} {'→40%':>6} {'→50%':>6} {'→60%':>6} {'→70%':>6} {'→80%':>6} {'→90%':>6} {'→95%':>6}")
            for buy_pct in range(5, 50, 5):
                buy = buy_pct / 100.0
                matching = [r for r in underdog_records if buy <= r["min"] < buy + 0.05]
                if len(matching) < 5:
                    continue
                n = len(matching)
                targets = {}
                for t in [40, 50, 60, 70, 80, 90, 95]:
                    tv = t / 100.0
                    if tv <= buy:
                        targets[t] = "  -- "
                    else:
                        hit = sum(1 for r in matching if r["max_after"] >= tv)
                        targets[t] = f"{hit/n*100:4.0f}%"
                print(f"    {buy_pct:3d}-{buy_pct+4}% {n:5d}", end="")
                for t in [40, 50, 60, 70, 80, 90, 95]:
                    print(f" {targets[t]:>5}", end="")
                print()

        # Per-asset min-min
        print(f"\n  PER-ASSET: buy at overall min, sell at max after")
        for asset in sorted(TRADE_ASSETS):
            asset_recs = [r for r in records if r["asset"] == asset]
            if not asset_recs:
                continue
            spreads = sorted([r["spread"] for r in asset_recs])
            mins = sorted([r["min"] for r in asset_recs])
            print(f"    {asset} (n={len(asset_recs)}): "
                  f"median min={mins[len(mins)//2]*100:.0f}%, "
                  f"median spread={spreads[len(spreads)//2]*100:.0f}pp, "
                  f"mean spread={sum(spreads)/len(spreads)*100:.0f}pp, "
                  f"min spread={spreads[0]*100:.0f}pp, "
                  f"max spread={spreads[-1]*100:.0f}pp")

        # What % of markets have a min below X and then recover above Y?
        print(f"\n  MARKET FLOOR → CEILING (% of all {tf} markets)")
        print(f"  {'Floor<':>8}", end="")
        for ceiling in [60, 70, 80, 85, 90, 95, 99]:
            print(f" {'→'+str(ceiling)+'%':>7}", end="")
        print(f" {'n':>5}")
        for floor_pct in [5, 10, 15, 20, 25, 30, 35, 40, 45, 50]:
            floor = floor_pct / 100.0
            matching = [r for r in records if r["min"] <= floor]
            if len(matching) < 3:
                continue
            n = len(matching)
            print(f"  {floor_pct:5d}%  ", end="")
            for ceiling in [60, 70, 80, 85, 90, 95, 99]:
                cv = ceiling / 100.0
                hit = sum(1 for r in matching if r["max_after"] >= cv)
                print(f" {hit/n*100:6.0f}%", end="")
            print(f" {n:5d}")

if __name__ == "__main__":
    main()
