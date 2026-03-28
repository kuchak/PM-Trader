#!/usr/bin/env python3
"""
Tennis Trading Bot — 3-Layer Strategy (Seeding + Ranking Ratio + Set 1 Margin)

Uses ESPN + TennisExplorer for rankings/seedings and live Set 1 results,
combined with Polymarket for market entry/exit.

3-layer system based on backtested 2023-2024 data (105k matches, exclusive WRs):
  Layer 0: Favorite filter — S1 winner must be better-ranked (underdog S1 winners: 77% WR, not profitable)
  Layer 1: S1 Margin (always required — must win Set 1)
  Layer 2: Tournament Seeding (seeded vs unseeded — from TE draw page)
  Layer 3: Ranking Ratio (fallback when seeding unavailable)

  Tier S: ≥96% WR → 40% bankroll, $100 cap
  Tier A: 93-96% WR → 25% bankroll, $75 cap
  Tier B: 90-93% WR → 15% bankroll, $50 cap
  Tier C: 88-90% WR → 6% bankroll, $25 cap
"""

import os, sys, json, time, signal, logging, traceback, csv, re, unicodedata
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_REPO_DIR, "data", "tennis_bot_state.json")
TRADES_CSV = os.path.join(_REPO_DIR, "data", "tennis_trades.csv")
INITIAL_BANKROLL = 500.0
POLL_INTERVAL = 30
MIN_BET = 5.0
MAX_ENTRY_PRICE = 0.98
MAX_ENTRY_TIER_B = 0.94   # Tier B cap: skip entries >94c (backtested: +$23.54, avoids outsized losses)
SKIP_ABOVE_PRICE = 0.99
MAX_CONCURRENT = 6
MAX_TOTAL_EXPOSURE_PCT = 1.00
MAX_PER_MARKET_PCT = 0.40   # tennis tiers can go up to 50%, allow room

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"
TAG_ID = 864           # "Tennis" tag — 100639 ("Games") buries tennis among 3000+ events

ESPN_ATP_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard"
ESPN_WTA_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard"
ESPN_ATP_RANKINGS = "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/rankings"
ESPN_WTA_RANKINGS = "https://site.api.espn.com/apis/site/v2/sports/tennis/wta/rankings"

# --- Tennis slug prefixes on Polymarket ---
TENNIS_PREFIXES = ('atp-', 'wta-')

# --- Stop-loss: none for tennis (at these WRs, stops just create false exits) ---
# But we do cut truly dead positions
STOP_LOSS = 0.30

SKIP_OUTCOMES = {'Over', 'Under', 'Draw', 'Tie'}

# ─── TIER DEFINITIONS (3-Layer: Seeding + Ranking Ratio + S1 Margin) ─────────
# Checked top-to-bottom per category. First match = best tier = biggest bet.
# Exclusive WRs from 2023-2024 backtest (105k matches, JeffSackmann data).
# 'seed_vs_unseed': True = S1 winner must be seeded AND opponent unseeded.

TIERS = [
    # ════════════════════════════════════════════════════════════════════
    # PREREQUISITE: S1 winner must be the FAVORITE (better-ranked).
    # Underdog S1 winners have only 76-79% WR — filtered out before tiers.
    #
    # S1 margin is the FOUNDATION. High margin alone qualifies.
    # Seeding/ratio only unlock LOWER margins that wouldn't qualify alone.
    # Tiers evaluated top-to-bottom; first match wins.
    # WRs below are FAVORITE-only from 2023-2024 backtest.
    # ════════════════════════════════════════════════════════════════════

    # ── ALL CATEGORIES: S1 margin alone (universal) ──
    # S1≥+6: 95-97% WR for favorites
    {'name': 'S', 'categories': ['WTA_CHALL'], 'seed_vs_unseed': False,
     's1_margin_min': 6, 'ratio_min': 0, 'bet_pct': 0.40, 'bet_cap': 100},   # ~97% fav WR
    {'name': 'A', 'categories': ['ATP_CHALL'], 'seed_vs_unseed': False,
     's1_margin_min': 6, 'ratio_min': 0, 'bet_pct': 0.25, 'bet_cap': 75},    # ~95% fav WR
    {'name': 'A', 'categories': ['ATP_TOUR', 'ATP_SLAM'], 'seed_vs_unseed': False,
     's1_margin_min': 6, 'ratio_min': 0, 'bet_pct': 0.25, 'bet_cap': 75},    # ~96% fav WR
    {'name': 'A', 'categories': ['WTA_TOUR', 'WTA_SLAM'], 'seed_vs_unseed': False,
     's1_margin_min': 6, 'ratio_min': 0, 'bet_pct': 0.25, 'bet_cap': 75},    # ~94% fav WR

    # S1≥+5: 93-95% WR for favorites
    {'name': 'A', 'categories': ['WTA_CHALL'], 'seed_vs_unseed': False,
     's1_margin_min': 5, 'ratio_min': 0, 'bet_pct': 0.25, 'bet_cap': 75},    # ~95% fav WR
    {'name': 'B', 'categories': ['ATP_CHALL'], 'seed_vs_unseed': False,
     's1_margin_min': 5, 'ratio_min': 0, 'bet_pct': 0.15, 'bet_cap': 50},    # ~93% fav WR
    {'name': 'B', 'categories': ['ATP_TOUR', 'ATP_SLAM'], 'seed_vs_unseed': False,
     's1_margin_min': 5, 'ratio_min': 0, 'bet_pct': 0.15, 'bet_cap': 50},    # ~93% fav WR
    {'name': 'B', 'categories': ['WTA_TOUR', 'WTA_SLAM'], 'seed_vs_unseed': False,
     's1_margin_min': 5, 'ratio_min': 0, 'bet_pct': 0.15, 'bet_cap': 50},    # ~93% fav WR

    # S1≥+4: 90-92% WR for favorites
    {'name': 'B', 'categories': ['WTA_CHALL'], 'seed_vs_unseed': False,
     's1_margin_min': 4, 'ratio_min': 0, 'bet_pct': 0.15, 'bet_cap': 50},    # ~92% fav WR

    # ── LOWER MARGINS: seeding/ratio required to boost WR ──

    # Seed + S1≥+3: unlocks lower margin for WTA_CHALL
    {'name': 'B', 'categories': ['WTA_CHALL'], 'seed_vs_unseed': True,
     's1_margin_min': 3, 'ratio_min': 0, 'bet_pct': 0.15, 'bet_cap': 50},    # ~93% fav+seed WR

    # Ratio≥3x + S1≥+4: unlocks +4 for ATP categories (already favorite-filtered)
    {'name': 'B', 'categories': ['ATP_CHALL'], 'seed_vs_unseed': False,
     's1_margin_min': 4, 'ratio_min': 3, 'bet_pct': 0.15, 'bet_cap': 50},    # ~97% fav+ratio WR
    {'name': 'B', 'categories': ['ATP_TOUR', 'ATP_SLAM'], 'seed_vs_unseed': False,
     's1_margin_min': 4, 'ratio_min': 3, 'bet_pct': 0.15, 'bet_cap': 50},    # ~93% fav+ratio WR
    {'name': 'B', 'categories': ['WTA_TOUR', 'WTA_SLAM'], 'seed_vs_unseed': False,
     's1_margin_min': 4, 'ratio_min': 3, 'bet_pct': 0.15, 'bet_cap': 50},    # ~93% fav+ratio WR

    # Seed + S1≥+4: unlocks +4 for ATP_CHALL via seeding
    {'name': 'C', 'categories': ['ATP_CHALL'], 'seed_vs_unseed': True,
     's1_margin_min': 4, 'ratio_min': 0, 'bet_pct': 0.06, 'bet_cap': 25},    # ~92% fav+seed WR

    # Ratio≥2x + S1≥+3: unlocks +3 for ATP_CHALL
    {'name': 'C', 'categories': ['ATP_CHALL'], 'seed_vs_unseed': False,
     's1_margin_min': 3, 'ratio_min': 2, 'bet_pct': 0.06, 'bet_cap': 25},    # ~93% fav+ratio WR

    # Ratio≥3x + S1≥+3: unlocks +3 for Tour
    {'name': 'C', 'categories': ['ATP_TOUR', 'ATP_SLAM'], 'seed_vs_unseed': False,
     's1_margin_min': 3, 'ratio_min': 3, 'bet_pct': 0.06, 'bet_cap': 25},    # ~92% fav+ratio WR
    {'name': 'C', 'categories': ['WTA_TOUR', 'WTA_SLAM'], 'seed_vs_unseed': False,
     's1_margin_min': 3, 'ratio_min': 3, 'bet_pct': 0.06, 'bet_cap': 25},    # ~92% fav+ratio WR
]

