#!/usr/bin/env python3
"""
Bankroll simulation v2 — fixes from v1:
1. Exclusive tier assignment (each match → one condition only)
2. Bet caps on ALL tiers
3. Reports EXCLUSIVE WRs (true WR when higher tiers take the best matches)
4. Entry price estimated from S1 margin

Start: $100. Reports monthly PnL.
"""

import csv
import re
from collections import defaultdict, OrderedDict

DATA_DIR = "/Users/minikrys/polymarket-monitor/data/atp"

FILES = {
    'ATP_TOUR': [f"{DATA_DIR}/atp_matches_2023.csv", f"{DATA_DIR}/atp_matches_2024.csv"],
    'ATP_CHALL': [f"{DATA_DIR}/atp_matches_qual_chall_2023.csv", f"{DATA_DIR}/atp_matches_qual_chall_2024.csv"],
    'WTA_TOUR': [f"{DATA_DIR}/wta_matches_2023.csv", f"{DATA_DIR}/wta_matches_2024.csv"],
    'WTA_CHALL': [f"{DATA_DIR}/wta_matches_qual_itf_2023.csv", f"{DATA_DIR}/wta_matches_qual_itf_2024.csv"],
}

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
    return min(MAX_ENTRY_PRICE, 0.68 + 0.04 * s1_margin)


# ── CONDITIONS per category, ordered HIGHEST WR first ──
# Each: (label, check_fn)
# check_fn(margin, svs, seed_gap, ratio) → bool

CONDITIONS = {
    'WTA_CHALL': [
        ("seed+S1≥+5",     lambda m, svs, sg, r: svs and m >= 5),
        ("S1=+6",          lambda m, svs, sg, r: m == 6 and not svs),  # only non-seeded +6
        ("seed+S1=+4",     lambda m, svs, sg, r: svs and m == 4),
        ("S1≥+5 (noseed)", lambda m, svs, sg, r: m >= 5 and not svs),
        ("S1=+4 (noseed)", lambda m, svs, sg, r: m == 4 and not svs),
        ("seed+S1=+3",     lambda m, svs, sg, r: svs and m == 3),
        ("S1=+3 (noseed)", lambda m, svs, sg, r: m == 3 and not svs),
    ],
    'ATP_CHALL': [
        ("ratio≥3x+S1≥+4",    lambda m, svs, sg, r: r >= 3 and m >= 4),
        ("ratio≥2x+S1≥+5",    lambda m, svs, sg, r: r >= 2 and m >= 5),
        ("ratio≥2x+S1=+4",    lambda m, svs, sg, r: r >= 2 and m == 4),
        ("seed+S1≥+5",        lambda m, svs, sg, r: svs and m >= 5),
        ("ratio≥2x+S1=+3",    lambda m, svs, sg, r: r >= 2 and m == 3),
        ("seed+S1=+4",        lambda m, svs, sg, r: svs and m == 4),
        ("seed+S1=+3",        lambda m, svs, sg, r: svs and m == 3),
        ("S1≥+5 (noseed)",    lambda m, svs, sg, r: m >= 5),
    ],
    'ATP_TOUR': [
        ("ratio≥5x+S1≥+4",  lambda m, svs, sg, r: r >= 5 and m >= 4),
        ("ratio≥5x+S1=+3",  lambda m, svs, sg, r: r >= 5 and m == 3),
        ("ratio≥3x+S1≥+4",  lambda m, svs, sg, r: r >= 3 and m >= 4),
        ("ratio≥5x+S1=+2",  lambda m, svs, sg, r: r >= 5 and m == 2),
        ("ratio≥3x+S1=+3",  lambda m, svs, sg, r: r >= 3 and m == 3),
        ("ratio≥3x+S1=+2",  lambda m, svs, sg, r: r >= 3 and m == 2),
        ("seed+S1≥+3",      lambda m, svs, sg, r: svs and m >= 3),
        ("ratio≥2x+S1≥+3",  lambda m, svs, sg, r: r >= 2 and m >= 3),
        ("seed+S1=+2",      lambda m, svs, sg, r: svs and m == 2),
        ("ratio≥2x+S1=+2",  lambda m, svs, sg, r: r >= 2 and m == 2),
    ],
    'WTA_TOUR': [
        ("ratio≥5x+S1≥+4",  lambda m, svs, sg, r: r >= 5 and m >= 4),
        ("ratio≥5x+S1=+3",  lambda m, svs, sg, r: r >= 5 and m == 3),
        ("ratio≥2x+S1≥+5",  lambda m, svs, sg, r: r >= 2 and m >= 5),
        ("ratio≥3x+S1=+3",  lambda m, svs, sg, r: r >= 3 and m == 3),
        ("ratio≥3x+S1=+4",  lambda m, svs, sg, r: r >= 3 and m == 4),
        ("seed+S1≥+4",      lambda m, svs, sg, r: svs and m >= 4),
        ("ratio≥2x+S1=+3",  lambda m, svs, sg, r: r >= 2 and m == 3),
        ("seed+S1=+3",      lambda m, svs, sg, r: svs and m == 3),
    ],
}


