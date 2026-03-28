#!/usr/bin/env python3
"""
Two independent backtests on JeffSackmann tennis data (2023-2024):
1. Seeding gap alone — does tournament seed difference predict match winner after S1?
2. S1 margin alone — does Set 1 game margin predict match winner after S1?

For each, we ask: if the S1 winner has trait X, what % of the time do they win the match?
This simulates our bot's decision point: S1 is over, should we bet on the S1 winner?
"""

import csv
import re
import os
from collections import defaultdict

DATA_DIR = "/Users/minikrys/polymarket-monitor/data/atp"

FILES = {
    'ATP_TOUR': [f"{DATA_DIR}/atp_matches_2023.csv", f"{DATA_DIR}/atp_matches_2024.csv"],
    'ATP_CHALL': [f"{DATA_DIR}/atp_matches_qual_chall_2023.csv", f"{DATA_DIR}/atp_matches_qual_chall_2024.csv"],
    'WTA_TOUR': [f"{DATA_DIR}/wta_matches_2023.csv", f"{DATA_DIR}/wta_matches_2024.csv"],
    'WTA_CHALL': [f"{DATA_DIR}/wta_matches_qual_itf_2023.csv", f"{DATA_DIR}/wta_matches_qual_itf_2024.csv"],
}


def parse_s1(score_str):
    """Parse Set 1 from score string like '6-3 7-6(5) 6-4'.
    Returns (winner_s1_games, loser_s1_games) or None if unparseable.
    'winner' and 'loser' refer to the MATCH winner/loser (as recorded in the CSV)."""
    if not score_str or score_str.strip() in ('', 'W/O', 'RET', 'DEF', 'Walkover'):
        return None
    # Handle retirements mid-score
    score_str = re.sub(r'\s*(RET|ABN|DEF|W/O|Walkover|Default|Abandoned).*', '', score_str, flags=re.IGNORECASE)
    sets = score_str.strip().split()
    if not sets:
        return None
    s1 = sets[0]
    # Handle tiebreak notation: 7-6(5) or 6-7(3)
    m = re.match(r'(\d+)-(\d+)', s1)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_seed(seed_val):
    """Parse seed value — could be number, empty, or have trailing characters."""
    if not seed_val or not seed_val.strip():
        return None
    # Remove trailing characters like 'WC', 'Q', 'LL', 'SE', 'PR', 'ALT'
    num = re.match(r'(\d+)', seed_val.strip())
    if num:
        return int(num.group(1))
    return None


