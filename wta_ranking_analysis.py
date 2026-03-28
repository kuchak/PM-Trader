#!/usr/bin/env python3
"""WTA ranking gap analysis: find sweet spots for betting on favorites by ranking gap."""
import csv
from collections import defaultdict

DATA_DIR = "data/atp"
TOUR_FILES = [f"{DATA_DIR}/wta_matches_2023.csv", f"{DATA_DIR}/wta_matches_2024.csv"]
# ITF/qual file has challengers mixed in — filter by level
CHALL_ITF_FILES = [f"{DATA_DIR}/wta_matches_qual_itf_2023.csv", f"{DATA_DIR}/wta_matches_qual_itf_2024.csv"]


def load_matches(files, level_filter=None):
    matches = []
    for fpath in files:
        with open(fpath) as f:
            for row in csv.DictReader(f):
                level = row.get("tourney_level", "?")
                if level_filter and level not in level_filter:
                    continue
                try:
                    w_rank = int(row["winner_rank"]) if row.get("winner_rank") else None
                    l_rank = int(row["loser_rank"]) if row.get("loser_rank") else None
                except (ValueError, KeyError):
                    w_rank = l_rank = None
                if w_rank is None or l_rank is None:
                    continue

                w_seed = row.get("winner_seed", "").strip()
                l_seed = row.get("loser_seed", "").strip()
                w_entry = row.get("winner_entry", "").strip()
                l_entry = row.get("loser_entry", "").strip()

                matches.append({
                    "tourney": row["tourney_name"],
                    "tourney_id": row["tourney_id"],
                    "level": level,
                    "draw_size": int(row["draw_size"]) if row.get("draw_size", "").isdigit() else 0,
                    "round": row.get("round", ""),
                    "surface": row.get("surface", ""),
                    "winner": row["winner_name"],
                    "loser": row["loser_name"],
                    "w_rank": w_rank,
                    "l_rank": l_rank,
                    "w_seed": int(w_seed) if w_seed.isdigit() else None,
                    "l_seed": int(l_seed) if l_seed.isdigit() else None,
                    "w_entry": w_entry,
                    "l_entry": l_entry,
                    "score": row.get("score", ""),
                })
    return matches