def load_all_matches():
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
                        'winner_seed': parse_seed(row.get('winner_seed', '')),
                        'loser_seed': parse_seed(row.get('loser_seed', '')),
                        'winner_rank': int(wr) if wr else None,
                        'loser_rank': int(lr) if lr else None,
                        'w_s1': w_s1,
                        'l_s1': l_s1,
                    })
    all_matches.sort(key=lambda m: m['tourney_date'])
    return all_matches


def compute_match_factors(match):
    """Return (margin, is_seeded_vs_unseeded, seed_gap, ratio, s1_winner_won_match)."""
    w_s1, l_s1 = match['w_s1'], match['l_s1']
    match_winner_won_s1 = w_s1 > l_s1

    if match_winner_won_s1:
        s1w_seed, s1l_seed = match['winner_seed'], match['loser_seed']
        s1w_rank, s1l_rank = match['winner_rank'], match['loser_rank']
        margin = w_s1 - l_s1
        won_match = True
    else:
        s1w_seed, s1l_seed = match['loser_seed'], match['winner_seed']
        s1w_rank, s1l_rank = match['loser_rank'], match['winner_rank']
        margin = l_s1 - w_s1
        won_match = False

    svs = (s1w_seed is not None and s1l_seed is None)
    sg = 0
    if s1w_seed is not None and s1l_seed is not None and s1w_seed < s1l_seed:
        sg = s1l_seed - s1w_seed

    ratio = 0
    if s1w_rank and s1l_rank and s1w_rank < s1l_rank:
        ratio = s1l_rank / s1w_rank

    return margin, svs, sg, ratio, won_match