# Grand Slam tournament name fragments (for Slam tier detection)
SLAM_NAMES = {'australian open', 'roland garros', 'french open', 'wimbledon', 'us open'}

# ─── LOGGING ─────────────────────────────────────────────────────────────────

LOG_DIR = os.path.expanduser("~/polymarket/trader_logs")
os.makedirs(LOG_DIR, exist_ok=True)
log_file = os.path.join(LOG_DIR, f"tennis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)


# ─── UTILITIES ───────────────────────────────────────────────────────────────

def normalize_name(name):
    """Strip diacritics, lowercase, collapse whitespace."""
    if not name:
        return ""
    n = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    n = re.sub(r'\s+', ' ', n).strip().lower()
    return n


def extract_last_name(name):
    """Extract last name from full name for fuzzy matching."""
    parts = normalize_name(name).split()
    return parts[-1] if parts else ""


def names_match(name_a, name_b):
    """Check if two player names likely refer to the same person."""
    a = normalize_name(name_a)
    b = normalize_name(name_b)
    if a == b:
        return True
    # Last name match + first initial
    parts_a = a.split()
    parts_b = b.split()
    if not parts_a or not parts_b:
        return False
    # Same last name
    if parts_a[-1] == parts_b[-1]:
        # Either full match or first initial matches
        if len(parts_a) >= 2 and len(parts_b) >= 2:
            if parts_a[0][0] == parts_b[0][0]:
                return True
        return True  # last name alone is usually enough in tennis
    # Handle "Auger-Aliassime" vs "Auger Aliassime" etc.
    a_joined = a.replace('-', ' ').replace('.', '')
    b_joined = b.replace('-', ' ').replace('.', '')
    if a_joined == b_joined:
        return True
    return False


def parse_polymarket_score(score_str):
    """
    Parse Polymarket's score format into set-by-set results.
    Format: "6-3, 0-2" → [(6, 3), (0, 2)]
    Returns list of (home_games, away_games) tuples, or empty list.
    """
    if not score_str or not score_str.strip():
        return []
    sets = []
    for part in score_str.split(','):
        part = part.strip()
        m = re.match(r'(\d+)-(\d+)', part)
        if m:
            sets.append((int(m.group(1)), int(m.group(2))))
    return sets


def setup_clob_client():
    from py_clob_client.client import ClobClient
    pk = os.getenv("POLYMARKET_PK")
    funder = os.getenv("POLYMARKET_FUNDER")
    if not pk or not funder:
        logger.error("Missing POLYMARKET_PK or POLYMARKET_FUNDER"); sys.exit(1)
    client = ClobClient(CLOB_API, key=pk, chain_id=137, signature_type=1, funder=funder)
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    logger.info(f"CLOB client ready. Funder: {funder[:10]}...")
    return client


# ─── TENNIS EXPLORER CLIENT ────────────────────────────────────────────────

class TennisExplorerClient:
    """
    Scrapes TennisExplorer for Challenger live scores and rankings.
    Uses match-detail pages which are server-rendered (no JS needed).
    """
    BASE = "https://www.tennisexplorer.com"
    HEADERS = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self._schedule_cache = {}   # {norm_name: match_id}
        self._schedule_ts = None
        self._schedule_ttl = 1800   # refresh every 30 min
        self._detail_cache = {}     # {match_id: {parsed data}}
        self._detail_ts = {}        # {match_id: timestamp}
        self._detail_ttl = 25       # re-fetch detail every 25s
        self._rank_cache = {}       # {norm_name: rank} — built from match-detail pages
        self._rank_cache_ts = None
        self._rank_cache_ttl = 3600 # rankings don't change during a session
        self._seed_cache = {}       # {norm_name: seed_number} — from draw pages
        self._seed_cache_ts = None
        self._seed_cache_ttl = 7200 # seeds don't change during a tournament (2h)

    def _refresh_schedule(self):
        """Load today's Challenger schedule: player_name → match_id mapping."""
        now = time.time()
        if self._schedule_ts and (now - self._schedule_ts) < self._schedule_ttl:
            return
        cache = {}
        for match_type in ['atp-challenger', 'wta-challenger']:
            try:
                url = f"{self.BASE}/matches/?type={match_type}"
                resp = self.session.get(url, timeout=15)
                if resp.status_code != 200:
                    logger.warning(f"TennisExplorer {match_type} schedule: HTTP {resp.status_code}")
                    continue
                html = resp.text
                # HTML structure: each match has two <tr> rows sharing one match-detail link.
                # Row 1 (id="rN"): player1 name + match-detail link
                # Row 2 (id="rNb"): player2 name
                # Strategy: find each match-detail link, then find the two closest
                # player links — one before (in same row) and one after (in next row)
                # Split around each match-detail link
                parts = re.split(r'(match-detail/\?id=\d+)', html)
                for i in range(1, len(parts), 2):
                    mid = re.search(r'(\d+)', parts[i]).group(1)
                    # Player 1 is in the section before this link (same <tr>)
                    before = parts[i-1] if i > 0 else ''
                    # Player 2 is in the section after this link (next <tr>)
                    after = parts[i+1] if i+1 < len(parts) else ''
                    # Get the last player link from before
                    p1_links = re.findall(r'<a href="/player/[^"]*"[^>]*>(.*?)</a>', before)
                    # Get the first player link from after
                    p2_links = re.findall(r'<a href="/player/[^"]*"[^>]*>(.*?)</a>', after)
                    if p1_links:
                        norm = normalize_name(p1_links[-1])
                        if norm:
                            cache[norm] = mid
                    if p2_links:
                        norm = normalize_name(p2_links[0])
                        if norm:
                            cache[norm] = mid
                count = len(set(cache.values()))
                logger.info(f"  TennisExplorer: {match_type} schedule loaded ({count} matches, {len(cache)} players)")
            except Exception as e:
                logger.error(f"TennisExplorer {match_type} schedule failed: {e}")
        if cache:
            self._schedule_cache = cache
            self._schedule_ts = now

    def find_match_id(self, player_name):
        """Look up a player's match ID from today's schedule.
        TennisExplorer uses 'LastName F.' format, Polymarket uses full names.
        Handles hyphenated names like 'Bautista Agut' → 'bautista-agut r.'
        """
        self._refresh_schedule()
        norm = normalize_name(player_name)
        if norm in self._schedule_cache:
            return self._schedule_cache[norm]
        # TennisExplorer names are "lastname f." — try matching by last name
        last = extract_last_name(player_name)
        # Match: cache key starts with last name (TE format: "kecmanovic m.")
        candidates = [(k, v) for k, v in self._schedule_cache.items()
                       if k.startswith(last + ' ') or k == last]
        if len(candidates) == 1:
            return candidates[0][1]
        # If multiple matches (e.g., two "smith"), try first initial
        if len(candidates) > 1:
            parts = normalize_name(player_name).split()
            if len(parts) >= 2:
                first_initial = parts[0][0]
                refined = [(k, v) for k, v in candidates if len(k.split()) >= 2 and k.split()[1].startswith(first_initial)]
                if len(refined) == 1:
                    return refined[0][1]
        # Handle multi-word last names: "Bautista Agut" → try "bautista-agut"
        # or match any cache key containing the last name
        if not candidates:
            parts = norm.split()
            if len(parts) >= 2:
                # Try hyphenated variants: "bautista agut" → "bautista-agut"
                for i in range(1, len(parts)):
                    hyph = '-'.join(parts[i:])
                    candidates = [(k, v) for k, v in self._schedule_cache.items()
                                   if k.startswith(hyph + ' ') or k.startswith(hyph + '.')]
                    if len(candidates) == 1:
                        return candidates[0][1]
                    # Also try "lastname1-lastname2" where we join last N words
                    hyph2 = '-'.join(parts[i:])
                    candidates = [(k, v) for k, v in self._schedule_cache.items()
                                   if hyph2 in k]
                    if len(candidates) == 1:
                        return candidates[0][1]
        return None

    def _refresh_seeds(self):
        """Scrape draw pages for active Challenger tournaments to get seeding info.
        Seeds appear as [N] after player names on TennisExplorer draw pages."""
        now = time.time()
        if self._seed_cache_ts and (now - self._seed_cache_ts) < self._seed_cache_ttl:
            return
        seeds = {}
        # Scrape draw pages from the schedule page — look for tournament links
        for match_type, draw_type in [('atp-challenger', 'atp-men'),
                                       ('wta-challenger', 'wta-women')]:
            try:
                url = f"{self.BASE}/matches/?type={match_type}"
                resp = self.session.get(url, timeout=15)
                if resp.status_code != 200:
                    continue
                # Extract tournament draw links from schedule page
                # Pattern: /tournament-name/year/draw-type/
                tourney_links = set(re.findall(
                    r'href="(/[a-z0-9-]+/\d{4}/' + re.escape(draw_type) + r'/)"',
                    resp.text
                ))
                for tlink in tourney_links:
                    try:
                        time.sleep(0.5)
                        draw_url = f"{self.BASE}{tlink}"
                        dresp = self.session.get(draw_url, timeout=15)
                        if dresp.status_code != 200:
                            continue
                        # Extract seeded players: <a href="/player/name/">Name</a> [N]
                        seed_matches = re.findall(
                            r'<a href="/player/[^"]+/">([^<]+)</a>\s*\[(\d+)\]',
                            dresp.text
                        )
                        for pname, seed_num in seed_matches:
                            norm = normalize_name(pname)
                            if norm:
                                seeds[norm] = int(seed_num)
                    except Exception as e:
                        logger.debug(f"TennisExplorer draw scrape failed for {tlink}: {e}")
            except Exception as e:
                logger.error(f"TennisExplorer seed scrape failed for {match_type}: {e}")
        if seeds:
            self._seed_cache = seeds
            self._seed_cache_ts = now
            logger.info(f"  TennisExplorer: loaded {len(seeds)} seeded players from draw pages")

    def get_seed(self, player_name):
        """Look up a player's tournament seed. Returns seed number or None."""
        self._refresh_seeds()
        norm = normalize_name(player_name)
        if norm in self._seed_cache:
            return self._seed_cache[norm]
        # Try last-name matching (TE uses "LastName" format)
        last = extract_last_name(player_name)
        candidates = [(k, v) for k, v in self._seed_cache.items()
                       if k == last or k.startswith(last + ' ')]
        if len(candidates) == 1:
            return candidates[0][1]
        return None

    def is_seeded_vs_unseeded(self, fav_name, dog_name):
        """Check if favorite is seeded and opponent is unseeded."""
        fav_seed = self.get_seed(fav_name)
        dog_seed = self.get_seed(dog_name)
        return fav_seed is not None and dog_seed is None

    def fetch_match_detail(self, match_id):
        """
        Fetch and parse a match-detail page.
        Returns dict with players, rankings, set scores, or None.
        """
        now = time.time()
        if match_id in self._detail_ts and (now - self._detail_ts[match_id]) < self._detail_ttl:
            return self._detail_cache.get(match_id)
        try:
            url = f"{self.BASE}/match-detail/?id={match_id}"
            time.sleep(1)  # pace requests to avoid TE rate limiting
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                return None
            html = resp.text
        except Exception as e:
            logger.error(f"TennisExplorer match {match_id} fetch failed: {e}")
            return None

        # Parse the gDetail section
        idx = html.find('result gDetail')
        if idx < 0:
            return None
        section = html[idx:idx+2000]

        # Player names (in order: player1, player2)
        names = re.findall(r'<a href="/player/[^"]*">(.*?)</a>', section)
        if len(names) < 2:
            return None

        # Overall score (e.g. "2 : 0" or "&nbsp;" if not started)
        score_match = re.search(r'gScore">\s*(.*?)\s*<', section)
        overall = score_match.group(1).strip() if score_match else ''

        # Set scores in parentheses (e.g. "6-4, 6-2")
        sets_match = re.search(r'<span>\((.*?)\)</span>', section)
        sets_str = sets_match.group(1) if sets_match else ''

        # Rankings from the tbody (format: <td class="tr">91.</td>)
        rankings = re.findall(r'<td class="t[rl]">(\d+)\.</td>', section)

        # Parse set scores
        sets = parse_polymarket_score(sets_str) if sets_str else []

        # Determine S1 status
        s1_complete = False
        s1_p1 = s1_p2 = None
        if sets:
            s1_p1, s1_p2 = sets[0]
            if len(sets) >= 2:
                s1_complete = True
            elif s1_p1 >= 6 and s1_p2 <= s1_p1 - 2:
                s1_complete = True
            elif s1_p1 == 7:
                s1_complete = True
            elif s1_p2 >= 6 and s1_p1 <= s1_p2 - 2:
                s1_complete = True
            elif s1_p2 == 7:
                s1_complete = True

        home_rank = int(rankings[0]) if len(rankings) >= 1 else None
        away_rank = int(rankings[1]) if len(rankings) >= 2 else None

        # Populate rank cache from match-detail data
        home_norm = normalize_name(names[0])
        away_norm = normalize_name(names[1])
        if home_rank and home_norm:
            self._rank_cache[home_norm] = home_rank
        if away_rank and away_norm:
            self._rank_cache[away_norm] = away_rank

        result = {
            'home_name': names[0],
            'away_name': names[1],
            'home_rank': home_rank,
            'away_rank': away_rank,
            's1_home': s1_p1,
            's1_away': s1_p2,
            's1_complete': s1_complete,
            'sets': sets,
            'overall': overall,
            'match_id': match_id,
            'source': 'TennisExplorer',
        }
        self._detail_cache[match_id] = result
        self._detail_ts[match_id] = now
        return result

    def get_live_score(self, player_name):
        """
        Convenience: find match ID and fetch score for a player.
        Returns parsed match data or None.
        """
        mid = self.find_match_id(player_name)
        if not mid:
            return None
        return self.fetch_match_detail(mid)

    def get_rank(self, player_name):
        """
        Look up a player's ranking from cached match-detail data.
        Returns rank (int) or None.
        """
        norm = normalize_name(player_name)
        # Direct match
        if norm in self._rank_cache:
            return self._rank_cache[norm]
        # TE stores "lastname firstname", Polymarket uses "firstname lastname" — try reversed
        parts = norm.split()
        if len(parts) >= 2:
            reversed_name = ' '.join(parts[1:]) + ' ' + parts[0]
            if reversed_name in self._rank_cache:
                return self._rank_cache[reversed_name]
        # Last-name match
        last = extract_last_name(player_name)
        candidates = [(k, v) for k, v in self._rank_cache.items()
                       if k.startswith(last + ' ') or k.endswith(' ' + last)]
        if len(candidates) == 1:
            return candidates[0][1]
        # Not cached yet — fetch their match-detail to populate rank cache
        detail = self.get_live_score(player_name)
        if detail:
            # Re-check cache (fetch_match_detail populates it)
            if norm in self._rank_cache:
                return self._rank_cache[norm]
            parts = norm.split()
            if len(parts) >= 2:
                reversed_name = ' '.join(parts[1:]) + ' ' + parts[0]
                if reversed_name in self._rank_cache:
                    return self._rank_cache[reversed_name]
            # Direct match against detail
            if names_match(player_name, detail['home_name']) and detail['home_rank']:
                return detail['home_rank']
            if names_match(player_name, detail['away_name']) and detail['away_rank']:
                return detail['away_rank']
        return None


def get_live_balance(client):
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        bal = client.get_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL, signature_type=1))
        raw = int(bal.get('balance', 0))
        return raw / 1_000_000
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")
        return 0


