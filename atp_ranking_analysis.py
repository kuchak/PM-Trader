#!/usr/bin/env python3
"""ATP ranking gap analysis: find sweet spots for betting on favorites by ranking gap."""
import csv
from collections import defaultdict

DATA_DIR = "data/atp"
TOUR_FILES = [f"{DATA_DIR}/atp_matches_2023.csv", f"{DATA_DIR}/atp_matches_2024.csv"]
CHALL_FILES = [f"{DATA_DIR}/atp_matches_qual_chall_2023.csv", f"{DATA_DIR}/atp_matches_qual_chall_2024.csv"]

def load_matches(files, level_filter=None):
    """Load matches, return list of dicts with ranking info."""
    matches = []
    for fpath in files:
        with open(fpath) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if level_filter and row.get("tourney_level") not in level_filter:
                    continue
                try:
                    w_rank = int(row["winner_rank"]) if row["winner_rank"] else None
                    l_rank = int(row["loser_rank"]) if row["loser_rank"] else None
                except (ValueError, KeyError):
                    w_rank = l_rank = None
                if w_rank is None or l_rank is None:
                    continue

                w_seed = row.get("winner_seed", "").strip()
                l_seed = row.get("loser_seed", "").strip()

                matches.append({
                    "tourney": row["tourney_name"],
                    "level": row.get("tourney_level", "?"),
                    "round": row.get("round", ""),
                    "surface": row.get("surface", ""),
                    "winner": row["winner_name"],
                    "loser": row["loser_name"],
                    "w_rank": w_rank,
                    "l_rank": l_rank,
                    "w_seed": int(w_seed) if w_seed.isdigit() else None,
                    "l_seed": int(l_seed) if l_seed.isdigit() else None,
                    "w_age": float(row.get("winner_age", 0) or 0),
                    "l_age": float(row.get("loser_age", 0) or 0),
                    "score": row.get("score", ""),
                })
    return matches


