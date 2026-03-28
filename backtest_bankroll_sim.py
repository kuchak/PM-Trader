#!/usr/bin/env python3
"""
Full bankroll simulation using proposed 3-layer tier system.
Start with $100, simulate every qualifying match chronologically.
Report monthly PnL for 2023-2024.

Entry price model (estimated Polymarket price after S1):
  entry_price = min(0.93, 0.68 + 0.04 * s1_margin)
  +1→0.72  +2→0.76  +3→0.80  +4→0.84  +5→0.88  +6→0.92

Win:  profit = bet * (1/entry_price - 1)
Loss: loss   = bet
"""

import csv
import re
import os
from collections import defaultdict, OrderedDict

DATA_DIR = "/Users/minikrys/polymarket-monitor/data/atp"

FILES = {
    'ATP_TOUR': [f"{DATA_DIR}/atp_matches_2023.csv", f"{DATA_DIR}/atp_matches_2024.csv"],
    'ATP_CHALL': [f"{DATA_DIR}/atp_matches_qual_chall_2023.csv", f"{DATA_DIR}/atp_matches_qual_chall_2024.csv"],
    'WTA_TOUR': [f"{DATA_DIR}/wta_matches_2023.csv", f"{DATA_DIR}/wta_matches_2024.csv"],
    'WTA_CHALL': [f"{DATA_DIR}/wta_matches_qual_itf_2023.csv", f"{DATA_DIR}/wta_matches_qual_itf_2024.csv"],
}

# ── WR → Bet sizing ──
# WR bucket: (min_wr, bet_pct, bet_cap)
BET_TIERS = [
    (0.96, 0.40, None),   # ≥96% → 40%
    (0.94, 0.25, 75),     # 94-96% → 25%
    (0.92, 0.15, 50),     # 92-94% → 15%
    (0.90, 0.10, 40),     # 90-92% → 10%
    (0.88, 0.06, 25),     # 88-90% → 6%
]

MIN_BET = 5.0
MAX_ENTRY_PRICE = 0.93
STARTING_BANKROLL = 100.0


def parse_s1(score_str):
    if not score_str or score_str.strip() in ('', 'W/O', 'RET', 'DEF', 'Walkover'):
        return None
    score_str = re.sub(r'\s*(RET|ABN|DEF|W/O|Walkover|Default|Abandoned).*', '', score_str, flags=re.IGNORECASE)
    sets = score_str.strip().split()
    if not sets:
        return None
    m = re.match(r'(\d+)-(\d+)', sets[0])
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_seed(seed_val):
    if not seed_val or not seed_val.strip():
        return None
    num = re.match(r'(\d+)', seed_val.strip())
    return int(num.group(1)) if num else None


def estimate_entry_price(s1_margin):
    """Estimate Polymarket price after S1 based on margin."""
    return min(MAX_ENTRY_PRICE, 0.68 + 0.04 * s1_margin)


# ── Condition tables per category ──
# Each condition: (backtested_wr, check_fn_name, description)
# check_fn receives: (margin, is_seeded_vs_unseeded, seed_gap, ratio)