# ─── SOFASCORE CLIENT ────────────────────────────────────────────────────────

class ESPNClient:
    """Fetches live tennis scores and rankings from ESPN free API."""

    def __init__(self):
        self.session = requests.Session()
        self._rankings_cache = {}   # {norm_name: rank}
        self._rankings_ts = None
        self._rankings_ttl = 86400  # 24h

    def _refresh_rankings(self):
        """Fetch ATP + WTA rankings, build name→rank lookup."""
        now = time.time()
        if self._rankings_ts and (now - self._rankings_ts) < self._rankings_ttl:
            return
        cache = {}
        for label, url in [('ATP', ESPN_ATP_RANKINGS), ('WTA', ESPN_WTA_RANKINGS)]:
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code != 200:
                    logger.warning(f"ESPN {label} rankings: HTTP {resp.status_code}")
                    continue
                data = resp.json()
                for ranking_list in data.get('rankings', []):
                    for entry in ranking_list.get('ranks', []):
                        rank = entry.get('current', 0)
                        name = entry.get('athlete', {}).get('displayName', '')
                        if name and rank > 0:
                            cache[normalize_name(name)] = rank
                count = len(cache)
                logger.info(f"  ESPN: {label} rankings loaded ({count} total)")
            except Exception as e:
                logger.error(f"ESPN {label} rankings failed: {e}")
        if cache:
            self._rankings_cache = cache
            self._rankings_ts = now

    def get_rank(self, player_name):
        """Look up a player's current ranking. Returns None if not found."""
        self._refresh_rankings()
        norm = normalize_name(player_name)
        if norm in self._rankings_cache:
            return self._rankings_cache[norm]
        # Try last-name matching as fallback
        last = extract_last_name(player_name)
        candidates = [(k, v) for k, v in self._rankings_cache.items() if k.endswith(' ' + last)]
        if len(candidates) == 1:
            return candidates[0][1]
        return None

    def fetch_live_matches(self):
        """
        Fetch all in-progress tennis matches from ESPN.
        Returns list of dicts with parsed match data.
        """
        matches = []
        for tour_label, url in [('ATP', ESPN_ATP_SCOREBOARD), ('WTA', ESPN_WTA_SCOREBOARD)]:
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code != 200:
                    logger.warning(f"ESPN {tour_label} scoreboard: HTTP {resp.status_code}")
                    continue
                data = resp.json()
            except Exception as e:
                logger.error(f"ESPN {tour_label} scoreboard failed: {e}")
                continue

            for event in data.get('events', []):
                tourney_name = event.get('name', '')
                for grp in event.get('groupings', []):
                    grouping_name = grp.get('grouping', {}).get('displayName', '')
                    # Only singles
                    if 'singles' not in grouping_name.lower():
                        continue

                    for comp in grp.get('competitions', []):
                        status = comp.get('status', {})
                        status_name = status.get('type', {}).get('name', '')
                        status_period = status.get('period', 0)

                        # We want in-progress OR recently finished (to catch exits)
                        if status_name not in ('STATUS_IN_PROGRESS', 'STATUS_FINAL'):
                            continue

                        competitors = comp.get('competitors', [])
                        if len(competitors) < 2:
                            continue

                        # ESPN uses order 1=home, 2=away — but for tennis it's just player1/player2
                        p1 = competitors[0]
                        p2 = competitors[1]
                        p1_name = p1.get('athlete', {}).get('displayName', '')
                        p2_name = p2.get('athlete', {}).get('displayName', '')
                        if not p1_name or not p2_name:
                            continue

                        # Linescores = set-by-set games
                        ls1 = p1.get('linescores', [])
                        ls2 = p2.get('linescores', [])

                        # Set 1 score
                        s1_p1 = int(ls1[0].get('value', 0)) if len(ls1) > 0 else None
                        s1_p2 = int(ls2[0].get('value', 0)) if len(ls2) > 0 else None

                        # S1 complete if we're in period 2+
                        s1_complete = (status_period >= 2 and s1_p1 is not None and s1_p2 is not None)
                        # Or if status is final
                        if status_name == 'STATUS_FINAL' and s1_p1 is not None:
                            s1_complete = True

                        # Rankings from cache (ESPN doesn't embed in scoreboard)
                        p1_rank = self.get_rank(p1_name)
                        p2_rank = self.get_rank(p2_name)

                        # Round info from notes
                        round_name = ''
                        for note in comp.get('notes', []):
                            text = note.get('text', '')
                            if text:
                                round_name = text
                                break

                        match = {
                            'home_name': p1_name,
                            'away_name': p2_name,
                            'home_rank': p1_rank,
                            'away_rank': p2_rank,
                            's1_home': s1_p1,
                            's1_away': s1_p2,
                            's1_complete': s1_complete,
                            'sets_home': sum(1 for ls in ls1 if ls.get('winner')),
                            'sets_away': sum(1 for ls in ls2 if ls.get('winner')),
                            'status_desc': status.get('type', {}).get('detail', ''),
                            'status_type': status_name,
                            'tourney_name': tourney_name,
                            'category': tour_label,
                            'round_name': round_name,
                            'is_live': status_name == 'STATUS_IN_PROGRESS',
                        }
                        matches.append(match)

        return matches


