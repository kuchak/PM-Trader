#!/usr/bin/env python3
"""Analyze whether stop-losses would have caught losing crypto markets before expiry wipeout."""
import csv
from collections import defaultdict
from datetime import datetime

SNAPSHOTS = "data/crypto_snapshots.csv"
RESOLUTIONS = "data/crypto_resolutions.csv"

# Only analyze timeframes we trade
TRADE_TIMEFRAMES = {"15m", "1h"}
# Only analyze assets we trade
TRADE_ASSETS = {"BTC", "ETH", "XRP"}  # SOL dropped

STOP_LEVELS = [0.92, 0.90, 0.88, 0.85, 0.82, 0.80, 0.75, 0.70, 0.60, 0.50, 0.40]

# Entry threshold
ENTRY_THRESHOLD = 0.90

def main():
    # Step 1: Load resolutions — find losing markets
    # A "losing" market for us = we bet "Up" at >=90% prob, but "Down" won (or vice versa)
    # Since we always buy the high-prob side, a loss = the winning_outcome is the opposite of what was at 90%+
    # In Up/Down markets, we buy whichever side is >=90%. If that side loses, we lose.

    losing_slugs = {}  # event_slug -> {asset, tf, winning_outcome}
    winning_slugs = {}

    with open(RESOLUTIONS) as f:
        reader = csv.DictReader(f)
        for row in reader:
            tf = row.get("timeframe", "")
            asset = row.get("asset", "")
            if tf not in TRADE_TIMEFRAMES or asset not in TRADE_ASSETS:
                continue
            slug = row["event_slug"]
            winning = row["winning_outcome"].strip().upper()
            losing_slugs[slug] = {"asset": asset, "tf": tf, "winning": winning}

    print(f"Total tradeable resolutions: {len(losing_slugs)}")

    # Step 2: Load snapshots and build price trajectories per slug
    # We need to figure out which column is the slug and which is the prob
    # For old-format rows (daily_above): col2=slug, col6=implied_prob
    # For new-format rows (up_down): col2=event_slug, col9=implied_prob

    trajectories = defaultdict(list)  # slug -> [(timestamp, up_prob, minutes_to_expiry)]

    with open(SNAPSHOTS) as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if len(row) < 10:
                continue
            # Detect format by checking if column 5 looks like a timeframe
            col5 = row[4] if len(row) > 4 else ""
            if col5 in ("15m", "1h", "4h", "5m"):
                # New format: event_slug=col2, series_slug=col3, asset=col4, tf=col5,
                # market_type=col6, threshold=col7, outcome=col8, prob=col9,
                # liquidity=col10, volume=col11, minutes_to_expiry=col12
                event_slug = row[1]
                asset = row[3].upper()
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

                if tf not in TRADE_TIMEFRAMES or asset not in TRADE_ASSETS:
                    continue
                if event_slug not in losing_slugs:
                    continue

                ts = row[0]
                trajectories[event_slug].append({
                    "ts": ts, "outcome": outcome, "prob": prob, "minutes": minutes
                })
            else:
                # Old format — skip (daily_above, not what we trade)
                continue

    print(f"Slugs with snapshot data: {len(trajectories)}")

    # Step 3: For each resolved market, determine if we would have entered and if stops would have helped

    # We enter when prob >= 90% (buy the high side)
    # A "loss" is when we bought a side and the OTHER side won

    results = {
        "total_losses": 0,
        "total_wins": 0,
        "losses_with_data": 0,
        "wins_with_data": 0,
        "loss_details": [],
        "stop_saves": {level: 0 for level in STOP_LEVELS},
        "stop_false_triggers_on_wins": {level: 0 for level in STOP_LEVELS},
        "gap_through_losses": 0,  # went from >=entry straight to 0 with no dip
    }

    for slug, info in losing_slugs.items():
        winning_outcome = info["winning"]  # "UP" or "DOWN"
        tf = info["tf"]

        if slug not in trajectories:
            continue

        traj = trajectories[slug]

        # Group by outcome — we need the "Up" trajectory and "Down" trajectory
        up_points = sorted([p for p in traj if p["outcome"] == "Up"], key=lambda x: x["ts"])
        down_points = sorted([p for p in traj if p["outcome"] == "Down"], key=lambda x: x["ts"])

        if not up_points and not down_points:
            continue

        # Determine which side we would have bought
        # We buy the side with prob >= 90%
        # Check if Up ever hit 90%+
        up_entry = None
        down_entry = None

        for p in up_points:
            if p["prob"] >= ENTRY_THRESHOLD and (p["minutes"] is not None):
                # Check time windows: 15m=3-13min, 1h=10-50min
                if tf == "15m" and 3 <= p["minutes"] <= 13:
                    up_entry = p
                    break
                elif tf == "1h" and 10 <= p["minutes"] <= 50:
                    up_entry = p
                    break

        for p in down_points:
            if p["prob"] >= ENTRY_THRESHOLD and (p["minutes"] is not None):
                if tf == "15m" and 3 <= p["minutes"] <= 13:
                    down_entry = p
                    break
                elif tf == "1h" and 10 <= p["minutes"] <= 50:
                    down_entry = p
                    break

        # Pick the side we would have entered (highest prob at entry)
        entry = None
        our_side = None
        if up_entry and down_entry:
            if up_entry["prob"] >= down_entry["prob"]:
                entry = up_entry
                our_side = "UP"
            else:
                entry = down_entry
                our_side = "DOWN"
        elif up_entry:
            entry = up_entry
            our_side = "UP"
        elif down_entry:
            entry = down_entry
            our_side = "DOWN"
        else:
            continue  # No valid entry point

        # Skip entries at >= 99% (our rule)
        if entry["prob"] >= 0.99:
            continue

        # Did we win or lose?
        won = (our_side == winning_outcome)

        # Get our side's trajectory AFTER entry
        our_points = up_points if our_side == "UP" else down_points
        post_entry = [p for p in our_points if p["ts"] >= entry["ts"]]

        if won:
            results["total_wins"] += 1
            results["wins_with_data"] += 1

            # Check false stop triggers on wins
            for level in STOP_LEVELS:
                min_prob = min(p["prob"] for p in post_entry) if post_entry else 1.0
                if min_prob <= level:
                    results["stop_false_triggers_on_wins"][level] += 1
        else:
            results["total_losses"] += 1
            results["losses_with_data"] += 1

            # Track the price trajectory for this loss
            min_prob = min(p["prob"] for p in post_entry) if post_entry else entry["prob"]
            max_prob_after_entry = max(p["prob"] for p in post_entry) if post_entry else entry["prob"]

            detail = {
                "slug": slug,
                "asset": info["asset"],
                "tf": tf,
                "our_side": our_side,
                "winning": winning_outcome,
                "entry_prob": entry["prob"],
                "entry_minutes": entry["minutes"],
                "min_prob_after": min_prob,
                "max_prob_after": max_prob_after_entry,
                "n_snapshots": len(post_entry),
                "trajectory": [(p["ts"], p["prob"], p["minutes"]) for p in post_entry],
            }
            results["loss_details"].append(detail)

            # Check which stop levels would have saved us
            for level in STOP_LEVELS:
                if min_prob <= level:
                    results["stop_saves"][level] += 1

            if min_prob > 0.85:
                results["gap_through_losses"] += 1

    # Print results
    print(f"\n{'='*70}")
    print(f"STOP-LOSS ANALYSIS FOR CRYPTO UP/DOWN MARKETS")
    print(f"{'='*70}")
    print(f"Markets where we would have entered (prob >= {ENTRY_THRESHOLD*100}%, correct time window):")
    print(f"  Wins:   {results['wins_with_data']}")
    print(f"  Losses: {results['losses_with_data']}")
    if results['wins_with_data'] + results['losses_with_data'] > 0:
        wr = results['wins_with_data'] / (results['wins_with_data'] + results['losses_with_data']) * 100
        print(f"  Win rate: {wr:.1f}%")

    print(f"\n--- STOP-LOSS EFFECTIVENESS ON LOSSES ---")
    print(f"Total losses: {results['losses_with_data']}")
    print(f"Gap-through losses (never dipped below 85%): {results['gap_through_losses']}")
    print(f"\nStop level | Losses caught | % of losses | False triggers on wins | % of wins")
    print(f"{'-'*85}")
    for level in STOP_LEVELS:
        saves = results['stop_saves'][level]
        false_t = results['stop_false_triggers_on_wins'][level]
        pct_losses = (saves / results['losses_with_data'] * 100) if results['losses_with_data'] > 0 else 0
        pct_wins = (false_t / results['wins_with_data'] * 100) if results['wins_with_data'] > 0 else 0
        print(f"  {level*100:5.1f}%   |  {saves:4d}         | {pct_losses:5.1f}%       | {false_t:4d}                  | {pct_wins:5.1f}%")

    print(f"\n--- LOSS DETAILS (sorted by min prob) ---")
    sorted_losses = sorted(results["loss_details"], key=lambda x: x["min_prob_after"])

    by_tf = defaultdict(list)
    for d in sorted_losses:
        by_tf[d["tf"]].append(d)

    for tf in ["15m", "1h"]:
        losses = by_tf.get(tf, [])
        print(f"\n  {tf} losses ({len(losses)}):")
        for d in losses[:30]:  # Show up to 30
            traj_str = ""
            if len(d["trajectory"]) <= 8:
                traj_str = " | " + " → ".join(f"{p[1]*100:.0f}%" for p in d["trajectory"])
            else:
                # Show first 3, last 3
                pts = d["trajectory"]
                traj_str = " | " + " → ".join(f"{p[1]*100:.0f}%" for p in pts[:3]) + " ... " + " → ".join(f"{p[1]*100:.0f}%" for p in pts[-3:])

            print(f"    {d['asset']} | entry={d['entry_prob']*100:.0f}% @{d['entry_minutes']:.0f}min | "
                  f"min={d['min_prob_after']*100:.1f}% | {d['n_snapshots']} pts{traj_str}")

    # PnL analysis: what would different stops save?
    print(f"\n--- PNL IMPACT ANALYSIS ---")
    print(f"Assuming $20 avg bet, entry at 92% avg, win pays ~8% profit ($1.60)")
    print(f"Without stops: loss = -$20 per loss (100% of bet)")

    for level in STOP_LEVELS:
        saves = results['stop_saves'][level]
        false_t = results['stop_false_triggers_on_wins'][level]
        loss_at_stop = 1.0 - level  # fraction lost when stop triggers (entered at ~92%, stop at level)
        # Actually: entered at entry_prob, stop at level. Loss = (entry_prob - level) * shares
        # Simplified: cost = entry_price * shares. At stop, value = level * shares.
        # PnL at stop = (level - entry_price) * shares = negative
        # vs PnL at 0 = (0 - entry_price) * shares = -cost
        # Savings per stopped loss ≈ level * cost / entry_price  (roughly)

        # More precisely:
        # Without stop: lose full cost (market goes to 0)
        # With stop at level: lose (entry - level)/entry fraction of cost
        # Savings per loss = level/entry * cost ≈ level * $20 / 0.92

        avg_entry = 0.92
        cost = 20
        loss_without = cost  # full loss
        loss_with = cost * (avg_entry - level) / avg_entry  # partial loss
        savings_per = loss_without - loss_with

        # False triggers: lose the win we would have had
        win_profit = cost * (1.0 / avg_entry - 1)  # profit per win ≈ $1.74
        # Plus we take a loss on the false trigger
        false_loss = cost * (avg_entry - level) / avg_entry
        false_cost = (win_profit + false_loss) * false_t

        total_saved = savings_per * saves
        net = total_saved - false_cost

        print(f"  Stop {level*100:.0f}%: saves ${total_saved:,.0f} on {saves} catches, "
              f"costs ${false_cost:,.0f} on {false_t} false triggers → net ${net:+,.0f}")

if __name__ == "__main__":
    main()
