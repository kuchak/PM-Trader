#!/usr/bin/env python3
"""ATP tournament seed & draw position analysis: find sweet spots within tournaments."""
import csv
from collections import defaultdict

DATA_DIR = "data/atp"
TOUR_FILES = [f"{DATA_DIR}/atp_matches_2023.csv", f"{DATA_DIR}/atp_matches_2024.csv"]
CHALL_FILES = [f"{DATA_DIR}/atp_matches_qual_chall_2023.csv", f"{DATA_DIR}/atp_matches_qual_chall_2024.csv"]


def load_matches(files):
    matches = []
    for fpath in files:
        with open(fpath) as f:
            for row in csv.DictReader(f):
                try:
                    w_rank = int(row["winner_rank"]) if row["winner_rank"] else None
                    l_rank = int(row["loser_rank"]) if row["loser_rank"] else None
                except (ValueError, KeyError):
                    w_rank = l_rank = None

                w_seed = row.get("winner_seed", "").strip()
                l_seed = row.get("loser_seed", "").strip()
                w_entry = row.get("winner_entry", "").strip()
                l_entry = row.get("loser_entry", "").strip()

                matches.append({
                    "tourney": row["tourney_name"],
                    "tourney_id": row["tourney_id"],
                    "level": row.get("tourney_level", "?"),
                    "draw_size": int(row["draw_size"]) if row.get("draw_size", "").isdigit() else 0,
                    "round": row.get("round", ""),
                    "surface": row.get("surface", ""),
                    "winner": row["winner_name"],
                    "loser": row["loser_name"],
                    "w_rank": w_rank,
                    "l_rank": l_rank,
                    "w_seed": int(w_seed) if w_seed.isdigit() else None,
                    "l_seed": int(l_seed) if l_seed.isdigit() else None,
                    "w_entry": w_entry,  # WC=wildcard, Q=qualifier, LL=lucky loser, PR=protected ranking
                    "l_entry": l_entry,
                    "score": row.get("score", ""),
                })
    return matches