# ─── TRADING BOT ─────────────────────────────────────────────────────────────

class TennisTradingBot:
    def __init__(self, clob_client, espn_client, dry_run=False, explorer_client=None):
        self.client = clob_client
        self.espn = espn_client
        self.explorer = explorer_client or TennisExplorerClient()
        self.dry_run = dry_run
        if not dry_run and clob_client:
            self.bankroll = get_live_balance(clob_client)
        else:
            self.bankroll = INITIAL_BANKROLL
        if self.bankroll <= 0:
            self.bankroll = INITIAL_BANKROLL
        self.open_positions = {}
        self.market_positions = {}
        self.entered_tokens = set()
        self._api_exited = set()
        self.closed_positions = []
        self.scan_count = 0
        self.total_wagered = 0
        self.total_pnl = 0
        self._load_state()

        mode = "DRY RUN" if dry_run else "LIVE"
        logger.info(f"{'='*70}")
        logger.info(f"  Tennis Bot v2 — 3-Layer (Seed+Ratio+S1) — {mode} — ${self.bankroll:.2f}")
        logger.info(f"  Max entry: {MAX_ENTRY_PRICE*100:.0f}c | Max concurrent: {MAX_CONCURRENT}")
        logger.info(f"  Stop-loss: {STOP_LOSS*100:.0f}%")
        logger.info(f"  Tiers: S(40%/$100) → A(25%/$75) → B(15%/$50) → C(6%/$25)")
        logger.info(f"{'='*70}")

    # ── State persistence ──

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    state = json.load(f)
                self.open_positions = state.get('open_positions', {})
                self.market_positions = state.get('market_positions', {})
                self.entered_tokens = set(state.get('entered_tokens', []))
                self.bankroll = state.get('bankroll', self.bankroll)
                self.total_wagered = state.get('total_wagered', 0)
                self.total_pnl = state.get('total_pnl', 0)
                self.closed_positions = state.get('closed_positions', [])
                n = len(self.open_positions)
                logger.info(f"  Loaded state: {n} open positions, ${self.bankroll:.2f} cash")
                for tid, pos in self.open_positions.items():
                    logger.info(f"    {pos['outcome'][:30]} @ {pos['entry_price']:.3f} "
                                f"| ${pos['cost']:.2f} | Tier {pos.get('tier', '?')}")
            except Exception as e:
                logger.error(f"State load failed: {e}")

    def _save(self):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump({
                    'open_positions': self.open_positions,
                    'market_positions': self.market_positions,
                    'entered_tokens': list(self.entered_tokens),
                    'bankroll': self.bankroll,
                    'total_wagered': self.total_wagered,
                    'total_pnl': self.total_pnl,
                    'closed_positions': self.closed_positions[-50:],
                }, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"State save failed: {e}")

    def _log_trade_csv(self, pos, pnl, won, exit_price, exit_reason):
        fields = [
            "exit_ts", "tier", "event", "outcome", "entry_price",
            "exit_price", "shares", "cost", "pnl", "won", "exit_reason",
            "entry_ts", "ratio", "s1_margin", "category", "market_id", "token_id",
        ]
        write_header = not os.path.exists(TRADES_CSV)
        try:
            with open(TRADES_CSV, "a", newline="") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(fields)
                w.writerow([
                    datetime.now(timezone.utc).isoformat(),
                    pos.get("tier", ""),
                    pos.get("event", ""),
                    pos.get("outcome", ""),
                    pos.get("entry_price", ""),
                    exit_price,
                    pos.get("shares", ""),
                    pos.get("cost", ""),
                    f"{pnl:.4f}",
                    won,
                    exit_reason,
                    pos.get("entry_ts", ""),
                    pos.get("ratio", ""),
                    pos.get("s1_margin", ""),
                    pos.get("category", ""),
                    pos.get("market_id", ""),
                    pos.get("token_id", ""),
                ])
        except Exception as e:
            logger.warning(f"  CSV write failed: {e}")

    # ── Exposure tracking ──

    def total_exposure(self):
        return sum(p['cost'] for p in self.open_positions.values())

    def market_exposure(self, market_id):
        return self.market_positions.get(market_id, 0)

    # ── Market discovery ──

    def _fetch_polymarket_tennis(self):
        """Fetch tennis markets from Polymarket Gamma API."""
        all_markets = []
        seen_events = set()

        et_now = datetime.now(timezone(timedelta(hours=-5)))
        today = et_now.strftime('%Y-%m-%d')
        yesterday = (et_now - timedelta(days=1)).strftime('%Y-%m-%d')

        # Tag 864 ("Tennis") is small (~50 events) — one broad query catches everything
        urls = [
            f"{GAMMA_API}/events?tag_id={TAG_ID}&active=true&closed=false&limit=200",
        ]

        all_events = []
        for base_url in urls:
            offset = 0
            while True:
                try:
                    url = f"{base_url}&offset={offset}" if offset > 0 else base_url
                    resp = requests.get(url, timeout=15)
                    resp.raise_for_status()
                    events = resp.json()
                except Exception as e:
                    logger.error(f"Gamma fetch failed: {e}")
                    break
                if not events:
                    break
                for ev in events:
                    eid = ev.get("id")
                    if eid and eid not in seen_events:
                        seen_events.add(eid)
                        all_events.append(ev)
                offset += 200
                if len(events) < 200:
                    break

        for event in all_events:
            # Capture event-level score/period (Polymarket-native live data)
            ev_score = event.get("score", "") or ""
            ev_period = event.get("period", "") or ""
            ev_live = event.get("live", False)

            for mkt in event.get("markets", []):
                slug = mkt.get("slug", "") or ""

                # Only tennis markets
                if not any(slug.startswith(p) for p in TENNIS_PREFIXES):
                    continue
                if mkt.get("sportsMarketType", "") != "moneyline":
                    continue

                # Parse elapsed time from gameStartTime
                elapsed_min = 0
                gst = mkt.get("gameStartTime", "")
                if gst:
                    try:
                        start = datetime.fromisoformat(
                            gst.replace("Z", "+00:00").replace(" ", "T").split("+")[0] + "+00:00"
                        )
                        mins = (datetime.now(timezone.utc) - start).total_seconds() / 60
                        if mins > 0:
                            elapsed_min = mins
                    except:
                        pass

                # Skip matches that haven't started or are very old
                if elapsed_min <= 0 or elapsed_min > 600:
                    continue

                outcomes_raw = mkt.get("outcomes", "[]")
                prices_raw = mkt.get("outcomePrices", "[]")
                tokens_raw = mkt.get("clobTokenIds", "[]")
                try: outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
                except: outcomes = []
                try: prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                except: prices = []
                try: token_ids = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
                except: token_ids = []

                if len(outcomes) < 2 or len(prices) < 2 or len(token_ids) < 2:
                    continue

                skip = False
                for o in outcomes:
                    if o in SKIP_OUTCOMES:
                        skip = True
                if skip:
                    continue

                liquidity = float(mkt.get("liquidity", 0) or 0)
                best_ask = float(mkt.get("bestAsk", 0) or 0)
                best_bid = float(mkt.get("bestBid", 0) or 0)

                # Parse S1 from Polymarket-native score (e.g. "6-3, 0-2")
                poly_sets = parse_polymarket_score(ev_score)
                poly_s1_complete = False
                poly_s1_home = None
                poly_s1_away = None
                if poly_sets:
                    poly_s1_home, poly_s1_away = poly_sets[0]
                    # S1 is complete if we're in set 2+ (period "S2", "S3", etc.)
                    # or if there are 2+ sets in the score
                    if len(poly_sets) >= 2:
                        poly_s1_complete = True
                    elif ev_period and re.match(r'S([2-9])', ev_period):
                        poly_s1_complete = True
                    elif poly_s1_home >= 6 and poly_s1_away <= poly_s1_home - 2:
                        poly_s1_complete = True  # e.g. 6-3 is clearly complete
                    elif poly_s1_home == 7:
                        poly_s1_complete = True  # tiebreak win
                    elif poly_s1_away >= 6 and poly_s1_home <= poly_s1_away - 2:
                        poly_s1_complete = True
                    elif poly_s1_away == 7:
                        poly_s1_complete = True

                for i in range(len(outcomes)):
                    try: prob = float(prices[i])
                    except: continue
                    all_markets.append({
                        'event_name': event.get("title", ""),
                        'slug': slug,
                        'outcome': outcomes[i],
                        'implied_prob': prob,
                        'best_ask': best_ask if i == 0 else (1 - best_bid),
                        'liquidity': liquidity,
                        'game_elapsed': elapsed_min,
                        'token_id': token_ids[i] if i < len(token_ids) else "",
                        'market_id': str(mkt.get("id", "")),
                        'condition_id': mkt.get("conditionId", ""),
                        'neg_risk': mkt.get("negRisk", False),
                        'tick_size': str(mkt.get("orderPriceMinTickSize", "0.01")),
                        # Polymarket-native live score data
                        'poly_score': ev_score,
                        'poly_period': ev_period,
                        'poly_live': ev_live,
                        'poly_s1_home': poly_s1_home,
                        'poly_s1_away': poly_s1_away,
                        'poly_s1_complete': poly_s1_complete,
                        # Both outcomes for this market (player names)
                        'all_outcomes': outcomes,
                    })

        logger.info(f"  Polymarket: {len(all_events)} events, {len(all_markets)} tennis outcomes")
        return all_markets

    # ── Category classification ──

    def _classify_category(self, event_name, slug, tourney_name='', fav_rank=None, dog_rank=None):
        """
        Classify a match into one of: ATP_TOUR, ATP_SLAM, ATP_CHALL,
        WTA_TOUR, WTA_SLAM, WTA_CHALL
        """
        is_atp = slug.startswith('atp-')
        is_wta = slug.startswith('wta-')

        event_lower = (event_name or '').lower()
        tourney_lower = (tourney_name or '').lower()
        combined = event_lower + ' ' + tourney_lower

        # Detect Slam
        is_slam = any(s in combined for s in SLAM_NAMES)

        # Detect Challenger: event title or both players ranked 100+
        is_challenger = 'challenger' in combined or '125' in combined
        if not is_challenger and fav_rank and dog_rank:
            if fav_rank > 80 and dog_rank > 80:
                is_challenger = True

        if is_atp:
            if is_slam:
                return 'ATP_SLAM'
            if is_challenger:
                return 'ATP_CHALL'
            return 'ATP_TOUR'
        elif is_wta:
            if is_slam:
                return 'WTA_SLAM'
            if is_challenger:
                return 'WTA_CHALL'
            return 'WTA_TOUR'
        return None

    # ── Tier matching ──

    def _find_tier(self, category, ratio, s1_margin, is_seeded_vs_unseed=False,
                   fav_rank=None, is_favorite=True):
        """
        Find the highest tier this trade qualifies for.
        3-layer system: seeding + ranking ratio + S1 margin.
        Favorite filter: S1-margin-alone tiers (no seed/ratio) require the S1
        winner to be the better-ranked player. Underdog S1 winners (77% WR)
        need seeding or ratio confirmation to qualify.
        Returns (tier_name, bet_pct, bet_cap) or None.
        """
        for cond in TIERS:
            if category not in cond['categories']:
                continue
            if s1_margin < cond['s1_margin_min']:
                continue
            if cond.get('ratio_min', 0) > 0 and ratio < cond['ratio_min']:
                continue
            if cond.get('seed_vs_unseed') and not is_seeded_vs_unseed:
                continue
            # S1-margin-alone tier (no seed, no ratio) → must be favorite
            if not cond.get('seed_vs_unseed') and cond.get('ratio_min', 0) == 0 and not is_favorite:
                continue  # underdog S1 winner — needs seed/ratio to qualify
            return cond['name'], cond['bet_pct'], cond.get('bet_cap')
        return None

    # ── Match SofaScore ↔ Polymarket ──

    def _match_live_to_poly(self, poly_outcome, poly_event, live_matches):
        """
        Try to match a Polymarket outcome (player name) to an ESPN live match.
        Returns the match dict and which side ('home'/'away') the poly outcome is, or None.
        """
        poly_name = poly_outcome.strip()

        for sm in live_matches:
            if names_match(poly_name, sm['home_name']):
                return sm, 'home'
            if names_match(poly_name, sm['away_name']):
                return sm, 'away'

        # Fallback: try matching against event name (which contains both player names)
        event_norm = normalize_name(poly_event)
        for sm in live_matches:
            home_last = extract_last_name(sm['home_name'])
            away_last = extract_last_name(sm['away_name'])
            if home_last in event_norm and away_last in event_norm:
                # Found the match — now figure out which side poly_outcome is
                poly_last = extract_last_name(poly_name)
                if poly_last == home_last:
                    return sm, 'home'
                elif poly_last == away_last:
                    return sm, 'away'
        return None, None

    # ── Entry logic ──

    def _find_opportunities(self, markets, live_matches):
        """
        Merge Polymarket markets with live data (ESPN or Polymarket-native).
        Find qualifying entries based on ranking ratio + S1 margin.
        """
        candidates = []

        for m in markets:
            tid = m['token_id']
            if not tid:
                continue
            if tid in self.entered_tokens or tid in self.open_positions:
                continue
            if len(self.open_positions) >= MAX_CONCURRENT:
                break

            prob = m['implied_prob']
            if prob >= SKIP_ABOVE_PRICE:
                continue

            # --- Score source 1: ESPN live match ---
            # Always orient from this outcome's perspective first,
            # then we'll flip to S1 winner's perspective below
            our_name = our_rank = opp_name = opp_rank = None
            our_s1 = opp_s1 = None
            s1_complete = False
            tourney_name = ''
            score_source = None

            sm, side = self._match_live_to_poly(m['outcome'], m['event_name'], live_matches)
            if sm and sm['s1_complete']:
                score_source = 'ESPN'
                tourney_name = sm.get('tourney_name', '')
                if side == 'home':
                    our_name, opp_name = sm['home_name'], sm['away_name']
                    our_rank, opp_rank = sm['home_rank'], sm['away_rank']
                    our_s1, opp_s1 = sm['s1_home'], sm['s1_away']
                else:
                    our_name, opp_name = sm['away_name'], sm['home_name']
                    our_rank, opp_rank = sm['away_rank'], sm['home_rank']
                    our_s1, opp_s1 = sm['s1_away'], sm['s1_home']
                s1_complete = True

            # --- Score source 2: Polymarket-native score ---
            if not s1_complete and m.get('poly_s1_complete'):
                score_source = 'POLY'
                poly_s1_home = m['poly_s1_home']
                poly_s1_away = m['poly_s1_away']
                all_outcomes = m.get('all_outcomes', [])

                # Polymarket score is from "home" perspective (outcome[0] = home)
                # Determine which outcome we are
                our_outcome = m['outcome']
                if len(all_outcomes) >= 2:
                    if our_outcome == all_outcomes[0]:
                        our_s1, opp_s1 = poly_s1_home, poly_s1_away
                        our_name, opp_name = all_outcomes[0], all_outcomes[1]
                    else:
                        our_s1, opp_s1 = poly_s1_away, poly_s1_home
                        our_name, opp_name = all_outcomes[1], all_outcomes[0]
                    s1_complete = True

            # --- Score source 3: TennisExplorer (Challengers) ---
            if not s1_complete:
                all_outcomes = m.get('all_outcomes', [])
                our_outcome = m['outcome']
                te_data = self.explorer.get_live_score(our_outcome)
                if not te_data and len(all_outcomes) >= 2:
                    opp_outcome = all_outcomes[1] if our_outcome == all_outcomes[0] else all_outcomes[0]
                    te_data = self.explorer.get_live_score(opp_outcome)
                if te_data and te_data['s1_complete']:
                    score_source = 'TE'
                    if names_match(our_outcome, te_data['home_name']):
                        our_s1, opp_s1 = te_data['s1_home'], te_data['s1_away']
                        our_name, opp_name = te_data['home_name'], te_data['away_name']
                        our_rank, opp_rank = te_data['home_rank'], te_data['away_rank']
                    elif names_match(our_outcome, te_data['away_name']):
                        our_s1, opp_s1 = te_data['s1_away'], te_data['s1_home']
                        our_name, opp_name = te_data['away_name'], te_data['home_name']
                        our_rank, opp_rank = te_data['away_rank'], te_data['home_rank']
                    else:
                        opp_out = all_outcomes[1] if our_outcome == all_outcomes[0] else all_outcomes[0]
                        if names_match(opp_out, te_data['home_name']):
                            our_s1, opp_s1 = te_data['s1_away'], te_data['s1_home']
                            our_name, opp_name = te_data['away_name'], te_data['home_name']
                            our_rank, opp_rank = te_data['away_rank'], te_data['home_rank']
                        elif names_match(opp_out, te_data['away_name']):
                            our_s1, opp_s1 = te_data['s1_home'], te_data['s1_away']
                            our_name, opp_name = te_data['home_name'], te_data['away_name']
                            our_rank, opp_rank = te_data['home_rank'], te_data['away_rank']
                        else:
                            te_data = None
                    if te_data:
                        s1_complete = True

            if not s1_complete:
                continue
            if our_s1 is None or opp_s1 is None:
                continue

            # ── Flip to S1 winner's perspective ──
            # We bet on whoever won S1, regardless of which outcome we're iterating.
            # If this outcome LOST S1, skip — the other outcome's iteration will handle it.
            if our_s1 < opp_s1:
                continue  # this outcome lost S1; the S1 winner's iteration will pick it up
            if our_s1 == opp_s1:
                continue  # tie / incomplete

            s1_margin = our_s1 - opp_s1

            # Get rankings — ESPN first, TennisExplorer fallback
            if not our_rank:
                our_rank = self.espn.get_rank(our_name)
            if not opp_rank:
                opp_rank = self.espn.get_rank(opp_name)
            if not our_rank:
                our_rank = self.explorer.get_rank(our_name)
            if not opp_rank:
                opp_rank = self.explorer.get_rank(opp_name)

            # Is S1 winner the favorite (better-ranked)?
            # Used by _find_tier to gate S1-margin-alone tiers.
            # True if better-ranked or if rankings unknown.
            is_favorite = not (our_rank and opp_rank and our_rank >= opp_rank)

            # Compute ranking ratio (0 if rankings unavailable)
            ratio = 0
            if our_rank and opp_rank and our_rank < opp_rank:
                ratio = opp_rank / our_rank

            # Check seeding (TennisExplorer draw pages)
            is_seeded_vs_unseed = self.explorer.is_seeded_vs_unseeded(
                our_name or m['outcome'],
                opp_name or ''
            )

            # Classify category
            category = self._classify_category(
                m['event_name'], m['slug'], tourney_name,
                fav_rank=our_rank, dog_rank=opp_rank
            )
            if not category:
                continue

            # Find tier — uses favorite filter + seeding + ratio + S1 margin
            tier_result = self._find_tier(
                category, ratio, s1_margin,
                is_seeded_vs_unseed=is_seeded_vs_unseed, fav_rank=our_rank,
                is_favorite=is_favorite
            )
            if not tier_result:
                continue
            tier_name, bet_pct, bet_cap = tier_result

            # Check entry price
            exec_price = m.get('best_ask', 0)
            if exec_price <= 0 or exec_price > 0.99:
                exec_price = prob
            if exec_price > MAX_ENTRY_PRICE:
                logger.info(f"  -- {m['outcome'][:25]} Tier {tier_name} but price {exec_price:.3f} > {MAX_ENTRY_PRICE}")
                continue
            if tier_name == 'B' and exec_price > MAX_ENTRY_TIER_B:
                logger.info(f"  -- {m['outcome'][:25]} Tier B but price {exec_price:.3f} > {MAX_ENTRY_TIER_B} (Tier B cap)")
                continue

            candidates.append({
                **m,
                'tier': tier_name,
                'bet_pct': bet_pct,
                'bet_cap': bet_cap,
                'ratio': ratio,
                's1_margin': s1_margin,
                'fav_rank': our_rank,
                'dog_rank': opp_rank,
                'fav_name': our_name,
                'dog_name': opp_name,
                'category': category,
                'exec_price': exec_price,
                'tourney': tourney_name,
                'score_source': score_source,
                'seeded_vs_unseed': is_seeded_vs_unseed,
            })

        # Sort by tier priority (S first), then by ratio (higher = better)
        tier_order = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4}
        candidates.sort(key=lambda x: (tier_order.get(x['tier'], 9), -x['ratio']))
        return candidates

    # ── Order execution ──

    def _place_order(self, token_id, price, size, info):
        if self.dry_run:
            logger.info(f"  [DRY] Buy {size:.1f} shr {info['outcome'][:25]} @ {price:.3f}")
            return True
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            import time as _time
            buy_price = 0.99  # market order — cross any ask
            size = max(5.0, float(int(info.get('bet_size', size * price) / price)))
            order_args = OrderArgs(
                price=buy_price, size=size,
                side="BUY", token_id=token_id
            )
            signed = self.client.create_order(order_args)
            logger.info(f"  Market buy {size:.0f} shr @ {buy_price} (imp={price:.3f})")
            resp = self.client.post_order(signed, OrderType.FOK)
            logger.info(f"  Order response: {resp}")
            if resp and resp.get("success"):
                order_id = resp.get('orderID', '')
                _time.sleep(4)
                try:
                    check = self.client.get_order(order_id)
                    if check:
                        status = check.get('status', '').upper()
                        matched = float(check.get('size_matched', 0) or 0)
                        logger.info(f"  Verify: status={status} matched={matched}/{size}")
                        if status == 'MATCHED' or matched >= size * 0.9:
                            return True
                        elif status == 'LIVE' and matched == 0:
                            logger.warning(f"  Not filled, cancelling...")
                            try: self.client.cancel(order_id)
                            except: pass
                            return False
                        elif matched > 0:
                            return True
                        else:
                            try: self.client.cancel(order_id)
                            except: pass
                            return False
                    return False
                except Exception as e:
                    logger.warning(f"  Verify failed: {e}")
                    return False
            logger.error(f"  Order rejected: {resp}")
            return False
        except Exception as e:
            logger.error(f"  Order error: {e}")
            return False

    def _sell_position(self, token_id, price, size, pos):
        if self.dry_run:
            logger.info(f"  [DRY] Sell {size:.1f} shr {pos['outcome'][:25]} @ {price:.3f}")
            return True
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            args = OrderArgs(price=price, size=size, side='SELL', token_id=token_id)
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed, OrderType.FOK)
            if resp and resp.get('success'):
                return True
            resp2 = self.client.post_order(signed, OrderType.GTC)
            if resp2 and resp2.get('success'):
                return True
            logger.warning(f"  Sell failed: {resp}")
            return False
        except Exception as e:
            err_msg = str(e).lower()
            if 'does not exist' in err_msg or 'not enough balance' in err_msg:
                logger.info(f"  Market resolved/redeemed: {pos['outcome'][:25]}")
                return 'RESOLVED'
            logger.error(f"  Sell error: {e}")
            return False

    def _get_token_price(self, token_id):
        try:
            result = self.client.get_last_trade_price(token_id)
            if result:
                price = float(result.get('price', 0))
                if price > 0:
                    return price
        except Exception as e:
            logger.debug(f"Price check failed for {token_id[:12]}: {e}")
        return None

    # ── Execute entries ──

    def _execute_entries(self, candidates):
        for c in candidates:
            tid = c['token_id']
            mid = c.get('market_id', '')

            if len(self.open_positions) >= MAX_CONCURRENT:
                break

            current_exposure = self.total_exposure()
            total_capital = self.bankroll + current_exposure
            max_deploy = total_capital * MAX_TOTAL_EXPOSURE_PCT - current_exposure
            if max_deploy < MIN_BET:
                logger.info(f"  Total exposure cap hit ({current_exposure:.0f} deployed)")
                break

            market_exp = self.market_exposure(mid)
            market_room = total_capital * MAX_PER_MARKET_PCT - market_exp
            if market_room < MIN_BET:
                continue

            # Tier-based sizing
            bet_size = total_capital * c['bet_pct']
            if c['bet_cap'] is not None:
                bet_size = min(bet_size, c['bet_cap'])
            bet_size = min(bet_size, market_room, max_deploy)

            if self.bankroll < bet_size:
                bet_size = self.bankroll
            if bet_size < MIN_BET:
                continue

            exec_price = c['exec_price']

            # Verify price via CLOB
            clob_price = self._get_token_price(tid)
            if clob_price and clob_price > 0:
                if clob_price > MAX_ENTRY_PRICE:
                    logger.info(f"  CLOB price {clob_price:.3f} > {MAX_ENTRY_PRICE} for {c['outcome'][:25]}")
                    continue

            shares = bet_size / exec_price
            if shares < 5:
                shares = 5
                bet_size = shares * exec_price
                if bet_size > self.bankroll:
                    continue

            if tid in self.entered_tokens or tid in self.open_positions:
                continue

            success = self._place_order(tid, exec_price, shares,
                                        {**c, 'bet_size': bet_size})
            if success:
                self.open_positions[tid] = {
                    'token_id': tid, 'event': c['event_name'],
                    'outcome': c['outcome'], 'tier': c['tier'],
                    'entry_price': exec_price, 'shares': shares,
                    'cost': bet_size, 'market_id': mid,
                    'ratio': round(c['ratio'], 1),
                    's1_margin': c['s1_margin'],
                    'fav_rank': c['fav_rank'], 'dog_rank': c['dog_rank'],
                    'category': c['category'],
                    'entry_ts': datetime.now(timezone.utc).isoformat(),
                    'event_slug': c.get('slug', ''),
                }
                self.entered_tokens.add(tid)
                self.bankroll -= bet_size
                self.total_wagered += bet_size
                if mid:
                    self.market_positions[mid] = self.market_positions.get(mid, 0) + bet_size

                seed_tag = " SEED" if c.get('seeded_vs_unseed') else ""
                ratio_tag = f" ratio={c['ratio']:.1f}x" if c['ratio'] > 0 else ""
                logger.info(f"  ENTRY Tier {c['tier']} | {c['outcome'][:30]} @ {exec_price:.3f} | "
                    f"${bet_size:.2f} ({shares:.0f} shr) | "
                    f"S1+{c['s1_margin']}{seed_tag}{ratio_tag} | "
                    f"rank {c.get('fav_rank','?')}v{c.get('dog_rank','?')} | "
                    f"{c['category']} | Bank: ${self.bankroll:.2f}")
                self._save()

    # ── Exit logic ──

    def _check_exits(self, markets):
        mkt_by_token = {m['token_id']: m for m in markets if m['token_id']}
        for tid in list(self.open_positions):
            pos = self.open_positions[tid]
            m = mkt_by_token.get(tid)
            if m:
                clob = self._get_token_price(tid)
                gamma = m.get('implied_prob', 0)
                if clob and clob > 0:
                    prob = min(clob, gamma) if gamma > 0 else clob
                else:
                    prob = gamma
            else:
                prob = self._get_token_price(tid)
                if prob is None:
                    entry_ts = pos.get('entry_ts', '')
                    if entry_ts:
                        try:
                            entered = datetime.fromisoformat(entry_ts)
                            age_hrs = (datetime.now(timezone.utc) - entered).total_seconds() / 3600
                            if age_hrs > 6:
                                logger.info(f"  Stale position ({age_hrs:.0f}h): {pos['outcome'][:25]} — attempting sell @0.99")
                                prob = 0.99
                            else:
                                continue
                        except:
                            continue
                    else:
                        continue
                else:
                    logger.info(f"  Off-scan price: {pos['outcome'][:25]} @ {prob:.3f}")

            action = None
            if prob >= 0.99:
                action = 'SELL_WIN'
            elif prob <= STOP_LOSS:
                action = 'SELL_LOSS'
            if not action:
                continue

            won = action == 'SELL_WIN'
            sell_price = min(prob, 0.99) if won else prob

            success = self._sell_position(tid, sell_price, pos['shares'], pos)
            if success == 'RESOLVED':
                cost = pos['cost']
                pnl = pos['shares'] - cost if won else -cost
                self.total_pnl += pnl
                emoji = 'W' if won else 'L'
                logger.info(f"  AUTO-RESOLVED ({emoji}) | {pos['outcome'][:25]} | cost=${cost:.2f} | PnL: ${pnl:+.2f}")
                self._log_trade_csv(pos, pnl, won, 1.0 if won else 0.0, "AUTO_RESOLVED")
                del self.open_positions[tid]
                self._save()
                continue
            if not success and not self.dry_run:
                if won:
                    success = self._sell_position(tid, sell_price - 0.01, pos['shares'], pos)
                    if success == 'RESOLVED':
                        cost = pos['cost']
                        pnl = pos['shares'] - cost
                        self.total_pnl += pnl
                        logger.info(f"  AUTO-RESOLVED (W) | {pos['outcome'][:25]} | PnL: ${pnl:+.2f}")
                        self._log_trade_csv(pos, pnl, True, 1.0, "AUTO_RESOLVED")
                        del self.open_positions[tid]
                        self._save()
                        continue
                    if success:
                        sell_price -= 0.01
                if not success:
                    logger.warning(f"  Sell failed {pos['outcome'][:25]} @{prob:.3f} - retry next cycle")
                    continue

            sell_revenue = pos['shares'] * sell_price
            cost = pos['cost']
            pnl = sell_revenue - cost
            self.bankroll += sell_revenue
            mid = pos.get('market_id', '')
            if mid in self.market_positions:
                self.market_positions[mid] = max(0, self.market_positions[mid] - cost)
                if self.market_positions[mid] <= 0:
                    del self.market_positions[mid]
            self.total_pnl += pnl
            self.closed_positions.append({
                **pos, 'pnl': pnl, 'won': won, 'exit_price': sell_price,
                'exit_ts': datetime.now(timezone.utc).isoformat()
            })
            exit_reason = "WIN" if won else "STOP_LOSS"
            self._log_trade_csv(pos, pnl, won, sell_price, exit_reason)
            tag = 'WIN' if won else 'LOSS'
            logger.info(f"  {tag} | {pos['outcome'][:25]} | Tier {pos.get('tier','?')} | "
                f"in@{pos['entry_price']:.3f} out@{sell_price:.3f} | "
                f"${cost:.2f} -> ${sell_revenue:.2f} (PnL: ${pnl:+.2f}) | Bank: ${self.bankroll:.2f}")
            del self.open_positions[tid]
            self._save()

    def _check_exits_from_api(self):
        """Query actual on-chain positions and sell anything at 99c+ or stop-loss."""
        try:
            funder = os.getenv("POLYMARKET_FUNDER")
            if not funder:
                return
            resp = requests.get(
                f"https://data-api.polymarket.com/positions?user={funder}",
                timeout=10)
            if resp.status_code != 200:
                return
            positions = resp.json()
        except Exception as e:
            logger.error(f"  Position API failed: {e}")
            return

        for p in positions:
            size = float(p.get("size", 0))
            cur_price = float(p.get("curPrice", 0))
            avg_price = float(p.get("avgPrice", 0))
            token_id = p.get("asset", "")
            outcome = p.get("outcome", "?")

            if size < 0.1 or cur_price <= 0:
                continue
            if token_id in self._api_exited:
                continue
            # Only process tennis positions (check if in our state)
            if token_id not in self.open_positions:
                continue

            action = None
            if cur_price >= 0.99:
                action = "SELL_WIN"
            elif cur_price < 0.01:
                cost = self.open_positions[token_id]['cost'] if token_id in self.open_positions else 0
                if token_id in self.open_positions:
                    pos = self.open_positions[token_id]
                    self.closed_positions.append({
                        **pos, 'pnl': -cost, 'won': False,
                        'exit_price': 0, 'exit_ts': datetime.now(timezone.utc).isoformat()
                    })
                    self._log_trade_csv(pos, -cost, False, 0.0, "WRITE_OFF")
                    del self.open_positions[token_id]
                self.total_pnl -= cost
                self._api_exited.add(token_id)
                logger.info(f"  WRITE-OFF | {outcome[:25]} | PnL: ${-cost:+.2f}")
                self._save()
                continue
            elif cur_price <= STOP_LOSS:
                action = "STOP_LOSS"

            if not action:
                continue

            sell_price = min(cur_price, 0.99) if action == "SELL_WIN" else cur_price
            logger.info(f"  API-EXIT ({action}) | {outcome[:25]} | {size:.1f} shr @ {sell_price:.3f}")

            success = self._sell_position(token_id, sell_price, size, {"outcome": outcome})
            if success and success != "RESOLVED":
                revenue = size * sell_price
                cost = size * avg_price
                pnl = revenue - cost
                self.bankroll += revenue
                self.total_pnl += pnl
                pos = self.open_positions.get(token_id, {"outcome": outcome, "tier": "?"})
                if token_id in self.open_positions:
                    del self.open_positions[token_id]
                self._api_exited.add(token_id)
                won = action == "SELL_WIN"
                tag = "WIN" if won else "STOP-LOSS"
                logger.info(f"  {tag} (API) | {outcome[:25]} | "
                    f"in@{avg_price:.3f} out@{sell_price:.3f} | PnL: ${pnl:+.2f}")
                self._log_trade_csv(pos, pnl, won, sell_price, f"API_{tag}")
                self._save()
            elif success == "RESOLVED":
                self._api_exited.add(token_id)
                pos = self.open_positions.get(token_id, {"outcome": outcome, "tier": "?"})
                if token_id in self.open_positions:
                    del self.open_positions[token_id]
                logger.info(f"  RESOLVED (API) | {outcome[:25]}")

    # ── Status display ──

    def print_status(self):
        w = sum(1 for p in self.closed_positions if p.get('won'))
        l = len(self.closed_positions) - w
        exp = self.total_exposure()
        total_capital = self.bankroll + exp
        logger.info(f"\n{'='*70}")
        logger.info(f"  TENNIS BOT #{self.scan_count} | Cash: ${self.bankroll:.2f} | "
            f"Deployed: ${exp:.2f} ({exp/total_capital*100:.0f}% of ${total_capital:.2f}) | "
            f"{w}W/{l}L | PnL: ${self.total_pnl:+.2f}")
        for t, p in self.open_positions.items():
            logger.info(f"    Tier {p.get('tier','?')} | {p['outcome'][:30]} @ {p['entry_price']:.3f} "
                        f"| ${p['cost']:.2f} | ratio={p.get('ratio',0)}x S1+{p.get('s1_margin',0)} "
                        f"| {p.get('category','')}")
        logger.info(f"{'='*70}\n")

    # ── Main loop ──

    def run_once(self):
        self.scan_count += 1

        # Sync balance every 10 cycles
        if self.scan_count % 10 == 0 and not self.dry_run:
            live_bal = get_live_balance(self.client)
            if live_bal > 0:
                open_cost = self.total_exposure()
                self.bankroll = max(live_bal - open_cost, 0)

        # Fetch ESPN live matches
        live_matches = self.espn.fetch_live_matches()
        in_progress = [m for m in live_matches if m.get('is_live')]
        s1_ready = sum(1 for m in in_progress if m['s1_complete'])
        logger.info(f"Scan #{self.scan_count}: ESPN {len(in_progress)} live ({s1_ready} S1 complete)")

        # Fetch Polymarket tennis markets
        markets = self._fetch_polymarket_tennis()
        poly_s1 = sum(1 for m in markets if m.get('poly_s1_complete'))
        if poly_s1 > 0:
            logger.info(f"  Polymarket-native S1: {poly_s1} outcomes with S1 score")

        # Pre-warm TennisExplorer schedule + seed caches (for Challengers)
        self.explorer._refresh_schedule()
        self.explorer._refresh_seeds()
        te_matches = len(set(self.explorer._schedule_cache.values()))
        te_seeds = len(self.explorer._seed_cache)
        if te_matches > 0 or te_seeds > 0:
            logger.info(f"  TennisExplorer: {te_matches} matches, {te_seeds} seeded players")

        # Check exits
        self._check_exits(markets)

        # API exit check every 5 cycles
        if self.scan_count % 5 == 0:
            self._check_exits_from_api()

        # Find and execute entries
        candidates = self._find_opportunities(markets, in_progress)
        if candidates:
            logger.info(f"  {len(candidates)} entry candidates:")
            for c in candidates[:5]:
                seed_tag = " SEED" if c.get('seeded_vs_unseed') else ""
                ratio_tag = f" ratio={c['ratio']:.1f}x" if c['ratio'] > 0 else ""
                logger.info(f"    Tier {c['tier']} | {c['outcome'][:30]} @ {c['exec_price']:.3f} | "
                    f"S1+{c['s1_margin']}{seed_tag}{ratio_tag} | "
                    f"rank {c.get('fav_rank','?')}v{c.get('dog_rank','?')} | {c['category']} "
                    f"[{c.get('score_source', '?')}]")
        self._execute_entries(candidates)

        # Status every 5 cycles
        if self.scan_count % 5 == 0:
            self.print_status()

    def run(self):
        logger.info("Starting tennis bot loop...")
        running = True
        def stop(s, f):
            nonlocal running
            running = False
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        self.print_status()
        while running:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Error: {e}")
                logger.debug(traceback.format_exc())
            try:
                time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                break
        self.print_status()
        self._save()


def main():
    dry_run = "--dry-run" in sys.argv
    once = "--once" in sys.argv
    no_confirm = "--no-confirm" in sys.argv

    espn = ESPNClient()
    explorer = TennisExplorerClient()

    if not dry_run:
        print(f"\n  LIVE TENNIS BOT — Bankroll: ${INITIAL_BANKROLL:.2f}")
        print(f"  Tiers: S(50%) A(30%) B(20%) C(12-15%) D(6-8%)")
        print(f"  Max entry: {MAX_ENTRY_PRICE*100:.0f}c | Max concurrent: {MAX_CONCURRENT}")
        if not no_confirm:
            if input("Type 'GO': ").strip() != "GO":
                print("Aborted.")
                sys.exit(0)
        client = setup_clob_client()
    else:
        print(f"\n  DRY RUN — Tennis Bot\n")
        client = None

    bot = TennisTradingBot(client, espn, dry_run=dry_run, explorer_client=explorer)
    if once:
        bot.run_once()
        bot.print_status()
    else:
        bot.run()


if __name__ == "__main__":
    main()