def analyze_ranking_gap(matches, label):
    """Analyze favorite win rate by ranking gap buckets."""
    print(f"\n{'='*100}")
    print(f"  {label} — {len(matches)} matches with rankings")
    print(f"{'='*100}")

    # For each match: the "favorite" is the higher-ranked (lower number) player
    # ranking_gap = loser_rank - winner_rank (positive means favorite won)
    # But we want to analyze from the FAVORITE's perspective regardless of outcome

    records = []
    for m in matches:
        if m["w_rank"] < m["l_rank"]:
            # Higher-ranked player won (favorite won)
            fav_rank = m["w_rank"]
            dog_rank = m["l_rank"]
            fav_won = True
        else:
            # Lower-ranked player won (upset)
            fav_rank = m["l_rank"]
            dog_rank = m["w_rank"]
            fav_won = False

        gap = dog_rank - fav_rank
        ratio = dog_rank / fav_rank if fav_rank > 0 else 0

        records.append({
            "fav_rank": fav_rank,
            "dog_rank": dog_rank,
            "gap": gap,
            "ratio": ratio,
            "fav_won": fav_won,
            "round": m["round"],
            "tourney": m["tourney"],
            "surface": m["surface"],
        })

    # 1. Favorite win rate by ranking gap buckets
    print(f"\n  FAVORITE WIN RATE BY RANKING GAP (gap = underdog_rank - favorite_rank)")
    print(f"  {'Gap':>12} {'n':>5} {'Fav wins':>9} {'Fav WR':>7} {'Upset%':>7}")
    print(f"  {'-'*50}")

    gap_buckets = [
        (0, 5, "0-4"),
        (5, 10, "5-9"),
        (10, 20, "10-19"),
        (20, 30, "20-29"),
        (30, 50, "30-49"),
        (50, 75, "50-74"),
        (75, 100, "75-99"),
        (100, 150, "100-149"),
        (150, 200, "150-199"),
        (200, 300, "200-299"),
        (300, 500, "300-499"),
        (500, 1000, "500-999"),
        (1000, 9999, "1000+"),
    ]

    for lo, hi, label_b in gap_buckets:
        bucket = [r for r in records if lo <= r["gap"] < hi]
        if len(bucket) < 5:
            continue
        wins = sum(1 for r in bucket if r["fav_won"])
        wr = wins / len(bucket) * 100
        print(f"  {label_b:>12} {len(bucket):5d} {wins:9d} {wr:6.1f}% {100-wr:6.1f}%")

    # 2. Favorite win rate by ranking RATIO (more useful for cross-tier comparisons)
    print(f"\n  FAVORITE WIN RATE BY RANKING RATIO (underdog_rank / favorite_rank)")
    print(f"  {'Ratio':>12} {'n':>5} {'Fav wins':>9} {'Fav WR':>7} {'Upset%':>7}")
    print(f"  {'-'*50}")

    ratio_buckets = [
        (1.0, 1.2, "1.0-1.2x"),
        (1.2, 1.5, "1.2-1.5x"),
        (1.5, 2.0, "1.5-2.0x"),
        (2.0, 3.0, "2.0-3.0x"),
        (3.0, 5.0, "3.0-5.0x"),
        (5.0, 10.0, "5-10x"),
        (10.0, 20.0, "10-20x"),
        (20.0, 50.0, "20-50x"),
        (50.0, 9999, "50x+"),
    ]

    for lo, hi, label_b in ratio_buckets:
        bucket = [r for r in records if lo <= r["ratio"] < hi]
        if len(bucket) < 5:
            continue
        wins = sum(1 for r in bucket if r["fav_won"])
        wr = wins / len(bucket) * 100
        print(f"  {label_b:>12} {len(bucket):5d} {wins:9d} {wr:6.1f}% {100-wr:6.1f}%")

    # 3. By favorite's absolute rank tier
    print(f"\n  FAVORITE WIN RATE BY FAVORITE'S RANK TIER")
    print(f"  {'Fav rank':>12} {'n':>5} {'Fav WR':>7} | by gap: {'<20':>6} {'20-50':>6} {'50-100':>6} {'100+':>6}")
    print(f"  {'-'*75}")

    rank_tiers = [
        (1, 10, "Top 10"),
        (11, 20, "11-20"),
        (21, 30, "21-30"),
        (31, 50, "31-50"),
        (51, 75, "51-75"),
        (76, 100, "76-100"),
        (101, 150, "101-150"),
        (151, 200, "151-200"),
        (201, 500, "201-500"),
    ]

    for lo, hi, label_b in rank_tiers:
        tier = [r for r in records if lo <= r["fav_rank"] <= hi]
        if len(tier) < 10:
            continue
        wr = sum(1 for r in tier if r["fav_won"]) / len(tier) * 100

        # Sub-buckets by gap
        sub_wrs = []
        for g_lo, g_hi in [(0, 20), (20, 50), (50, 100), (100, 9999)]:
            sub = [r for r in tier if g_lo <= r["gap"] < g_hi]
            if len(sub) >= 5:
                sub_wr = sum(1 for r in sub if r["fav_won"]) / len(sub) * 100
                sub_wrs.append(f"{sub_wr:5.0f}%")
            else:
                sub_wrs.append("   -- ")
        print(f"  {label_b:>12} {len(tier):5d} {wr:6.1f}% | {' '.join(sub_wrs)}")

    # 4. By round
    print(f"\n  FAVORITE WIN RATE BY ROUND")
    print(f"  {'Round':>8} {'n':>5} {'Fav WR':>7}")
    print(f"  {'-'*25}")

    round_order = ["R128", "R64", "R32", "R16", "QF", "SF", "F", "RR"]
    for rnd in round_order:
        rnd_matches = [r for r in records if r["round"] == rnd]
        if len(rnd_matches) < 10:
            continue
        wr = sum(1 for r in rnd_matches if r["fav_won"]) / len(rnd_matches) * 100
        print(f"  {rnd:>8} {len(rnd_matches):5d} {wr:6.1f}%")

    # 5. Sweet spot analysis: where is favorite WR >= 90%?
    print(f"\n  SWEET SPOTS: Conditions where favorite wins >= 85%")
    print(f"  {'Fav rank':>12} {'Gap':>12} {'n':>5} {'Fav WR':>7}")
    print(f"  {'-'*45}")

    for r_lo, r_hi, r_label in rank_tiers:
        for g_lo, g_hi, g_label in gap_buckets:
            subset = [r for r in records
                      if r_lo <= r["fav_rank"] <= r_hi
                      and g_lo <= r["gap"] < g_hi]
            if len(subset) < 10:
                continue
            wr = sum(1 for r in subset if r["fav_won"]) / len(subset) * 100
            if wr >= 85:
                print(f"  {r_label:>12} {g_label:>12} {len(subset):5d} {wr:6.1f}%")

    # 6. Seed analysis (tournament seedings)
    seeded_matches = [r for r in records
                      if any(m["w_seed"] or m["l_seed"]
                             for m in matches
                             if (m["winner"] in (r.get("winner", ""), "") or True))]

    # Simpler: use original matches for seed analysis
    print(f"\n  SEEDED vs UNSEEDED")
    seeded_v_unseeded = []
    for m in matches:
        if m["w_seed"] and not m["l_seed"]:
            seeded_v_unseeded.append({"seeded_won": True, "seed": m["w_seed"],
                                      "opp_rank": m["l_rank"]})
        elif m["l_seed"] and not m["w_seed"]:
            seeded_v_unseeded.append({"seeded_won": False, "seed": m["l_seed"],
                                      "opp_rank": m["w_rank"]})

    if seeded_v_unseeded:
        wins = sum(1 for s in seeded_v_unseeded if s["seeded_won"])
        print(f"  Seeded vs unseeded: {len(seeded_v_unseeded)} matches, "
              f"seeded wins {wins/len(seeded_v_unseeded)*100:.1f}%")

        # By seed number
        print(f"\n  {'Seed':>6} {'n':>5} {'Win%':>6}")
        print(f"  {'-'*20}")
        for seed_lo, seed_hi, s_label in [(1, 1, "1"), (2, 2, "2"), (3, 4, "3-4"),
                                           (5, 8, "5-8"), (9, 16, "9-16"), (17, 32, "17-32")]:
            sub = [s for s in seeded_v_unseeded if seed_lo <= s["seed"] <= seed_hi]
            if len(sub) < 5:
                continue
            wr = sum(1 for s in sub if s["seeded_won"]) / len(sub) * 100
            print(f"  {s_label:>6} {len(sub):5d} {wr:5.1f}%")

    # Seed matchup: both seeded
    print(f"\n  SEED vs SEED (both players seeded)")
    seed_v_seed = []
    for m in matches:
        if m["w_seed"] and m["l_seed"]:
            higher_seed_won = m["w_seed"] < m["l_seed"]
            seed_v_seed.append({
                "higher_won": higher_seed_won,
                "high_seed": min(m["w_seed"], m["l_seed"]),
                "low_seed": max(m["w_seed"], m["l_seed"]),
                "gap": abs(m["w_seed"] - m["l_seed"]),
            })

    if seed_v_seed:
        wins = sum(1 for s in seed_v_seed if s["higher_won"])
        print(f"  Total: {len(seed_v_seed)} matches, higher seed wins {wins/len(seed_v_seed)*100:.1f}%")

        print(f"\n  {'Seed gap':>10} {'n':>5} {'Higher seed WR':>15}")
        print(f"  {'-'*35}")
        for sg_lo, sg_hi, sg_label in [(1, 2, "1"), (2, 4, "2-3"), (4, 8, "4-7"),
                                        (8, 16, "8-15"), (16, 32, "16-31")]:
            sub = [s for s in seed_v_seed if sg_lo <= s["gap"] < sg_hi]
            if len(sub) < 5:
                continue
            wr = sum(1 for s in sub if s["higher_won"]) / len(sub) * 100
            print(f"  {sg_label:>10} {len(sub):5d} {wr:14.1f}%")

    return records


