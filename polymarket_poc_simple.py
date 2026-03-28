#!/usr/bin/env python3
"""
Polymarket Sports Monitor - Simple POC
Fetches live individual game markets from Polymarket's Gamma API.

Uses tag_id=100639 (game bets) to get actual game-level markets
(e.g. "Lakers vs Celtics") instead of season-long futures.
Paginates through ALL results, then filters to events where
startDate is in the past (game has already started) and market
is still active. Shows Gamma prices + CLOB buy prices.
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone

GAMMA_EVENTS_API = "https://gamma-api.polymarket.com/events"
GAMMA_SPORTS_API = "https://gamma-api.polymarket.com/sports"
CLOB_PRICE_API = "https://clob.polymarket.com/price"

# tag_id for individual game bets (not futures)
GAME_BETS_TAG_ID = 100639
PAGE_SIZE = 100


def _api_get(url):
    """Make a GET request with standard headers."""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "polymarket-monitor/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_leagues():
    """Fetch available leagues from the /sports endpoint."""
    return _api_get(GAMMA_SPORTS_API)


def fetch_all_game_events():
    """Paginate through ALL active game-level events using tag_id=100639."""
    all_events = []
    offset = 0

    while True:
        url = (
            f"{GAMMA_EVENTS_API}"
            f"?tag_id={GAME_BETS_TAG_ID}"
            f"&active=true&closed=false"
            f"&limit={PAGE_SIZE}&offset={offset}"
            f"&order=startDate&ascending=false"
        )
        page = _api_get(url)
        if not page:
            break
        all_events.extend(page)
        print(f"  fetched page offset={offset}, got {len(page)} events "
              f"(total so far: {len(all_events)})", file=sys.stderr)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_events


def parse_start_date(event):
    """Parse the startDate field from an event. Returns None on failure."""
    sd = event.get("startDate")
    if not sd:
        return None
    try:
        return datetime.fromisoformat(sd.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def filter_started_events(events):
    """Filter to events where startDate < now (game already started)
    and market is still active. No 'today only' constraint — a game
    that started at 11:30pm yesterday and is still live at 12:30am
    today is included."""
    now = datetime.now(timezone.utc)
    started = []
    future = []

    for event in events:
        start_date = parse_start_date(event)
        if start_date is None:
            continue
        if start_date <= now:
            started.append(event)
        else:
            future.append(event)

    return started, future


def fetch_clob_price(token_id):
    """Fetch the BUY price from the CLOB for a given token ID."""
    url = f"{CLOB_PRICE_API}?token_id={token_id}&side=BUY"
    try:
        data = _api_get(url)
        return data.get("price")
    except Exception:
        return None


def _parse_json_field(raw):
    """Parse a field that may be a JSON string or already a list."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(raw, list):
        return raw
    return []


def print_market_prices(event):
    """Print Gamma prices and CLOB buy prices for each market in an event."""
    for market in event.get("markets", []):
        question = market.get("question", "?")
        outcomes = _parse_json_field(market.get("outcomes"))
        outcome_prices = _parse_json_field(market.get("outcomePrices"))
        clob_ids = _parse_json_field(market.get("clobTokenIds"))
        best_bid = market.get("bestBid")
        best_ask = market.get("bestAsk")
        last_trade = market.get("lastTradePrice")
        volume_24h = market.get("volume24hr", 0)
        liquidity = market.get("liquidity", 0)

        print(f"    Market: {question}")
        print(f"      Gamma bestBid={best_bid}  bestAsk={best_ask}  "
              f"lastTrade={last_trade}")
        print(f"      vol24h=${volume_24h}  liquidity=${liquidity}")

        for i, outcome in enumerate(outcomes):
            implied_prob = outcome_prices[i] if i < len(outcome_prices) else "?"
            token_id = clob_ids[i] if i < len(clob_ids) else None
            clob_buy = fetch_clob_price(token_id) if token_id else None
            clob_str = clob_buy if clob_buy else "n/a"
            print(f"      [{outcome}]  implied={implied_prob}  "
                  f"CLOB_BUY={clob_str}")


def main():
    print("Polymarket Live Game Monitor - POC")
    print("=" * 60)
    print()

    # Show available leagues
    leagues = fetch_leagues()
    league_names = [lg.get("sport", "?") for lg in leagues]
    print(f"Leagues tracked: {len(leagues)} "
          f"({', '.join(league_names[:10])}, ...)")
    print()

    # Paginate through ALL game-level events
    print(f"Fetching all game events (tag_id={GAME_BETS_TAG_ID})...")
    events = fetch_all_game_events()
    print(f"Total game events fetched: {len(events)}")
    print()

    # Filter: startDate < now (game has started) + still active
    started, future = filter_started_events(events)
    now = datetime.now(timezone.utc)
    print(f"Current UTC: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Started (startDate in past, still active): {len(started)}")
    print(f"  Future  (startDate not yet reached):       {len(future)}")
    print()

    if future:
        print(f"EXCLUDED — {len(future)} future games (not yet started):")
        print("-" * 60)
        for i, event in enumerate(future[:5]):
            title = event.get("title", "?")
            sd = event.get("startDate", "?")
            print(f"  {i+1}. {title}")
            print(f"     startDate: {sd}")
        if len(future) > 5:
            print(f"  ... and {len(future) - 5} more")
        print()

    # Show live games with prices (cap display to first 15 for readability)
    display_count = min(len(started), 15)
    print(f"LIVE GAMES — {len(started)} total "
          f"(showing first {display_count} with prices):")
    print("-" * 60)
    for i, event in enumerate(started[:display_count]):
        title = event.get("title", "?")
        sd = event.get("startDate", "?")
        print(f"  {i+1}. {title}")
        print(f"     startDate: {sd}")
        print_market_prices(event)
        print()

    if len(started) > display_count:
        print(f"  ... and {len(started) - display_count} more live markets")
    print()
    print(f"SUMMARY: {len(started)} live game markets "
          f"(from {len(events)} total, {len(future)} future excluded)")


if __name__ == "__main__":
    main()