def load_matches(filepath):
    """Load matches from a JeffSackmann CSV file."""
    matches = []
    with open(filepath, encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for row in reader:
            score = row.get('score', '')
            s1 = parse_s1(score)
            if s1 is None:
                continue
            w_s1, l_s1 = s1
            if w_s1 == l_s1:
                continue  # incomplete S1

            matches.append({
                'tourney_name': row.get('tourney_name', ''),
                'tourney_level': row.get('tourney_level', ''),
                'round': row.get('round', ''),
                'winner_name': row.get('winner_name', ''),
                'loser_name': row.get('loser_name', ''),
                'winner_seed': parse_seed(row.get('winner_seed', '')),
                'loser_seed': parse_seed(row.get('loser_seed', '')),
                'winner_rank': int(row['winner_rank']) if row.get('winner_rank', '').strip() else None,
                'loser_rank': int(row['loser_rank']) if row.get('loser_rank', '').strip() else None,
                'w_s1': w_s1,
                'l_s1': l_s1,
                'score': score,
            })
    return matches


def backtest_seeding_gap(all_matches, category):
    """Backtest 1: Seeding gap alone.
    After S1, if the S1 winner is also the better seed, what's their match win rate?
    Broken down by: seeded vs unseeded, and seed gap buckets."""

    print(f"\n{'='*70}")
    print(f"BACKTEST 1: SEEDING GAP ALONE — {category}")
    print(f"{'='*70}")

    # Buckets: (description, filter_fn)
    buckets = defaultdict(lambda: {'wins': 0, 'losses': 0})

    for m in all_matches:
        w_s1, l_s1 = m['w_s1'], m['l_s1']

        # Determine S1 winner (match-winner won S1, or match-loser won S1)
        match_winner_won_s1 = w_s1 > l_s1

        # Get seeds for S1 winner and S1 loser
        if match_winner_won_s1:
            s1_winner_seed = m['winner_seed']
            s1_loser_seed = m['loser_seed']
            s1_winner_won_match = True
        else:
            s1_winner_seed = m['loser_seed']
            s1_loser_seed = m['winner_seed']
            s1_winner_won_match = False

        # --- Scenario A: S1 winner is seeded, S1 loser is unseeded ---
        if s1_winner_seed is not None and s1_loser_seed is None:
            key = 'seeded_vs_unseeded'
            if s1_winner_won_match:
                buckets[key]['wins'] += 1
            else:
                buckets[key]['losses'] += 1

        # --- Scenario B: Both seeded, S1 winner has better (lower) seed ---
        if s1_winner_seed is not None and s1_loser_seed is not None:
            if s1_winner_seed < s1_loser_seed:
                gap = s1_loser_seed - s1_winner_seed
                # Bucket by gap ranges
                if gap >= 10:
                    gkey = 'both_seeded_gap_10+'
                elif gap >= 5:
                    gkey = 'both_seeded_gap_5-9'
                elif gap >= 3:
                    gkey = 'both_seeded_gap_3-4'
                else:
                    gkey = 'both_seeded_gap_1-2'
                if s1_winner_won_match:
                    buckets[gkey]['wins'] += 1
                else:
                    buckets[gkey]['losses'] += 1

                # Also track "better seed won S1" overall
                if s1_winner_won_match:
                    buckets['better_seed_won_s1']['wins'] += 1
                else:
                    buckets['better_seed_won_s1']['losses'] += 1

            elif s1_winner_seed > s1_loser_seed:
                # S1 winner is the WORSE seed (upset in S1)
                if s1_winner_won_match:
                    buckets['worse_seed_won_s1']['wins'] += 1
                else:
                    buckets['worse_seed_won_s1']['losses'] += 1

        # --- Scenario C: S1 winner is unseeded, S1 loser is seeded (upset) ---
        if s1_winner_seed is None and s1_loser_seed is not None:
            key = 'unseeded_beat_seeded_s1'
            if s1_winner_won_match:
                buckets[key]['wins'] += 1
            else:
                buckets[key]['losses'] += 1

        # --- Overall: any seeding info available ---
        if s1_winner_seed is not None or s1_loser_seed is not None:
            if s1_winner_won_match:
                buckets['any_seed_info']['wins'] += 1
            else:
                buckets['any_seed_info']['losses'] += 1

        # Track all S1 winners regardless
        if s1_winner_won_match:
            buckets['all_s1_winners']['wins'] += 1
        else:
            buckets['all_s1_winners']['losses'] += 1

    print(f"\n{'Scenario':<35} {'W':>5} {'L':>5} {'Total':>6} {'WR%':>7}")
    print("-" * 62)
    order = [
        'all_s1_winners',
        'seeded_vs_unseeded',
        'better_seed_won_s1',
        'both_seeded_gap_10+',
        'both_seeded_gap_5-9',
        'both_seeded_gap_3-4',
        'both_seeded_gap_1-2',
        'worse_seed_won_s1',
        'unseeded_beat_seeded_s1',
    ]
    for key in order:
        b = buckets.get(key, {'wins': 0, 'losses': 0})
        total = b['wins'] + b['losses']
        if total == 0:
            continue
        wr = b['wins'] / total * 100
        print(f"  {key:<33} {b['wins']:>5} {b['losses']:>5} {total:>6} {wr:>6.1f}%")


def backtest_s1_margin(all_matches, category):
    """Backtest 2: S1 margin alone.
    After S1, what's the S1 winner's match win rate by S1 margin?"""

    print(f"\n{'='*70}")
    print(f"BACKTEST 2: S1 MARGIN ALONE — {category}")
    print(f"{'='*70}")

    margin_buckets = defaultdict(lambda: {'wins': 0, 'losses': 0})

    for m in all_matches:
        w_s1, l_s1 = m['w_s1'], m['l_s1']
        match_winner_won_s1 = w_s1 > l_s1

        if match_winner_won_s1:
            margin = w_s1 - l_s1
            s1_winner_won_match = True
        else:
            margin = l_s1 - w_s1
            s1_winner_won_match = False

        key = f'+{margin}'
        if s1_winner_won_match:
            margin_buckets[key]['wins'] += 1
        else:
            margin_buckets[key]['losses'] += 1

        # Cumulative buckets (>=N)
        for threshold in [2, 3, 4, 5]:
            if margin >= threshold:
                ckey = f'>={threshold}'
                if s1_winner_won_match:
                    margin_buckets[ckey]['wins'] += 1
                else:
                    margin_buckets[ckey]['losses'] += 1

    print(f"\n{'S1 Margin':<20} {'W':>5} {'L':>5} {'Total':>6} {'WR%':>7}")
    print("-" * 47)

    # Exact margins
    for mg in ['+1', '+2', '+3', '+4', '+5', '+6']:
        b = margin_buckets.get(mg, {'wins': 0, 'losses': 0})
        total = b['wins'] + b['losses']
        if total == 0:
            continue
        wr = b['wins'] / total * 100
        print(f"  {mg:<18} {b['wins']:>5} {b['losses']:>5} {total:>6} {wr:>6.1f}%")

    print("-" * 47)
    # Cumulative
    for thr in ['>=2', '>=3', '>=4', '>=5']:
        b = margin_buckets.get(thr, {'wins': 0, 'losses': 0})
        total = b['wins'] + b['losses']
        if total == 0:
            continue
        wr = b['wins'] / total * 100
        print(f"  {thr:<18} {b['wins']:>5} {b['losses']:>5} {total:>6} {wr:>6.1f}%")


def backtest_seeding_by_margin(all_matches, category):
    """Cross-tab: seed scenarios broken down by S1 margin."""

    print(f"\n{'='*70}")
    print(f"CROSS-TAB: SEEDING × S1 MARGIN — {category}")
    print(f"{'='*70}")

    # For "seeded vs unseeded" + "better seed won S1", break down by S1 margin
    scenarios = {
        'seeded_vs_unseeded': [],
        'better_seed_gap>=2': [],
        'better_seed_gap>=5': [],
    }

    for m in all_matches:
        w_s1, l_s1 = m['w_s1'], m['l_s1']
        match_winner_won_s1 = w_s1 > l_s1

        if match_winner_won_s1:
            s1_winner_seed = m['winner_seed']
            s1_loser_seed = m['loser_seed']
            margin = w_s1 - l_s1
            won_match = True
        else:
            s1_winner_seed = m['loser_seed']
            s1_loser_seed = m['winner_seed']
            margin = l_s1 - w_s1
            won_match = False

        rec = {'margin': margin, 'won': won_match}

        if s1_winner_seed is not None and s1_loser_seed is None:
            scenarios['seeded_vs_unseeded'].append(rec)

        if s1_winner_seed is not None and s1_loser_seed is not None:
            if s1_winner_seed < s1_loser_seed:
                gap = s1_loser_seed - s1_winner_seed
                if gap >= 2:
                    scenarios['better_seed_gap>=2'].append(rec)
                if gap >= 5:
                    scenarios['better_seed_gap>=5'].append(rec)

    for scenario_name, records in scenarios.items():
        if not records:
            continue
        print(f"\n  {scenario_name}:")
        print(f"  {'S1 Margin':<15} {'W':>5} {'L':>5} {'Total':>6} {'WR%':>7}")
        print(f"  {'-'*42}")
        for mg in [1, 2, 3, 4, 5, 6]:
            w = sum(1 for r in records if r['margin'] == mg and r['won'])
            l = sum(1 for r in records if r['margin'] == mg and not r['won'])
            total = w + l
            if total == 0:
                continue
            wr = w / total * 100
            print(f"  +{mg:<14} {w:>5} {l:>5} {total:>6} {wr:>6.1f}%")
        # Cumulative
        for thr in [3, 4, 5]:
            w = sum(1 for r in records if r['margin'] >= thr and r['won'])
            l = sum(1 for r in records if r['margin'] >= thr and not r['won'])
            total = w + l
            if total == 0:
                continue
            wr = w / total * 100
            print(f"  >={thr:<13} {w:>5} {l:>5} {total:>6} {wr:>6.1f}%")


def main():
    for category, files in FILES.items():
        all_matches = []
        for fp in files:
            all_matches.extend(load_matches(fp))

        n_with_seeds = sum(1 for m in all_matches if m['winner_seed'] or m['loser_seed'])
        print(f"\n{'#'*70}")
        print(f"  {category}: {len(all_matches)} matches loaded, {n_with_seeds} have seed info")
        print(f"{'#'*70}")

        backtest_seeding_gap(all_matches, category)
        backtest_s1_margin(all_matches, category)
        backtest_seeding_by_margin(all_matches, category)

    # Also run combined across all categories
    all_combined = []
    for category, files in FILES.items():
        for fp in files:
            all_combined.extend(load_matches(fp))
    print(f"\n{'#'*70}")
    print(f"  ALL COMBINED: {len(all_combined)} matches")
    print(f"{'#'*70}")
    backtest_seeding_gap(all_combined, 'ALL')
    backtest_s1_margin(all_combined, 'ALL')
    backtest_seeding_by_margin(all_combined, 'ALL')


if __name__ == '__main__':
    main()