def polymarket_context(records_tour, records_chall):
    """What this means for Polymarket betting at 90%+ implied prob."""
    print(f"\n{'='*100}")
    print(f"  POLYMARKET RELEVANCE: When market says 90%+ for the favorite...")
    print(f"{'='*100}")

    # On Polymarket, when a player is at 90%+, they're the clear favorite
    # The question: does the ranking gap tell us if 90% is UNDER or OVER-pricing?

    # For Tour matches where fav WR is actually > 90%
    print(f"\n  CONDITIONS WHERE FAVORITE ACTUALLY WINS > 90% (Tour events):")
    print(f"  {'Condition':>30} {'n':>5} {'Actual WR':>10}")
    print(f"  {'-'*50}")

    conditions = [
        ("Top 10 vs 100+", lambda r: r["fav_rank"] <= 10 and r["dog_rank"] > 100),
        ("Top 10 vs 50+", lambda r: r["fav_rank"] <= 10 and r["dog_rank"] > 50),
        ("Top 20 vs 100+", lambda r: r["fav_rank"] <= 20 and r["dog_rank"] > 100),
        ("Top 20 vs 200+", lambda r: r["fav_rank"] <= 20 and r["dog_rank"] > 200),
        ("Top 30 vs 100+", lambda r: r["fav_rank"] <= 30 and r["dog_rank"] > 100),
        ("Top 50 vs 200+", lambda r: r["fav_rank"] <= 50 and r["dog_rank"] > 200),
        ("Gap 100+", lambda r: r["gap"] >= 100),
        ("Gap 200+", lambda r: r["gap"] >= 200),
        ("Ratio 5x+", lambda r: r["ratio"] >= 5),
        ("Ratio 10x+", lambda r: r["ratio"] >= 10),
        ("Top 5 any", lambda r: r["fav_rank"] <= 5),
        ("Top 3 any", lambda r: r["fav_rank"] <= 3),
    ]

    for label_c, fn in conditions:
        subset = [r for r in records_tour if fn(r)]
        if len(subset) < 10:
            continue
        wr = sum(1 for r in subset if r["fav_won"]) / len(subset) * 100
        marker = " <<<" if wr >= 90 else ""
        print(f"  {label_c:>30} {len(subset):5d} {wr:9.1f}%{marker}")

    print(f"\n  CONDITIONS WHERE FAVORITE ACTUALLY WINS > 90% (Challenger events):")
    print(f"  {'Condition':>30} {'n':>5} {'Actual WR':>10}")
    print(f"  {'-'*50}")

    for label_c, fn in conditions:
        subset = [r for r in records_chall if fn(r)]
        if len(subset) < 10:
            continue
        wr = sum(1 for r in subset if r["fav_won"]) / len(subset) * 100
        marker = " <<<" if wr >= 90 else ""
        print(f"  {label_c:>30} {len(subset):5d} {wr:9.1f}%{marker}")

    # Upset risk zones
    print(f"\n  UPSET DANGER ZONES: Where favorite WR < 65% (high upset risk)")
    print(f"  {'Condition':>30} {'n':>5} {'Fav WR':>7} {'Tier':>8}")
    print(f"  {'-'*55}")

    danger_conditions = [
        ("Gap < 5", lambda r: r["gap"] < 5, "Tour"),
        ("Gap 5-9", lambda r: 5 <= r["gap"] < 10, "Tour"),
        ("Ratio < 1.2x", lambda r: r["ratio"] < 1.2, "Tour"),
        ("Top 10 vs Top 20", lambda r: r["fav_rank"] <= 10 and r["dog_rank"] <= 20, "Tour"),
        ("Top 20 vs Top 30", lambda r: r["fav_rank"] <= 20 and r["dog_rank"] <= 30, "Tour"),
    ]

    for label_c, fn, tier in danger_conditions:
        recs = records_tour if tier == "Tour" else records_chall
        subset = [r for r in recs if fn(r)]
        if len(subset) < 10:
            continue
        wr = sum(1 for r in subset if r["fav_won"]) / len(subset) * 100
        marker = " ⚠️" if wr < 65 else ""
        print(f"  {label_c:>30} {len(subset):5d} {wr:6.1f}% {tier:>8}{marker}")


