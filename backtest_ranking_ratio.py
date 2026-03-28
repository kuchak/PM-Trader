#!/usr/bin/env python3
"""
Backtest ranking ratio independently + cross-tabs with S1 margin and seeding.
Completes the picture alongside the seeding and S1 margin backtests.
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


def load_matches(filepath):
    matches = []
    with open(filepath, encoding='utf-8', errors='replace') as f:
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
            matches.append({
                'winner_seed': parse_seed(row.get('winner_seed', '')),
                'loser_seed': parse_seed(row.get('loser_seed', '')),
                'winner_rank': int(wr) if wr else None,
                'loser_rank': int(lr) if lr else None,
                'w_s1': w_s1,
                'l_s1': l_s1,
            })
    return matches


def analyze(all_matches, category):
    print(f"\n{'#'*70}")
    print(f"  {category}: {len(all_matches)} matches")
    print(f"{'#'*70}")

    # ── RANKING RATIO ALONE ──
    print(f"\n  RANKING RATIO ALONE (S1 winner is better-ranked):")
    print(f"  {'Ratio':<20} {'W':>5} {'L':>5} {'Total':>6} {'WR%':>7}")
    print(f"  {'-'*47}")

    ratio_buckets = defaultdict(lambda: {'w': 0, 'l': 0})

    for m in all_matches:
        match_winner_won_s1 = m['w_s1'] > m['l_s1']
        if match_winner_won_s1:
            s1w_rank = m['winner_rank']
            s1l_rank = m['loser_rank']
            won = True
        else:
            s1w_rank = m['loser_rank']
            s1l_rank = m['winner_rank']
            won = False

        if s1w_rank is None or s1l_rank is None:
            continue
        if s1w_rank >= s1l_rank:
            continue  # S1 winner is not better-ranked

        ratio = s1l_rank / s1w_rank

        for label, lo, hi in [('any (>1x)', 1, 999), ('>=1.5x', 1.5, 999),
                               ('>=2x', 2, 999), ('>=3x', 3, 999),
                               ('>=5x', 5, 999), ('>=10x', 10, 999)]:
            if ratio >= lo:
                if won:
                    ratio_buckets[label]['w'] += 1
                else:
                    ratio_buckets[label]['l'] += 1

    for label in ['any (>1x)', '>=1.5x', '>=2x', '>=3x', '>=5x', '>=10x']:
        b = ratio_buckets[label]
        t = b['w'] + b['l']
        if t == 0: continue
        print(f"  {label:<20} {b['w']:>5} {b['l']:>5} {t:>6} {b['w']/t*100:>6.1f}%")

    # ── RANKING RATIO × S1 MARGIN ──
    print(f"\n  RANKING RATIO × S1 MARGIN:")
    print(f"  {'Ratio × Margin':<25} {'W':>5} {'L':>5} {'Total':>6} {'WR%':>7}")
    print(f"  {'-'*52}")

    for ratio_label, ratio_min in [('ratio>=2x', 2), ('ratio>=3x', 3), ('ratio>=5x', 5)]:
        for margin_label, margin_min in [('S1>=+2', 2), ('S1>=+3', 3), ('S1>=+4', 4), ('S1>=+5', 5)]:
            w, l = 0, 0
            for m_ in all_matches:
                mwws1 = m_['w_s1'] > m_['l_s1']
                if mwws1:
                    s1wr, s1lr = m_['winner_rank'], m_['loser_rank']
                    margin = m_['w_s1'] - m_['l_s1']
                    won = True
                else:
                    s1wr, s1lr = m_['loser_rank'], m_['winner_rank']
                    margin = m_['l_s1'] - m_['w_s1']
                    won = False
                if s1wr is None or s1lr is None or s1wr >= s1lr:
                    continue
                ratio = s1lr / s1wr
                if ratio >= ratio_min and margin >= margin_min:
                    if won: w += 1
                    else: l += 1
            t = w + l
            if t == 0: continue
            key = f"{ratio_label} + {margin_label}"
            print(f"  {key:<25} {w:>5} {l:>5} {t:>6} {w/t*100:>6.1f}%")
        print()

    # ── ALL THREE: SEEDING + RANKING + S1 MARGIN ──
    print(f"\n  SEEDED_VS_UNSEEDED + RANKING RATIO + S1 MARGIN:")
    print(f"  {'Condition':<35} {'W':>5} {'L':>5} {'Total':>6} {'WR%':>7}")
    print(f"  {'-'*62}")

    for ratio_label, ratio_min in [('any_ratio', 0), ('ratio>=2x', 2), ('ratio>=3x', 3)]:
        for margin_label, margin_min in [('S1>=+3', 3), ('S1>=+4', 4), ('S1>=+5', 5)]:
            w, l = 0, 0
            for m_ in all_matches:
                mwws1 = m_['w_s1'] > m_['l_s1']
                if mwws1:
                    s1w_seed, s1l_seed = m_['winner_seed'], m_['loser_seed']
                    s1wr, s1lr = m_['winner_rank'], m_['loser_rank']
                    margin = m_['w_s1'] - m_['l_s1']
                    won = True
                else:
                    s1w_seed, s1l_seed = m_['loser_seed'], m_['winner_seed']
                    s1wr, s1lr = m_['loser_rank'], m_['winner_rank']
                    margin = m_['l_s1'] - m_['w_s1']
                    won = False

                # Must be seeded vs unseeded
                if s1w_seed is None or s1l_seed is not None:
                    continue
                if margin < margin_min:
                    continue
                # Ranking ratio filter (if applicable)
                if ratio_min > 0:
                    if s1wr is None or s1lr is None or s1wr >= s1lr:
                        continue
                    if s1lr / s1wr < ratio_min:
                        continue

                if won: w += 1
                else: l += 1
            t = w + l
            if t == 0: continue
            key = f"seed + {ratio_label} + {margin_label}"
            print(f"  {key:<35} {w:>5} {l:>5} {t:>6} {w/t*100:>6.1f}%")
        print()

    # ── NO SEEDING: RANKING RATIO AS FALLBACK + S1 MARGIN ──
    print(f"\n  NO SEED INFO + RANKING RATIO + S1 MARGIN (fallback scenario):")
    print(f"  {'Condition':<35} {'W':>5} {'L':>5} {'Total':>6} {'WR%':>7}")
    print(f"  {'-'*62}")

    for ratio_label, ratio_min in [('ratio>=2x', 2), ('ratio>=3x', 3), ('ratio>=5x', 5)]:
        for margin_label, margin_min in [('S1>=+3', 3), ('S1>=+4', 4), ('S1>=+5', 5)]:
            w, l = 0, 0
            for m_ in all_matches:
                mwws1 = m_['w_s1'] > m_['l_s1']
                if mwws1:
                    s1w_seed, s1l_seed = m_['winner_seed'], m_['loser_seed']
                    s1wr, s1lr = m_['winner_rank'], m_['loser_rank']
                    margin = m_['w_s1'] - m_['l_s1']
                    won = True
                else:
                    s1w_seed, s1l_seed = m_['loser_seed'], m_['winner_seed']
                    s1wr, s1lr = m_['loser_rank'], m_['winner_rank']
                    margin = m_['l_s1'] - m_['w_s1']
                    won = False

                # Neither player seeded
                if s1w_seed is not None or s1l_seed is not None:
                    continue
                if margin < margin_min:
                    continue
                if s1wr is None or s1lr is None or s1wr >= s1lr:
                    continue
                if s1lr / s1wr < ratio_min:
                    continue

                if won: w += 1
                else: l += 1
            t = w + l
            if t == 0: continue
            key = f"noseed + {ratio_label} + {margin_label}"
            print(f"  {key:<35} {w:>5} {l:>5} {t:>6} {w/t*100:>6.1f}%")
        print()


def main():
    for category, files in FILES.items():
        all_matches = []
        for fp in files:
            all_matches.extend(load_matches(fp))
        analyze(all_matches, category)


if __name__ == '__main__':
    main()