def make_conditions(category):
    """Return list of (wr, check_fn, desc) sorted by WR descending for a category."""
    conditions = []

    if category == 'WTA_CHALL':
        conditions = [
            (0.961, lambda m, svs, sg, r: svs and m >= 5, "seed+S1≥+5"),
            (0.959, lambda m, svs, sg, r: m >= 6, "S1=+6"),
            (0.948, lambda m, svs, sg, r: svs and m >= 4, "seed+S1≥+4"),
            (0.931, lambda m, svs, sg, r: m >= 5, "S1≥+5"),
            (0.909, lambda m, svs, sg, r: m >= 4, "S1≥+4"),
            (0.889, lambda m, svs, sg, r: m >= 3, "S1≥+3"),
        ]
    elif category == 'ATP_CHALL':
        conditions = [
            (0.966, lambda m, svs, sg, r: r >= 3 and m >= 4, "ratio≥3x+S1≥+4"),
            (0.950, lambda m, svs, sg, r: r >= 2 and m >= 5, "ratio≥2x+S1≥+5"),
            (0.946, lambda m, svs, sg, r: r >= 2 and m >= 4, "ratio≥2x+S1≥+4"),
            (0.931, lambda m, svs, sg, r: svs and m >= 5, "seed+S1≥+5"),
            (0.929, lambda m, svs, sg, r: r >= 2 and m >= 3, "ratio≥2x+S1≥+3"),
            (0.901, lambda m, svs, sg, r: svs and m >= 3, "seed+S1≥+3"),
            (0.881, lambda m, svs, sg, r: m >= 5, "S1≥+5"),
        ]
    elif category == 'ATP_TOUR':
        conditions = [
            (0.967, lambda m, svs, sg, r: r >= 5 and m >= 4, "ratio≥5x+S1≥+4"),
            (0.945, lambda m, svs, sg, r: r >= 5 and m >= 3, "ratio≥5x+S1≥+3"),
            (0.946, lambda m, svs, sg, r: r >= 3 and m >= 4, "ratio≥3x+S1≥+4"),
            (0.935, lambda m, svs, sg, r: r >= 5 and m >= 2, "ratio≥5x+S1≥+2"),
            (0.921, lambda m, svs, sg, r: r >= 3 and m >= 2, "ratio≥3x+S1≥+2"),
            (0.918, lambda m, svs, sg, r: svs and m >= 3, "seed+S1≥+3"),
            (0.904, lambda m, svs, sg, r: r >= 2 and m >= 3, "ratio≥2x+S1≥+3"),
            (0.895, lambda m, svs, sg, r: svs and m >= 2, "seed+S1≥+2"),
        ]
    elif category == 'WTA_TOUR':
        conditions = [
            (0.959, lambda m, svs, sg, r: r >= 5 and m >= 4, "ratio≥5x+S1≥+4"),
            (0.946, lambda m, svs, sg, r: r >= 5 and m >= 3, "ratio≥5x+S1≥+3"),
            (0.942, lambda m, svs, sg, r: r >= 2 and m >= 5, "ratio≥2x+S1≥+5"),
            (0.921, lambda m, svs, sg, r: r >= 3 and m >= 3, "ratio≥3x+S1≥+3"),
            (0.920, lambda m, svs, sg, r: svs and m >= 4, "seed+S1≥+4"),
            (0.911, lambda m, svs, sg, r: r >= 2 and m >= 3, "ratio≥2x+S1≥+3"),
            (0.900, lambda m, svs, sg, r: svs and m >= 3, "seed+S1≥+3"),
        ]

    # Sort by WR descending so first match = best tier
    conditions.sort(key=lambda x: -x[0])
    return conditions


def find_best_condition(category, margin, is_seeded_vs_unseeded, seed_gap, ratio):
    """Find highest-WR condition this match qualifies for. Returns (wr, desc) or None."""
    for wr, check_fn, desc in make_conditions(category):
        if check_fn(margin, is_seeded_vs_unseeded, seed_gap, ratio):
            return wr, desc
    return None


def get_bet_sizing(wr):
    """Given backtested WR, return (bet_pct, bet_cap)."""
    for min_wr, pct, cap in BET_TIERS:
        if wr >= min_wr:
            return pct, cap
    return None, None


def load_all_matches():
    """Load all matches from all categories, sorted by tournament date."""
    all_matches = []
    for category, files in FILES.items():
        for fp in files:
            with open(fp, encoding='utf-8', errors='replace') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    s1 = parse_s1(row.get('score', ''))
                    if s1 is None:
                        continue
                    w_s1, l_s1 = s1
                    if w_s1 == l_s1:
                        continue

                    wr = row.get('winner_rank', '').strip()
                    lr = row.get('loser_rank', '').strip()
                    td = row.get('tourney_date', '').strip()

                    all_matches.append({
                        'category': category,
                        'tourney_date': td,
                        'tourney_name': row.get('tourney_name', ''),
                        'round': row.get('round', ''),
                        'winner_name': row.get('winner_name', ''),
                        'loser_name': row.get('loser_name', ''),
                        'winner_seed': parse_seed(row.get('winner_seed', '')),
                        'loser_seed': parse_seed(row.get('loser_seed', '')),
                        'winner_rank': int(wr) if wr else None,
                        'loser_rank': int(lr) if lr else None,
                        'w_s1': w_s1,
                        'l_s1': l_s1,
                        'score': row.get('score', ''),
                    })

    # Sort by tourney_date
    all_matches.sort(key=lambda m: m['tourney_date'])
    return all_matches