def main():
    # Load Tour matches (Grand Slam, Masters, ATP 250/500)
    tour_matches = load_matches(TOUR_FILES, level_filter={"G", "M", "A", "F"})
    # Load Challenger matches
    chall_matches = load_matches(CHALL_FILES, level_filter={"C"})

    print(f"Loaded {len(tour_matches)} Tour matches, {len(chall_matches)} Challenger matches (2023-2024)")

    records_tour = analyze_ranking_gap(tour_matches, "ATP TOUR (Grand Slams + Masters + ATP 250/500)")
    records_chall = analyze_ranking_gap(chall_matches, "ATP CHALLENGER")

    polymarket_context(records_tour, records_chall)

    # Surface analysis
    print(f"\n{'='*100}")
    print(f"  SURFACE BREAKDOWN (Tour events)")
    print(f"{'='*100}")

    for surface in ["Hard", "Clay", "Grass"]:
        surf_matches = [m for m in tour_matches if m["surface"] == surface]
        if len(surf_matches) < 50:
            continue
        records = []
        for m in surf_matches:
            fav_rank = min(m["w_rank"], m["l_rank"])
            dog_rank = max(m["w_rank"], m["l_rank"])
            fav_won = m["w_rank"] < m["l_rank"]
            gap = dog_rank - fav_rank
            records.append({"fav_rank": fav_rank, "gap": gap, "fav_won": fav_won})

        fav_wr = sum(1 for r in records if r["fav_won"]) / len(records) * 100
        print(f"\n  {surface} ({len(records)} matches, overall fav WR: {fav_wr:.1f}%)")
        print(f"  {'Gap':>12} {'n':>5} {'Fav WR':>7}")
        print(f"  {'-'*30}")

        for lo, hi, label_b in [(0, 10, "0-9"), (10, 30, "10-29"), (30, 50, "30-49"),
                                 (50, 100, "50-99"), (100, 200, "100-199"), (200, 9999, "200+")]:
            bucket = [r for r in records if lo <= r["gap"] < hi]
            if len(bucket) < 10:
                continue
            wr = sum(1 for r in bucket if r["fav_won"]) / len(bucket) * 100
            print(f"  {label_b:>12} {len(bucket):5d} {wr:6.1f}%")


if __name__ == "__main__":
    main()