def main():
    matches = load_all_matches()
    print(f"Loaded {len(matches)} matches\n")

    # ── PHASE 1: Compute exclusive WRs ──
    print("=" * 70)
    print("  PHASE 1: EXCLUSIVE WIN RATES (each match → best condition only)")
    print("=" * 70)

    cond_stats = defaultdict(lambda: {'w': 0, 'l': 0})

    for match in matches:
        margin, svs, sg, ratio, won_match = compute_match_factors(match)
        cat = match['category']

        # Find first (best) matching condition
        matched = None
        for label, check_fn in CONDITIONS.get(cat, []):
            if check_fn(margin, svs, sg, ratio):
                matched = f"{cat}: {label}"
                break

        if matched is None:
            continue

        if won_match:
            cond_stats[matched]['w'] += 1
        else:
            cond_stats[matched]['l'] += 1

    # Print exclusive WRs per category
    for cat in ['ATP_TOUR', 'ATP_CHALL', 'WTA_TOUR', 'WTA_CHALL']:
        print(f"\n  {cat}:")
        print(f"  {'Condition':<30} {'W':>5} {'L':>5} {'Total':>6} {'WR%':>7}")
        print(f"  {'-'*57}")
        for label, _ in CONDITIONS.get(cat, []):
            key = f"{cat}: {label}"
            cs = cond_stats.get(key, {'w': 0, 'l': 0})
            t = cs['w'] + cs['l']
            if t == 0:
                continue
            wr = cs['w'] / t * 100
            print(f"  {label:<30} {cs['w']:>5} {cs['l']:>5} {t:>6} {wr:>6.1f}%")
        # Category total
        cat_w = sum(cond_stats.get(f"{cat}: {l}", {'w': 0})['w'] for l, _ in CONDITIONS.get(cat, []))
        cat_l = sum(cond_stats.get(f"{cat}: {l}", {'w': 0, 'l': 0})['l'] for l, _ in CONDITIONS.get(cat, []))
        cat_t = cat_w + cat_l
        if cat_t > 0:
            print(f"  {'TOTAL':<30} {cat_w:>5} {cat_l:>5} {cat_t:>6} {cat_w/cat_t*100:>6.1f}%")

    # ── Build WR lookup from exclusive stats ──
    exclusive_wr = {}
    for key, cs in cond_stats.items():
        t = cs['w'] + cs['l']
        if t >= 20:  # min sample
            exclusive_wr[key] = cs['w'] / t

    # ── PHASE 2: Bankroll simulation with EXCLUSIVE WRs for bet sizing ──
    print(f"\n\n{'=' * 70}")
    print(f"  PHASE 2: BANKROLL SIMULATION ($100 start)")
    print(f"{'=' * 70}")

    # WR-based bet sizing using EXCLUSIVE WRs
    def get_bet(wr):
        if wr >= 0.96:
            return 0.40, 100
        elif wr >= 0.94:
            return 0.25, 75
        elif wr >= 0.92:
            return 0.15, 50
        elif wr >= 0.90:
            return 0.10, 40
        elif wr >= 0.88:
            return 0.06, 25
        elif wr >= 0.85:
            return 0.04, 20
        return None, None

    bankroll = STARTING_BANKROLL
    monthly = OrderedDict()
    cat_pnl = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': 0.0, 'wagered': 0.0})
    cond_pnl = defaultdict(lambda: {'w': 0, 'l': 0, 'pnl': 0.0, 'wagered': 0.0})
    total_w, total_l, total_wag = 0, 0, 0.0

    for match in matches:
        margin, svs, sg, ratio, won_match = compute_match_factors(match)
        cat = match['category']

        matched = None
        for label, check_fn in CONDITIONS.get(cat, []):
            if check_fn(margin, svs, sg, ratio):
                matched = f"{cat}: {label}"
                break

        if matched is None:
            continue

        wr = exclusive_wr.get(matched)
        if wr is None:
            continue

        bet_pct, bet_cap = get_bet(wr)
        if bet_pct is None:
            continue

        bet = min(bankroll * bet_pct, bet_cap)
        if bet < MIN_BET or bankroll < MIN_BET:
            continue

        entry_price = estimate_entry_price(margin)

        if won_match:
            profit = bet * (1.0 / entry_price - 1.0)
            total_w += 1
        else:
            profit = -bet
            total_l += 1

        bankroll += profit
        total_wag += bet

        # Monthly
        td = match['tourney_date']
        month = f"{td[:4]}-{td[4:6]}" if len(td) >= 6 else "?"
        if month not in monthly:
            monthly[month] = {'pnl': 0.0, 'w': 0, 'l': 0, 'wagered': 0.0}
        monthly[month]['pnl'] += profit
        monthly[month]['w' if won_match else 'l'] += 1
        monthly[month]['wagered'] += bet

        # Category
        cat_pnl[cat]['w' if won_match else 'l'] += 1
        cat_pnl[cat]['pnl'] += profit
        cat_pnl[cat]['wagered'] += bet

        # Condition
        cond_pnl[matched]['w' if won_match else 'l'] += 1
        cond_pnl[matched]['pnl'] += profit
        cond_pnl[matched]['wagered'] += bet

    total = total_w + total_l
    print(f"\n  Final bankroll: ${bankroll:.2f}")
    print(f"  Total P&L: ${bankroll - STARTING_BANKROLL:+.2f}")
    print(f"  Total bets: {total} ({total_w}W/{total_l}L = {total_w/total*100:.1f}%)")
    print(f"  Total wagered: ${total_wag:.2f}")
    print(f"  ROI: {(bankroll - STARTING_BANKROLL)/total_wag*100:.2f}%")
    print(f"  Avg bets/month: {total / len(monthly):.0f}")

    # Monthly
    print(f"\n{'─'*75}")
    print(f"  MONTHLY BREAKDOWN")
    print(f"{'─'*75}")
    print(f"  {'Month':<8} {'P&L':>9} {'Bank':>9} {'Bets':>5} {'W':>4} {'L':>4} {'WR%':>6} {'Wagered':>9}")
    print(f"  {'-'*62}")

    running = STARTING_BANKROLL
    for month, d in monthly.items():
        running += d['pnl']
        t = d['w'] + d['l']
        wr_m = d['w'] / t * 100 if t > 0 else 0
        print(f"  {month:<8} ${d['pnl']:>+8.2f} ${running:>8.2f} {t:>5} {d['w']:>4} {d['l']:>4} {wr_m:>5.1f}% ${d['wagered']:>8.2f}")

    # By category
    print(f"\n{'─'*75}")
    print(f"  BY CATEGORY")
    print(f"{'─'*75}")
    print(f"  {'Category':<12} {'W':>5} {'L':>5} {'WR%':>6} {'P&L':>9} {'Wagered':>9} {'ROI%':>7}")
    print(f"  {'-'*58}")
    for cat in ['ATP_TOUR', 'ATP_CHALL', 'WTA_TOUR', 'WTA_CHALL']:
        cs = cat_pnl[cat]
        t = cs['w'] + cs['l']
        if t == 0:
            continue
        wr_c = cs['w'] / t * 100
        roi = cs['pnl'] / cs['wagered'] * 100 if cs['wagered'] else 0
        print(f"  {cat:<12} {cs['w']:>5} {cs['l']:>5} {wr_c:>5.1f}% ${cs['pnl']:>+8.2f} ${cs['wagered']:>8.2f} {roi:>+6.2f}%")

    # By condition
    print(f"\n{'─'*75}")
    print(f"  BY CONDITION (sorted by P&L)")
    print(f"{'─'*75}")
    print(f"  {'Condition':<35} {'W':>4} {'L':>4} {'ExWR%':>6} {'P&L':>9} {'Wagered':>9}")
    print(f"  {'-'*71}")
    for key, cs in sorted(cond_pnl.items(), key=lambda x: -x[1]['pnl']):
        t = cs['w'] + cs['l']
        wr_c = cs['w'] / t * 100
        print(f"  {key:<35} {cs['w']:>4} {cs['l']:>4} {wr_c:>5.1f}% ${cs['pnl']:>+8.2f} ${cs['wagered']:>8.2f}")


if __name__ == '__main__':
    main()