def simulate():
    matches = load_all_matches()
    print(f"Loaded {len(matches)} matches across all categories\n")

    bankroll = STARTING_BANKROLL
    total_bets = 0
    total_wins = 0
    total_losses = 0
    total_wagered = 0.0
    total_profit = 0.0

    monthly_pnl = OrderedDict()
    monthly_bets = OrderedDict()
    category_stats = defaultdict(lambda: {'w': 0, 'l': 0, 'wagered': 0.0, 'pnl': 0.0})
    tier_stats = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': 0.0})
    condition_stats = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': 0.0, 'wagered': 0.0})

    for match in matches:
        w_s1, l_s1 = match['w_s1'], match['l_s1']
        match_winner_won_s1 = w_s1 > l_s1

        # Determine S1 winner's properties
        if match_winner_won_s1:
            s1w_seed = match['winner_seed']
            s1l_seed = match['loser_seed']
            s1w_rank = match['winner_rank']
            s1l_rank = match['loser_rank']
            margin = w_s1 - l_s1
            s1_winner_won_match = True
        else:
            s1w_seed = match['loser_seed']
            s1l_seed = match['winner_seed']
            s1w_rank = match['loser_rank']
            s1l_rank = match['winner_rank']
            margin = l_s1 - w_s1
            s1_winner_won_match = False

        # Compute factors
        is_seeded_vs_unseeded = (s1w_seed is not None and s1l_seed is None)
        seed_gap = 0
        if s1w_seed is not None and s1l_seed is not None and s1w_seed < s1l_seed:
            seed_gap = s1l_seed - s1w_seed

        ratio = 0
        if s1w_rank and s1l_rank and s1w_rank < s1l_rank:
            ratio = s1l_rank / s1w_rank

        # Find best condition
        result = find_best_condition(match['category'], margin, is_seeded_vs_unseeded, seed_gap, ratio)
        if result is None:
            continue

        wr, desc = result
        bet_pct, bet_cap = get_bet_sizing(wr)
        if bet_pct is None:
            continue

        # Calculate bet
        bet_amount = bankroll * bet_pct
        if bet_cap and bet_amount > bet_cap:
            bet_amount = bet_cap
        if bet_amount < MIN_BET:
            continue
        if bankroll < MIN_BET:
            continue

        # Entry price estimate
        entry_price = estimate_entry_price(margin)

        # Resolve
        if s1_winner_won_match:
            # Win: we bought shares at entry_price, they pay out $1
            profit = bet_amount * (1.0 / entry_price - 1.0)
            bankroll += profit
            total_wins += 1
            won = True
        else:
            # Loss: we lose our bet
            profit = -bet_amount
            bankroll += profit
            total_losses += 1
            won = False

        total_bets += 1
        total_wagered += bet_amount
        total_profit += profit

        # Monthly tracking
        td = match['tourney_date']
        if len(td) >= 6:
            month_key = f"{td[:4]}-{td[4:6]}"
        else:
            month_key = "unknown"

        if month_key not in monthly_pnl:
            monthly_pnl[month_key] = 0.0
            monthly_bets[month_key] = {'w': 0, 'l': 0, 'wagered': 0.0}
        monthly_pnl[month_key] += profit
        monthly_bets[month_key]['w' if won else 'l'] += 1
        monthly_bets[month_key]['wagered'] += bet_amount

        # Category stats
        cs = category_stats[match['category']]
        cs['w' if won else 'l'] += 1
        cs['wagered'] += bet_amount
        cs['pnl'] += profit

        # Condition stats
        ckey = f"{match['category']}: {desc}"
        condition_stats[ckey]['w' if won else 'l'] += 1
        condition_stats[ckey]['pnl'] += profit
        condition_stats[ckey]['wagered'] += bet_amount

        # WR tier stats
        for min_wr, _, _ in BET_TIERS:
            if wr >= min_wr:
                tier_stats[f"≥{int(min_wr*100)}%"]['w' if won else 'l'] += 1
                tier_stats[f"≥{int(min_wr*100)}%"]['pnl'] += profit
                break

    # ── Print results ──
    print(f"{'='*70}")
    print(f"  BANKROLL SIMULATION: ${STARTING_BANKROLL:.0f} starting")
    print(f"{'='*70}")
    print(f"\n  Final bankroll: ${bankroll:.2f}")
    print(f"  Total P&L: ${total_profit:.2f} ({total_profit/STARTING_BANKROLL*100:.1f}%)")
    print(f"  Total bets: {total_bets} ({total_wins}W/{total_losses}L = {total_wins/total_bets*100:.1f}%)")
    print(f"  Total wagered: ${total_wagered:.2f}")
    print(f"  ROI on wagered: {total_profit/total_wagered*100:.2f}%")

    # Monthly breakdown
    print(f"\n{'─'*70}")
    print(f"  MONTHLY P&L")
    print(f"{'─'*70}")
    print(f"  {'Month':<10} {'P&L':>10} {'Bankroll':>10} {'Bets':>6} {'W':>4} {'L':>4} {'WR%':>6} {'Wagered':>10}")
    print(f"  {'-'*64}")

    running_bankroll = STARTING_BANKROLL
    for month, pnl in monthly_pnl.items():
        running_bankroll += pnl
        mb = monthly_bets[month]
        total_m = mb['w'] + mb['l']
        wr_m = mb['w'] / total_m * 100 if total_m > 0 else 0
        print(f"  {month:<10} ${pnl:>+9.2f} ${running_bankroll:>9.2f} {total_m:>6} {mb['w']:>4} {mb['l']:>4} {wr_m:>5.1f}% ${mb['wagered']:>9.2f}")

    # Category breakdown
    print(f"\n{'─'*70}")
    print(f"  BY CATEGORY")
    print(f"{'─'*70}")
    print(f"  {'Category':<15} {'W':>5} {'L':>5} {'WR%':>6} {'P&L':>10} {'Wagered':>10} {'ROI%':>7}")
    print(f"  {'-'*62}")
    for cat in ['ATP_TOUR', 'ATP_CHALL', 'WTA_TOUR', 'WTA_CHALL']:
        cs = category_stats[cat]
        t = cs['w'] + cs['l']
        if t == 0:
            continue
        wr_c = cs['w'] / t * 100
        roi = cs['pnl'] / cs['wagered'] * 100 if cs['wagered'] > 0 else 0
        print(f"  {cat:<15} {cs['w']:>5} {cs['l']:>5} {wr_c:>5.1f}% ${cs['pnl']:>+9.2f} ${cs['wagered']:>9.2f} {roi:>+6.2f}%")

    # WR tier breakdown
    print(f"\n{'─'*70}")
    print(f"  BY WR TIER")
    print(f"{'─'*70}")
    print(f"  {'Tier':<10} {'W':>5} {'L':>5} {'WR%':>6} {'P&L':>10}")
    print(f"  {'-'*40}")
    for tier_name in ['≥96%', '≥94%', '≥92%', '≥90%', '≥88%']:
        ts = tier_stats.get(tier_name, {'w': 0, 'l': 0, 'pnl': 0.0})
        t = ts['w'] + ts['l']
        if t == 0:
            continue
        wr_t = ts['w'] / t * 100
        print(f"  {tier_name:<10} {ts['w']:>5} {ts['l']:>5} {wr_t:>5.1f}% ${ts['pnl']:>+9.2f}")

    # Top conditions
    print(f"\n{'─'*70}")
    print(f"  BY CONDITION (sorted by P&L)")
    print(f"{'─'*70}")
    print(f"  {'Condition':<35} {'W':>4} {'L':>4} {'WR%':>6} {'P&L':>9} {'Wagered':>9}")
    print(f"  {'-'*71}")
    sorted_conds = sorted(condition_stats.items(), key=lambda x: -x[1]['pnl'])
    for ckey, cs in sorted_conds:
        t = cs['w'] + cs['l']
        wr_c = cs['w'] / t * 100
        print(f"  {ckey:<35} {cs['w']:>4} {cs['l']:>4} {wr_c:>5.1f}% ${cs['pnl']:>+8.2f} ${cs['wagered']:>8.2f}")


if __name__ == '__main__':
    simulate()