def seed_vs_position(matches, label):
    """Analyze seeded players vs their draw position opponents."""
    print(f"\n{'='*100}")
    print(f"  {label}")
    print(f"{'='*100}")

    # 1. Top seed tiers vs bottom seed tiers
    # In a 32-draw: seeds 1-8 are the "top", 9-16 are mid, unseeded are bottom
    # In a 128-draw (Slams): seeds 1-8 top, 9-16 mid, 17-32 lower seeds

    # Seeded vs unseeded by seed tier
    print(f"\n  SEEDED PLAYER WIN RATE BY SEED TIER (vs unseeded opponents)")
    print(f"  {'Seed':>10} {'n':>5} {'Win%':>6} {'vs Q/WC':>8} {'vs main draw':>13}")
    print(f"  {'-'*50}")

    for s_lo, s_hi, s_label in [(1, 1, "1"), (2, 2, "2"), (3, 4, "3-4"),
                                 (5, 8, "5-8"), (9, 16, "9-16"), (17, 32, "17-32")]:
        # Seeded player vs unseeded
        seed_wins = []
        vs_qualifier = []
        vs_main = []
        for m in matches:
            if m["w_seed"] and s_lo <= m["w_seed"] <= s_hi and not m["l_seed"]:
                seed_wins.append(True)
                if m["l_entry"] in ("Q", "WC", "LL"):
                    vs_qualifier.append(True)
                else:
                    vs_main.append(True)
            elif m["l_seed"] and s_lo <= m["l_seed"] <= s_hi and not m["w_seed"]:
                seed_wins.append(False)
                if m["w_entry"] in ("Q", "WC", "LL"):
                    vs_qualifier.append(False)
                else:
                    vs_main.append(False)

        if len(seed_wins) < 5:
            continue
        wr = sum(seed_wins) / len(seed_wins) * 100
        q_wr = f"{sum(vs_qualifier)/len(vs_qualifier)*100:.0f}%" if len(vs_qualifier) >= 5 else "  --"
        m_wr = f"{sum(vs_main)/len(vs_main)*100:.0f}%" if len(vs_main) >= 5 else "  --"
        print(f"  {s_label:>10} {len(seed_wins):5d} {wr:5.1f}% {q_wr:>8} {m_wr:>13}")

    # 2. Within-tournament rank position analysis
    # For each tournament, rank all players by their world ranking
    # Then compute: top-N vs bottom-N win rates
    print(f"\n  WITHIN-TOURNAMENT POSITION (ranked by world ranking within each draw)")

    # Group matches by tournament
    tourneys = defaultdict(list)
    for m in matches:
        if m["w_rank"] and m["l_rank"]:
            tourneys[m["tourney_id"]].append(m)

    # For each tournament, get all players and their ranks
    position_records = []
    for tid, t_matches in tourneys.items():
        # Collect all unique players with rankings
        players = {}
        for m in t_matches:
            players[m["winner"]] = m["w_rank"]
            players[m["loser"]] = m["l_rank"]

        if len(players) < 8:
            continue

        # Sort by rank (ascending = best)
        sorted_players = sorted(players.items(), key=lambda x: x[1])
        n = len(sorted_players)

        # Assign position (1 = best ranked in tourney, N = worst)
        pos_map = {name: i+1 for i, (name, _) in enumerate(sorted_players)}

        for m in t_matches:
            w_pos = pos_map.get(m["winner"])
            l_pos = pos_map.get(m["loser"])
            if w_pos and l_pos:
                position_records.append({
                    "w_pos": w_pos, "l_pos": l_pos,
                    "n_players": n,
                    "w_rank": m["w_rank"], "l_rank": m["l_rank"],
                    "round": m["round"],
                    "tourney": m["tourney"],
                    "draw_size": m["draw_size"],
                    "level": m["level"],
                })

    print(f"\n  Analyzed {len(position_records)} matches across {len(tourneys)} tournaments")

    # Top N vs Bottom N analysis
    print(f"\n  TOP-N vs BOTTOM-N IN DRAW (by world ranking within tournament)")
    print(f"  {'Matchup':>30} {'n':>5} {'Top wins':>9} {'Top WR':>7}")
    print(f"  {'-'*55}")

    matchup_defs = [
        ("Top 1 vs Bottom half", lambda r: r["w_pos"] == 1 or r["l_pos"] == 1,
         lambda r, w_top: True,  # always include seed 1
         lambda r: (r["w_pos"] == 1 and r["l_pos"] > r["n_players"] // 2) or
                   (r["l_pos"] == 1 and r["w_pos"] > r["n_players"] // 2)),
        ("Top 2 vs Bottom half", None, None,
         lambda r: (min(r["w_pos"], r["l_pos"]) <= 2 and max(r["w_pos"], r["l_pos"]) > r["n_players"] // 2)),
        ("Top 4 vs Bottom half", None, None,
         lambda r: (min(r["w_pos"], r["l_pos"]) <= 4 and max(r["w_pos"], r["l_pos"]) > r["n_players"] // 2)),
        ("Top 4 vs Bottom quarter", None, None,
         lambda r: (min(r["w_pos"], r["l_pos"]) <= 4 and max(r["w_pos"], r["l_pos"]) > r["n_players"] * 3 // 4)),
        ("Top 8 vs Bottom half", None, None,
         lambda r: (min(r["w_pos"], r["l_pos"]) <= 8 and max(r["w_pos"], r["l_pos"]) > r["n_players"] // 2)),
        ("Top 8 vs Bottom quarter", None, None,
         lambda r: (min(r["w_pos"], r["l_pos"]) <= 8 and max(r["w_pos"], r["l_pos"]) > r["n_players"] * 3 // 4)),
        ("Top 10 vs Bottom 30%", None, None,
         lambda r: (min(r["w_pos"], r["l_pos"]) <= 10 and max(r["w_pos"], r["l_pos"]) > r["n_players"] * 0.7)),
        ("Top quarter vs Bottom quarter", None, None,
         lambda r: (min(r["w_pos"], r["l_pos"]) <= r["n_players"] // 4 and
                    max(r["w_pos"], r["l_pos"]) > r["n_players"] * 3 // 4)),
    ]

    for label_m, _, _, filter_fn in matchup_defs:
        subset = [r for r in position_records if filter_fn(r)]
        if len(subset) < 10:
            continue
        # "Top" = the lower-positioned (better-ranked) player
        top_wins = sum(1 for r in subset if min(r["w_pos"], r["l_pos"]) == r["w_pos"])
        wr = top_wins / len(subset) * 100
        print(f"  {label_m:>30} {len(subset):5d} {top_wins:9d} {wr:6.1f}%")

    # 3. Percentile-based analysis (more granular)
    print(f"\n  PERCENTILE-BASED: favorite's position in draw (as % of field)")
    print(f"  {'Fav %ile':>10} {'Dog %ile':>10} {'n':>5} {'Fav WR':>7}")
    print(f"  {'-'*40}")

    for fav_lo, fav_hi, fav_label in [(0, 10, "Top 10%"), (0, 15, "Top 15%"), (0, 20, "Top 20%"),
                                       (0, 25, "Top 25%"), (20, 40, "20-40%"), (40, 60, "40-60%")]:
        for dog_lo, dog_hi, dog_label in [(50, 75, "50-75%"), (60, 80, "60-80%"),
                                           (70, 90, "70-90%"), (75, 100, "75-100%"),
                                           (80, 100, "80-100%"), (90, 100, "90-100%")]:
            if fav_hi >= dog_lo:
                continue
            subset = []
            for r in position_records:
                fav_pctile = min(r["w_pos"], r["l_pos"]) / r["n_players"] * 100
                dog_pctile = max(r["w_pos"], r["l_pos"]) / r["n_players"] * 100
                if fav_lo <= fav_pctile < fav_hi and dog_lo <= dog_pctile <= dog_hi:
                    fav_won = min(r["w_pos"], r["l_pos"]) == r["w_pos"]
                    subset.append(fav_won)
            if len(subset) < 15:
                continue
            wr = sum(subset) / len(subset) * 100
            if wr >= 75:  # only show strong spots
                print(f"  {fav_label:>10} {dog_label:>10} {len(subset):5d} {wr:6.1f}%")

    # 4. Grand Slam specific (128 draws have more data)
    print(f"\n  GRAND SLAM DRAWS (128 players, 32 seeds)")
    slam_recs = [r for r in position_records if r["level"] == "G"]
    print(f"  {len(slam_recs)} Grand Slam matches")

    if slam_recs:
        print(f"\n  {'Matchup':>35} {'n':>5} {'Fav WR':>7}")
        print(f"  {'-'*50}")

        slam_matchups = [
            ("Seed 1-4 vs unseeded (33+)", lambda r: min(r["w_pos"], r["l_pos"]) <= 4 and max(r["w_pos"], r["l_pos"]) > 32),
            ("Seed 1-8 vs unseeded (33+)", lambda r: min(r["w_pos"], r["l_pos"]) <= 8 and max(r["w_pos"], r["l_pos"]) > 32),
            ("Seed 1-8 vs seed 25-32", lambda r: min(r["w_pos"], r["l_pos"]) <= 8 and 25 <= max(r["w_pos"], r["l_pos"]) <= 32),
            ("Seed 1-8 vs pos 60-128", lambda r: min(r["w_pos"], r["l_pos"]) <= 8 and max(r["w_pos"], r["l_pos"]) >= 60),
            ("Seed 1-4 vs pos 80-128", lambda r: min(r["w_pos"], r["l_pos"]) <= 4 and max(r["w_pos"], r["l_pos"]) >= 80),
            ("Seed 1-4 vs pos 100-128", lambda r: min(r["w_pos"], r["l_pos"]) <= 4 and max(r["w_pos"], r["l_pos"]) >= 100),
            ("Seed 9-16 vs unseeded (33+)", lambda r: 9 <= min(r["w_pos"], r["l_pos"]) <= 16 and max(r["w_pos"], r["l_pos"]) > 32),
            ("Seed 17-32 vs unseeded (33+)", lambda r: 17 <= min(r["w_pos"], r["l_pos"]) <= 32 and max(r["w_pos"], r["l_pos"]) > 32),
            ("Pos 1-10 vs pos 100+", lambda r: min(r["w_pos"], r["l_pos"]) <= 10 and max(r["w_pos"], r["l_pos"]) >= 100),
        ]

        for label_m, fn in slam_matchups:
            subset = [r for r in slam_recs if fn(r)]
            if len(subset) < 5:
                continue
            top_wins = sum(1 for r in subset if min(r["w_pos"], r["l_pos"]) == r["w_pos"])
            wr = top_wins / len(subset) * 100
            marker = " <<<" if wr >= 85 else ""
            print(f"  {label_m:>35} {len(subset):5d} {wr:6.1f}%{marker}")

    # 5. By round — does the sweet spot change as tournament progresses?
    print(f"\n  FAVORITE (by rank) WIN RATE BY ROUND + RANK GAP RATIO")
    print(f"  {'Round':>6} {'Ratio':>10} {'n':>5} {'Fav WR':>7}")
    print(f"  {'-'*35}")

    for rnd in ["R128", "R64", "R32", "R16", "QF", "SF", "F"]:
        rnd_recs = [r for r in position_records if r["round"] == rnd]
        if len(rnd_recs) < 20:
            continue
        for ratio_lo, ratio_hi, r_label in [(1.0, 2.0, "1-2x"), (2.0, 4.0, "2-4x"),
                                              (4.0, 8.0, "4-8x"), (8.0, 999, "8x+")]:
            subset = []
            for r in rnd_recs:
                ratio = max(r["w_rank"], r["l_rank"]) / min(r["w_rank"], r["l_rank"]) if min(r["w_rank"], r["l_rank"]) > 0 else 0
                if ratio_lo <= ratio < ratio_hi:
                    fav_won = r["w_rank"] < r["l_rank"]
                    subset.append(fav_won)
            if len(subset) < 10:
                continue
            wr = sum(subset) / len(subset) * 100
            print(f"  {rnd:>6} {r_label:>10} {len(subset):5d} {wr:6.1f}%")

    # 6. Qualifier/Wildcard analysis
    print(f"\n  QUALIFIER & WILDCARD PERFORMANCE")
    print(f"  {'Entry type':>12} {'n':>5} {'Win%':>6} {'vs seeded':>10} {'vs unseeded':>12}")
    print(f"  {'-'*50}")

    for entry_type, e_label in [("Q", "Qualifier"), ("WC", "Wildcard"), ("LL", "Lucky Loser")]:
        wins_total = []
        vs_seeded = []
        vs_unseeded = []
        for m in matches:
            if m["w_entry"] == entry_type:
                wins_total.append(True)
                if m["l_seed"]:
                    vs_seeded.append(True)
                else:
                    vs_unseeded.append(True)
            elif m["l_entry"] == entry_type:
                wins_total.append(False)
                if m["w_seed"]:
                    vs_seeded.append(False)
                else:
                    vs_unseeded.append(False)

        if len(wins_total) < 5:
            continue
        wr = sum(wins_total) / len(wins_total) * 100
        s_wr = f"{sum(vs_seeded)/len(vs_seeded)*100:.0f}%" if len(vs_seeded) >= 5 else " --"
        u_wr = f"{sum(vs_unseeded)/len(vs_unseeded)*100:.0f}%" if len(vs_unseeded) >= 5 else " --"
        print(f"  {e_label:>12} {len(wins_total):5d} {wr:5.1f}% {s_wr:>10} {u_wr:>12}")

    # 7. THE SWEET SPOT FINDER — exhaustive search for conditions with WR >= 85% and n >= 15
    print(f"\n{'='*100}")
    print(f"  SWEET SPOT FINDER: All conditions with WR >= 80% and n >= 15")
    print(f"{'='*100}")

    sweet_spots = []

    # By seed vs rank position combinations
    for s_lo, s_hi, s_label in [(1, 1, "Seed 1"), (1, 2, "Seed 1-2"), (1, 4, "Seed 1-4"),
                                 (1, 8, "Seed 1-8"), (1, 16, "Seed 1-16")]:
        for opp_type, opp_label, opp_filter in [
            ("rank", "Opp rank 50+", lambda m, role: (m[f"{role}_rank"] or 0) >= 50),
            ("rank", "Opp rank 75+", lambda m, role: (m[f"{role}_rank"] or 0) >= 75),
            ("rank", "Opp rank 100+", lambda m, role: (m[f"{role}_rank"] or 0) >= 100),
            ("rank", "Opp rank 150+", lambda m, role: (m[f"{role}_rank"] or 0) >= 150),
            ("rank", "Opp rank 200+", lambda m, role: (m[f"{role}_rank"] or 0) >= 200),
            ("entry", "Opp is Q/WC/LL", lambda m, role: m[f"{role}_entry"] in ("Q", "WC", "LL")),
            ("unseed", "Opp unseeded", lambda m, role: m[f"{role}_seed"] is None),
        ]:
            results = []
            for m in matches:
                # Seed is winner
                if m["w_seed"] and s_lo <= m["w_seed"] <= s_hi:
                    if opp_filter(m, "l"):
                        results.append(True)
                # Seed is loser
                elif m["l_seed"] and s_lo <= m["l_seed"] <= s_hi:
                    if opp_filter(m, "w"):
                        results.append(False)

            if len(results) >= 15:
                wr = sum(results) / len(results) * 100
                if wr >= 80:
                    sweet_spots.append((wr, len(results), f"{s_label} vs {opp_label}"))

    # By world rank tiers
    for r_lo, r_hi, r_label in [(1, 3, "Rank 1-3"), (1, 5, "Rank 1-5"),
                                 (1, 10, "Rank 1-10"), (1, 20, "Rank 1-20")]:
        for opp_lo, opp_hi, o_label in [(30, 50, "vs rank 30-50"), (50, 100, "vs rank 50-100"),
                                          (50, 200, "vs rank 50-200"), (100, 500, "vs rank 100-500"),
                                          (100, 9999, "vs rank 100+"), (150, 9999, "vs rank 150+"),
                                          (200, 9999, "vs rank 200+")]:
            results = []
            for m in matches:
                fav_rank = min(m["w_rank"] or 9999, m["l_rank"] or 9999)
                dog_rank = max(m["w_rank"] or 0, m["l_rank"] or 0)
                if r_lo <= fav_rank <= r_hi and opp_lo <= dog_rank <= opp_hi:
                    results.append(m["w_rank"] < m["l_rank"])  # fav won

            if len(results) >= 15:
                wr = sum(results) / len(results) * 100
                if wr >= 80:
                    sweet_spots.append((wr, len(results), f"{r_label} {o_label}"))

    # Sort by WR descending
    sweet_spots.sort(key=lambda x: (-x[0], -x[1]))
    print(f"\n  {'WR':>6} {'n':>5}  Condition")
    print(f"  {'-'*60}")
    seen = set()
    for wr, n, cond in sweet_spots:
        if cond not in seen:
            seen.add(cond)
            print(f"  {wr:5.1f}% {n:5d}  {cond}")


def main():
    tour = load_matches(TOUR_FILES)
    tour = [m for m in tour if m["level"] in ("G", "M", "A", "F")]
    chall = load_matches(CHALL_FILES)
    chall = [m for m in chall if m["level"] == "C"]

    print(f"Tour: {len(tour)} matches | Challenger: {len(chall)} matches (2023-2024)\n")

    seed_vs_position(tour, "ATP TOUR — SEED & DRAW POSITION ANALYSIS")
    seed_vs_position(chall, "ATP CHALLENGER — SEED & DRAW POSITION ANALYSIS")


if __name__ == "__main__":
    main()