def analyze(matches, label):
    print(f"\n{'='*100}")
    print(f"  {label} — {len(matches)} matches with rankings")
    print(f"{'='*100}")

    records = []
    for m in matches:
        fav_rank = min(m["w_rank"], m["l_rank"])
        dog_rank = max(m["w_rank"], m["l_rank"])
        fav_won = m["w_rank"] < m["l_rank"]
        gap = dog_rank - fav_rank
        ratio = dog_rank / fav_rank if fav_rank > 0 else 0
        records.append({
            "fav_rank": fav_rank, "dog_rank": dog_rank,
            "gap": gap, "ratio": ratio, "fav_won": fav_won,
            "round": m["round"], "tourney": m["tourney"],
            "surface": m["surface"], "level": m["level"],
        })

    # 1. Ranking gap
    print(f"\n  FAVORITE WIN RATE BY RANKING GAP")
    print(f"  {'Gap':>12} {'n':>5} {'Fav WR':>7} {'Upset%':>7}")
    print(f"  {'-'*40}")

    for lo, hi, lab in [(0, 5, "0-4"), (5, 10, "5-9"), (10, 20, "10-19"),
                         (20, 30, "20-29"), (30, 50, "30-49"), (50, 75, "50-74"),
                         (75, 100, "75-99"), (100, 150, "100-149"), (150, 200, "150-199"),
                         (200, 300, "200-299"), (300, 500, "300-499"), (500, 1000, "500-999"),
                         (1000, 9999, "1000+")]:
        bucket = [r for r in records if lo <= r["gap"] < hi]
        if len(bucket) < 5:
            continue
        wr = sum(1 for r in bucket if r["fav_won"]) / len(bucket) * 100
        print(f"  {lab:>12} {len(bucket):5d} {wr:6.1f}% {100-wr:6.1f}%")

    # 2. Ranking ratio
    print(f"\n  FAVORITE WIN RATE BY RANKING RATIO")
    print(f"  {'Ratio':>12} {'n':>5} {'Fav WR':>7} {'Upset%':>7}")
    print(f"  {'-'*40}")

    for lo, hi, lab in [(1.0, 1.2, "1.0-1.2x"), (1.2, 1.5, "1.2-1.5x"),
                         (1.5, 2.0, "1.5-2.0x"), (2.0, 3.0, "2.0-3.0x"),
                         (3.0, 5.0, "3.0-5.0x"), (5.0, 10.0, "5-10x"),
                         (10.0, 20.0, "10-20x"), (20.0, 50.0, "20-50x"),
                         (50.0, 9999, "50x+")]:
        bucket = [r for r in records if lo <= r["ratio"] < hi]
        if len(bucket) < 5:
            continue
        wr = sum(1 for r in bucket if r["fav_won"]) / len(bucket) * 100
        print(f"  {lab:>12} {len(bucket):5d} {wr:6.1f}% {100-wr:6.1f}%")

    # 3. Fav rank tier with gap sub-buckets
    print(f"\n  FAVORITE WIN RATE BY RANK TIER")
    print(f"  {'Fav rank':>12} {'n':>5} {'Fav WR':>7} | by gap: {'<20':>6} {'20-50':>6} {'50-100':>6} {'100+':>6}")
    print(f"  {'-'*75}")

    for r_lo, r_hi, r_lab in [(1, 5, "Top 5"), (1, 10, "Top 10"), (11, 20, "11-20"),
                                (21, 30, "21-30"), (31, 50, "31-50"), (51, 75, "51-75"),
                                (76, 100, "76-100"), (101, 150, "101-150"), (151, 300, "151-300")]:
        tier = [r for r in records if r_lo <= r["fav_rank"] <= r_hi]
        if len(tier) < 10:
            continue
        wr = sum(1 for r in tier if r["fav_won"]) / len(tier) * 100
        subs = []
        for g_lo, g_hi in [(0, 20), (20, 50), (50, 100), (100, 9999)]:
            sub = [r for r in tier if g_lo <= r["gap"] < g_hi]
            if len(sub) >= 5:
                subs.append(f"{sum(1 for r in sub if r['fav_won'])/len(sub)*100:5.0f}%")
            else:
                subs.append("   -- ")
        print(f"  {r_lab:>12} {len(tier):5d} {wr:6.1f}% | {' '.join(subs)}")

    # 4. By round
    print(f"\n  FAVORITE WIN RATE BY ROUND")
    print(f"  {'Round':>8} {'n':>5} {'Fav WR':>7}")
    print(f"  {'-'*25}")

    for rnd in ["R128", "R64", "R32", "R16", "QF", "SF", "F", "RR"]:
        rnd_m = [r for r in records if r["round"] == rnd]
        if len(rnd_m) < 10:
            continue
        wr = sum(1 for r in rnd_m if r["fav_won"]) / len(rnd_m) * 100
        print(f"  {rnd:>8} {len(rnd_m):5d} {wr:6.1f}%")

    # 5. Seeded vs unseeded
    print(f"\n  SEEDED PLAYER WIN RATE BY SEED TIER (vs unseeded)")
    print(f"  {'Seed':>10} {'n':>5} {'Win%':>6} {'vs Q/WC':>8} {'vs main':>8}")
    print(f"  {'-'*45}")

    for s_lo, s_hi, s_lab in [(1, 1, "1"), (2, 2, "2"), (3, 4, "3-4"),
                                (5, 8, "5-8"), (9, 16, "9-16"), (17, 32, "17-32")]:
        results = []
        vs_q = []
        vs_m = []
        for m in matches:
            if m["w_seed"] and s_lo <= m["w_seed"] <= s_hi and not m["l_seed"]:
                results.append(True)
                (vs_q if m["l_entry"] in ("Q", "WC", "LL") else vs_m).append(True)
            elif m["l_seed"] and s_lo <= m["l_seed"] <= s_hi and not m["w_seed"]:
                results.append(False)
                (vs_q if m["w_entry"] in ("Q", "WC", "LL") else vs_m).append(False)
        if len(results) < 5:
            continue
        wr = sum(results) / len(results) * 100
        q_wr = f"{sum(vs_q)/len(vs_q)*100:.0f}%" if len(vs_q) >= 5 else " --"
        m_wr = f"{sum(vs_m)/len(vs_m)*100:.0f}%" if len(vs_m) >= 5 else " --"
        print(f"  {s_lab:>10} {len(results):5d} {wr:5.1f}% {q_wr:>8} {m_wr:>8}")

    # Seed vs seed
    print(f"\n  SEED vs SEED (both seeded)")
    seed_v_seed = []
    for m in matches:
        if m["w_seed"] and m["l_seed"]:
            seed_v_seed.append({
                "higher_won": m["w_seed"] < m["l_seed"],
                "gap": abs(m["w_seed"] - m["l_seed"]),
            })
    if seed_v_seed:
        wins = sum(1 for s in seed_v_seed if s["higher_won"])
        print(f"  Total: {len(seed_v_seed)} matches, higher seed wins {wins/len(seed_v_seed)*100:.1f}%")
        print(f"\n  {'Seed gap':>10} {'n':>5} {'Higher WR':>10}")
        print(f"  {'-'*30}")
        for sg_lo, sg_hi, sg_lab in [(1, 2, "1"), (2, 4, "2-3"), (4, 8, "4-7"),
                                      (8, 16, "8-15"), (16, 32, "16-31")]:
            sub = [s for s in seed_v_seed if sg_lo <= s["gap"] < sg_hi]
            if len(sub) < 5:
                continue
            wr = sum(1 for s in sub if s["higher_won"]) / len(sub) * 100
            print(f"  {sg_lab:>10} {len(sub):5d} {wr:9.1f}%")

    # 6. Grand Slam specific
    slam_recs = [r for r in records if r["level"] == "G"]
    if slam_recs:
        print(f"\n  GRAND SLAM DRAWS ({len(slam_recs)} matches)")
        print(f"\n  {'Matchup':>35} {'n':>5} {'Fav WR':>7}")
        print(f"  {'-'*50}")

        # Use position within tournament
        tourneys = defaultdict(list)
        for m in matches:
            if m["level"] == "G" and m["w_rank"] and m["l_rank"]:
                tourneys[m["tourney_id"]].append(m)

        slam_pos = []
        for tid, t_matches in tourneys.items():
            players = {}
            for m in t_matches:
                players[m["winner"]] = m["w_rank"]
                players[m["loser"]] = m["l_rank"]
            sorted_p = sorted(players.items(), key=lambda x: x[1])
            pos_map = {name: i+1 for i, (name, _) in enumerate(sorted_p)}
            for m in t_matches:
                w_pos = pos_map.get(m["winner"])
                l_pos = pos_map.get(m["loser"])
                if w_pos and l_pos:
                    slam_pos.append({"w_pos": w_pos, "l_pos": l_pos, "n_players": len(sorted_p)})

        slam_matchups = [
            ("Seed 1-4 vs unseeded (33+)", lambda r: min(r["w_pos"], r["l_pos"]) <= 4 and max(r["w_pos"], r["l_pos"]) > 32),
            ("Seed 1-8 vs unseeded (33+)", lambda r: min(r["w_pos"], r["l_pos"]) <= 8 and max(r["w_pos"], r["l_pos"]) > 32),
            ("Seed 1-8 vs pos 60-128", lambda r: min(r["w_pos"], r["l_pos"]) <= 8 and max(r["w_pos"], r["l_pos"]) >= 60),
            ("Seed 1-4 vs pos 80-128", lambda r: min(r["w_pos"], r["l_pos"]) <= 4 and max(r["w_pos"], r["l_pos"]) >= 80),
            ("Seed 9-16 vs unseeded (33+)", lambda r: 9 <= min(r["w_pos"], r["l_pos"]) <= 16 and max(r["w_pos"], r["l_pos"]) > 32),
            ("Seed 17-32 vs unseeded (33+)", lambda r: 17 <= min(r["w_pos"], r["l_pos"]) <= 32 and max(r["w_pos"], r["l_pos"]) > 32),
            ("Pos 1-10 vs pos 100+", lambda r: min(r["w_pos"], r["l_pos"]) <= 10 and max(r["w_pos"], r["l_pos"]) >= 100),
        ]

        for lab, fn in slam_matchups:
            subset = [r for r in slam_pos if fn(r)]
            if len(subset) < 5:
                continue
            top_wins = sum(1 for r in subset if min(r["w_pos"], r["l_pos"]) == r["w_pos"])
            wr = top_wins / len(subset) * 100
            marker = " <<<" if wr >= 85 else ""
            print(f"  {lab:>35} {len(subset):5d} {wr:6.1f}%{marker}")

    # 7. Round + ratio
    print(f"\n  FAVORITE WIN RATE BY ROUND + RANK RATIO")
    print(f"  {'Round':>6} {'Ratio':>10} {'n':>5} {'Fav WR':>7}")
    print(f"  {'-'*35}")

    for rnd in ["R128", "R64", "R32", "R16", "QF", "SF", "F"]:
        rnd_recs = [r for r in records if r["round"] == rnd]
        if len(rnd_recs) < 20:
            continue
        for ratio_lo, ratio_hi, r_lab in [(1.0, 2.0, "1-2x"), (2.0, 4.0, "2-4x"),
                                            (4.0, 8.0, "4-8x"), (8.0, 999, "8x+")]:
            subset = [r for r in rnd_recs if ratio_lo <= r["ratio"] < ratio_hi]
            if len(subset) < 10:
                continue
            wr = sum(1 for r in subset if r["fav_won"]) / len(subset) * 100
            print(f"  {rnd:>6} {r_lab:>10} {len(subset):5d} {wr:6.1f}%")

    # 8. Qualifier/WC
    print(f"\n  QUALIFIER & WILDCARD PERFORMANCE")
    print(f"  {'Entry':>12} {'n':>5} {'Win%':>6} {'vs seeded':>10} {'vs unseeded':>12}")
    print(f"  {'-'*50}")

    for et, e_lab in [("Q", "Qualifier"), ("WC", "Wildcard"), ("LL", "Lucky Loser")]:
        results = []
        vs_s = []
        vs_u = []
        for m in matches:
            if m["w_entry"] == et:
                results.append(True)
                (vs_s if m["l_seed"] else vs_u).append(True)
            elif m["l_entry"] == et:
                results.append(False)
                (vs_s if m["w_seed"] else vs_u).append(False)
        if len(results) < 5:
            continue
        wr = sum(results) / len(results) * 100
        s_wr = f"{sum(vs_s)/len(vs_s)*100:.0f}%" if len(vs_s) >= 5 else " --"
        u_wr = f"{sum(vs_u)/len(vs_u)*100:.0f}%" if len(vs_u) >= 5 else " --"
        print(f"  {e_lab:>12} {len(results):5d} {wr:5.1f}% {s_wr:>10} {u_wr:>12}")

    # 9. Sweet spot finder
    print(f"\n  SWEET SPOTS: WR >= 80% and n >= 15")
    print(f"  {'WR':>6} {'n':>5}  Condition")
    print(f"  {'-'*60}")

    spots = []
    for r_lo, r_hi, r_lab in [(1, 3, "Rank 1-3"), (1, 5, "Rank 1-5"),
                                (1, 10, "Rank 1-10"), (1, 20, "Rank 1-20")]:
        for o_lo, o_hi, o_lab in [(30, 50, "vs rank 30-50"), (50, 100, "vs rank 50-100"),
                                    (50, 200, "vs rank 50-200"), (100, 500, "vs rank 100-500"),
                                    (100, 9999, "vs rank 100+"), (150, 9999, "vs rank 150+"),
                                    (200, 9999, "vs rank 200+")]:
            sub = [r for r in records if r_lo <= r["fav_rank"] <= r_hi and o_lo <= r["dog_rank"] <= o_hi]
            if len(sub) >= 15:
                wr = sum(1 for r in sub if r["fav_won"]) / len(sub) * 100
                if wr >= 80:
                    spots.append((wr, len(sub), f"{r_lab} {o_lab}"))

    for s_lo, s_hi, s_lab in [(1, 2, "Seed 1-2"), (1, 4, "Seed 1-4"),
                                (1, 8, "Seed 1-8"), (1, 16, "Seed 1-16")]:
        for ot, o_lab, o_fn in [
            ("rank", "Opp rank 50+", lambda m, role: (m[f"{role}_rank"] or 0) >= 50),
            ("rank", "Opp rank 100+", lambda m, role: (m[f"{role}_rank"] or 0) >= 100),
            ("rank", "Opp rank 150+", lambda m, role: (m[f"{role}_rank"] or 0) >= 150),
            ("rank", "Opp rank 200+", lambda m, role: (m[f"{role}_rank"] or 0) >= 200),
            ("entry", "Opp Q/WC/LL", lambda m, role: m[f"{role}_entry"] in ("Q", "WC", "LL")),
            ("unseed", "Opp unseeded", lambda m, role: m[f"{role}_seed"] is None),
        ]:
            results = []
            for m in matches:
                if m["w_seed"] and s_lo <= m["w_seed"] <= s_hi:
                    if o_fn(m, "l"):
                        results.append(True)
                elif m["l_seed"] and s_lo <= m["l_seed"] <= s_hi:
                    if o_fn(m, "w"):
                        results.append(False)
            if len(results) >= 15:
                wr = sum(results) / len(results) * 100
                if wr >= 80:
                    spots.append((wr, len(results), f"{s_lab} vs {o_lab}"))

    spots.sort(key=lambda x: (-x[0], -x[1]))
    seen = set()
    for wr, n, cond in spots:
        if cond not in seen:
            seen.add(cond)
            print(f"  {wr:5.1f}% {n:5d}  {cond}")

    return records


def compare_atp_wta(wta_tour, wta_chall):
    """WTA vs ATP comparison highlights."""
    print(f"\n{'='*100}")
    print(f"  WTA vs ATP COMPARISON (key differences)")
    print(f"{'='*100}")

    # WTA is known for more upsets — quantify
    for label, recs in [("WTA Tour", wta_tour), ("WTA Challenger/ITF", wta_chall)]:
        if not recs:
            continue
        overall_wr = sum(1 for r in recs if r["fav_won"]) / len(recs) * 100
        # High-ratio matches
        high_ratio = [r for r in recs if r["ratio"] >= 5]
        high_wr = sum(1 for r in high_ratio if r["fav_won"]) / len(high_ratio) * 100 if high_ratio else 0
        # Top 10 fav
        top10 = [r for r in recs if r["fav_rank"] <= 10]
        top10_wr = sum(1 for r in top10 if r["fav_won"]) / len(top10) * 100 if top10 else 0
        print(f"\n  {label} ({len(recs)} matches):")
        print(f"    Overall fav WR: {overall_wr:.1f}%")
        print(f"    Ratio 5x+ fav WR: {high_wr:.1f}% (n={len(high_ratio)})")
        print(f"    Top 10 fav WR: {top10_wr:.1f}% (n={len(top10)})")


def main():
    # WTA Tour: G=Grand Slam, PM=Premier Mandatory (WTA 1000), P=Premier (500), I=International (250)
    tour = load_matches(TOUR_FILES, level_filter={"G", "PM", "P", "I", "F"})
    # WTA Challenger from ITF file
    chall = load_matches(CHALL_ITF_FILES, level_filter={"C"})

    print(f"WTA Tour: {len(tour)} matches | WTA Challenger: {len(chall)} matches (2023-2024)\n")

    recs_tour = analyze(tour, "WTA TOUR (Grand Slams + WTA 1000/500/250)")
    recs_chall = analyze(chall, "WTA CHALLENGER")
    compare_atp_wta(recs_tour, recs_chall)


if __name__ == "__main__":
    main()
