# CLAUDE.md

This file provides guidance for AI assistants working with this repository.

## Repository Overview

- **Name**: anthropic
- **Owner**: kuchak
- **Status**: New project (initial setup)

## Project Structure

This repository is in its initial state. As the project grows, document the directory layout here:

```
/
├── CLAUDE.md          # AI assistant guidance (this file)
├── planning/          # Plans, strategies, and roadmaps (.md files)
└── (project files)    # To be added
```

## Development Setup

### Prerequisites

<!-- Update with actual requirements as the project develops -->
- Git

### Getting Started

```bash
git clone <repository-url>
cd anthropic
# Add setup steps as project develops (e.g., dependency installation)
```

## Build & Run

<!-- Update these sections as build tooling is added -->

| Task    | Command |
| ------- | ------- |
| Build   | TBD     |
| Test    | TBD     |
| Lint    | TBD     |
| Format  | TBD     |

## Testing

<!-- Document testing patterns once a test framework is adopted -->
- Test framework: TBD
- Test location: TBD
- Run all tests: TBD
- Run a single test: TBD

## Code Conventions

<!-- Update as the team establishes conventions -->
- Follow consistent formatting (configure a formatter once the language/framework is chosen)
- Write clear commit messages describing the "why" not just the "what"
- Keep changes focused — one logical change per commit

## Architecture

<!-- Document key architectural decisions and patterns as they are made -->

No architecture decisions have been recorded yet. Update this section as the project takes shape.

## Key Files

<!-- List important files and their purposes as they are created -->

| File        | Purpose                                  |
| ----------- | ---------------------------------------- |
| CLAUDE.md   | AI assistant guidance                    |
| planning/   | Plans, strategies, and roadmap documents |

## Planning & Strategy Documents

All planning documents, strategy write-ups, and architectural plans live in the `./planning/` folder as Markdown files.

- When the user asks for a plan, strategy, roadmap, or any forward-looking document, save it as a `.md` file in `./planning/`
- Use descriptive filenames with kebab-case (e.g., `api-migration-plan.md`, `q2-growth-strategy.md`)
- If the `./planning/` folder doesn't exist yet, create it before writing the document

## Bot Components

| Bot | Script | Purpose |
|-----|--------|---------|
| Monitor | `polymarket_monitor.py` | Market data collection (30s cycles) |
| Trader | `polymarket_trader.py` | Live sports trading bot |
| Tennis Trader | `tennis_trader.py` | Tennis bot — 3-layer (seed + ratio + S1 margin) strategy |
| Whale Tracker | `whale-tracker/whale_tracker.py` | Whale & insider trade alerts (60s cycles) |
| Copy Trade | `copy-trade-monitor/copy_trade_monitor.py` | Leaderboard copy-trade tracker |
| Crypto Monitor | `crypto_monitor.py` | Passive crypto data collection (60s cycles) — Phase 1 |
| Crypto Trader | `crypto_trader.py` | Live crypto trading bot — 15m & 1h Up/Down markets |

### Running Bots

```bash
# Monitor
nohup python3 polymarket_monitor.py > nohup.out 2>&1 &

# Trader (--no-confirm for background mode)
nohup python3 polymarket_trader.py --no-confirm > trader_output.log 2>&1 &

# Whale Tracker
cd whale-tracker && nohup python3 whale_tracker.py > ../whale.log 2>&1 &

# Crypto Monitor (Phase 1 — passive data collection, no trades)
nohup python3 crypto_monitor.py > crypto_monitor.log 2>&1 &

# Crypto Trader (Phase 2 — live trading, $150 allocation)
nohup python3 crypto_trader.py --no-confirm > crypto_trader.log 2>&1 &

# Tennis Trader (3-layer: seed + ratio + S1 margin)
nohup python3 tennis_trader.py --no-confirm > tennis_trader.log 2>&1 &
```

## Crypto Monitor (Phase 1)

Passive data collector for BTC/ETH/SOL/XRP markets. No trading — build dataset for backtesting.

### Markets Tracked
| Timeframe | Assets | Count at any time |
|-----------|--------|-------------------|
| 5 min | BTC, ETH, SOL, XRP | ~35 per asset |
| 15 min | BTC, ETH, SOL, XRP | ~11 per asset |
| 1 hour | BTC, ETH, SOL, XRP | ~2 per asset |
| 4 hour | BTC, ETH, SOL, XRP | ~1 per asset |
| Daily Above | BTC, ETH | 11 thresholds each |

### Output Files
| File | Contents |
|------|----------|
| `data/crypto_snapshots.csv` | Probability time series (~238 rows/cycle) |
| `data/crypto_resolutions.csv` | Final outcomes (Up/Down/YES/NO per market) |
| `data/crypto_state.json` | Pending resolution tracking (restart-safe) |

### CSV Schema
**crypto_snapshots.csv**: `timestamp, event_slug, series_slug, asset, timeframe, market_type, threshold_price, outcome, implied_prob, liquidity, volume_24h, minutes_to_expiry, price_approx`

**crypto_resolutions.csv**: `resolved_timestamp, event_slug, series_slug, asset, timeframe, market_type, threshold_price, winning_outcome`

### API Discovery
- Up/Down: broad query `GET /events?closed=false&end_date_min=now&end_date_max=now+5h`, filter by `seriesSlug`
- Daily Above: slug-based `GET /events?slug=bitcoin-above-on-march-6`
- Resolution: batch query `GET /events?closed=true&end_date_min=now-20m&end_date_max=now`

## Trader Bot Parameters (as of March 10, 2026)

### Risk Controls
| Parameter | Value |
|-----------|-------|
| MIN_LIQUIDITY | $50,000 |
| MAX_TOTAL_EXPOSURE | 100% |
| MAX_PER_MARKET | 20% |
| Stop-loss | Sell when prob <= 40% |

### Entry Thresholds & Bet Sizing (per-sport, wired into cfg['max_per_bet_pct'])
| Sport | Threshold | Min Elapsed | Bet % | All-time WR | Rationale |
|-------|-----------|-------------|-------|-------------|-----------|
| WTA | 92% | 30 min | **28%** | 100% (23-0) | Perfect record |
| NBA | 91% | 0 min | **27%** | 97.8% (44-1) | Near-perfect |
| NCAA_CBB | 93% | 60 min | **22%** | 96.2% (51-2) | 2 pre-v7 blowups |
| ATP | 94% | 45 min | **18%** | 89.7% (26-3) | 3 historical losses |
| WTT_Women | 88% | 0 min | **15%** | 100% | n=1 sample cap |
| WTT_Men | 88% | 0 min | **15%** | 100% | n=1 sample cap |
| CWBB | 90% | 45 min | **15%** | unproven | Thin markets |
| NHL | 90% | 30 min | **15%** | unproven | threshold lowered 93%→90% |

### Performance (161W/7L all-time, Feb 26 - Mar 10, 2026)
- Post-v7 (Mar 7-10): 50W/0L, bankroll $305 → $474
- Best: WTA (100% all-time), NBA (97.8%)
- Full analysis: `planning/trading-bot-performance-analysis.md`

## Crypto Trader (Phase 2)

Live trading bot for BTC/ETH/XRP 15m and 1h Up/Down markets. SOL 15m excluded (net loser, 71% WR). Entries at prob ≥99% skipped (rounding artifact).

### Parameters
| Parameter | Value |
|-----------|-------|
| Entry threshold | 90% (skip if ≥99%) |
| 15m time window | 3–13 min remaining |
| 1h time window | 10–50 min remaining |
| Min bet | $10 |
| Max concurrent | 6 positions |
| Stop-loss | 15m: 82% / 1h: 40% |

### Bet Sizing (per-market % of available bankroll — Tier 1 gets larger allocation)
| Market | Bet % | WR | ROI/bet | Tier |
|--------|-------|----|---------|------|
| BTC 1h | **30%** | 100% (12-0) | 7.8% | 1 |
| BTC 15m | **10%** | 78% (14-4) | -0.1% | 3 |
| XRP 1h | **30%** | 100% (13-0) | 4.0% | 1 |
| ETH 15m | **10%** | 87% (13-2) | 0.3% | 4 |
| XRP 15m | **8%** | 88% (7-1) | 2.5% | 2 |
| ETH 1h | **15%** | 91% (10-1) | 1.9% | 3 |
| SOL 15m | dropped | 71% (5-2) | -9.2% | — |
| Target exit | 99% |
| Wallet allocation | $150 (hard cap, prevents conflict with sports bot) |
| Balance sync | Every 10 cycles (~5 min) |
| API exit check | Every 5 cycles (~2.5 min) |

### State File
`data/crypto_bot_state.json` — separate from sports bot's `data/state.json`

### Auto-resolution Handling
- `_sell()` returns `"RESOLVED"` (string) when CLOB says "does not exist" — market already redeemed on-chain
- `_check_exits()` handles `"RESOLVED"` distinctly: no revenue added (USDC already credited by Polymarket), PnL calculated from shares vs cost
- Retry at `price - 0.01` when FOK sell fails on a win (stuck-at-99c pattern)
- `_check_exits_from_api()` queries `data-api.polymarket.com/positions` every 5 cycles for on-chain sweep

### Wallet Isolation
- `WALLET_ALLOCATION = 150.0` — this bot never uses more than $150 of the shared wallet
- `_sync_balance()` caps reported balance at `min(live_usdc, WALLET_ALLOCATION)` on every sync
- Sports bot operates on whatever remains above this line — no conflict

## Tennis Trader (v2)

Dedicated tennis trading bot using 3-layer strategy: Tournament Seeding + Ranking Ratio + Set 1 Margin. Uses ESPN + TennisExplorer for live scores/rankings/seedings, Polymarket for market entry/exit.

### Data Sources (3-source chain)
| Source | Endpoint | Data |
|--------|----------|------|
| ESPN | `site.api.espn.com/.../atp/scoreboard` | Live scores + rankings (Tour events) |
| Polymarket | `gamma-api.polymarket.com/events` | Native scores, market prices |
| TennisExplorer | `tennisexplorer.com/match-detail/` | Challenger scores + rankings + seeds |

### 3-Layer Strategy (backtested on 2023-2024 JeffSackmann data, 105k matches, exclusive WRs)

**Layer 1: S1 Margin** (always required — must win Set 1)
**Layer 2: Tournament Seeding** (seeded vs unseeded — from TE draw pages)
**Layer 3: Ranking Ratio** (fallback when seeding unavailable)

| Tier | WR | Bet % | Cap | Key Conditions |
|------|-----|-------|-----|----------------|
| **S** | ≥96% | **40%** | $100 | WTA_CHALL: seed+S1≥+5 (96.1%); ATP_CHALL: ratio≥3x+S1≥+4 (96.6%); ATP/WTA Tour: ratio≥5x+S1≥+4 |
| **A** | 93-96% | **25%** | $75 | WTA_CHALL: S1=+6 or seed+S1=+4; ATP_CHALL: ratio≥2x+S1≥+4; WTA_TOUR: ratio≥2x+S1≥+5 |
| **B** | 90-93% | **15%** | $50 | WTA_CHALL: seed+S1=+3; ATP_CHALL: seed+S1≥+5 or ratio≥2x+S1=+3; ATP_TOUR: ratio≥5x+S1=+2/+3 |
| **C** | 88-90% | **6%** | $25 | WTA_CHALL: S1≥+5 (no seed); ATP_CHALL: seed+S1=+4; ATP_TOUR: ratio≥3x+S1=+2/+3 |

### Entry Rules
- Max entry price: **93c**
- Skip entries at **≥99c** (rounding artifact)
- Max concurrent positions: **6**
- Favorite must have **won Set 1** with qualifying margin
- Ranking ratio = underdog_rank / favorite_rank
- Seeding from TennisExplorer draw pages (`[N]` after player names)

### Categories
- `ATP_TOUR` / `ATP_SLAM`: Main tour + Grand Slams
- `ATP_CHALL`: Challenger events (detected by title or both players ranked >80)
- `WTA_TOUR` / `WTA_SLAM`: WTA main tour + Grand Slams
- `WTA_CHALL`: WTA Challenger/125/ITF events

### State Files
- `data/tennis_bot_state.json` — separate from sports/crypto bots
- `data/tennis_trades.csv` — append-only trade log with tier, S1 margin, seed/ratio info

## Changelog

### 2026-03-13: Tennis Trader v2 — 3-Layer Strategy
- **Complete tier system rewrite**: replaced ranking-ratio-only tiers with 3-layer system (Seeding + Ranking Ratio + S1 Margin)
- **Added TennisExplorer seed scraping**: draw pages parsed for `[N]` seed designations, cached per tournament
- **Seeding as primary signal for WTA_CHALL**: seeded vs unseeded + S1≥+5 → 96.1% WR (9,566 matches)
- **Exclusive WR validation**: all tiers validated with exclusive assignment (no double-counting)
- **Backtested on 105k matches** (2023-2024 JeffSackmann data): 38,414 qualifying trades, 92.2% WR
- Tier S: ≥96% WR → 40%/$100 | Tier A: 93-96% → 25%/$75 | Tier B: 90-93% → 15%/$50 | Tier C: 88-90% → 6%/$25
- Cut unprofitable conditions: ratio≥2x+S1=+2 (79-82% exclusive WR), S1=+3/+4 without seed/ratio (80-84%)
- Three score sources: ESPN → Polymarket-native → TennisExplorer (Challengers)
- TennisExplorer ranking fallback for players ESPN doesn't cover

### 2026-03-13: Tennis Trader v1 (superseded by v2)
- Built `tennis_trader.py` — dedicated tennis bot with ranking ratio + S1 margin strategy
- ESPN API integration for live scores (set-by-set) and rankings (top 150 ATP/WTA)
- 5-tier system (S/A/B/C/D) with bet sizing from 50% down to 5%
- Player name matching with diacritics normalization and last-name fallback

### 2026-03-11: Performance-Aligned Bet Sizing (v10)
- **Crypto**: Applied consistent tier framework based on actual WR + ROI from logs (Mar 9–11):
  - XRP 1h: 28% → **30%** (Tier 1: 100% WR, 4.0% ROI, n=13 — matches BTC 1h criteria)
  - ETH 1h: 8% → **15%** (Tier 3: 91% WR, 1.9% ROI — was severely undersized)
  - ETH 15m: 8% → **10%** (Tier 4a: 87% WR, 0.3% ROI)
  - BTC 15m: 29% → **10%** (demoted: 78% WR, -0.1% ROI post Mar-10 flash crash)
  - XRP 15m: stays 8% (Tier 4b: 85% WR, 1.5% ROI — same tier as BTC 15m post-crash)
- **Sports**: Tightened small-sample allocations and fixed dead threshold:
  - WTT_Women/WTT_Men: 20% → **15%** (n=1 sample cap; 20% unwarranted on single trade)
  - NHL: threshold 93% → **90%** (never triggered once in 2+ weeks at 93%)

### 2026-03-11: Per-Timeframe Stop-Loss (v9)
- **Crypto**: `STOP_LOSS` changed from global 40% to per-timeframe dict: `{'15m': 0.82, '1h': 0.40}`
  - 15m markets: 82% stop — backtested on 83k rows; saves 5/5 big losses, only 9% false-stop rate, net +$15.29 vs 40%
  - 1h markets: keep 40% — 97.4% WR, no data supporting tighter stop
  - Rationale: 15m have no recovery time (gap-throughs hit 92%→4% in one tick); 1h markets recover
- **Crypto**: Fixed startup display to show per-timeframe stop values and updated bet sizing display (30/29/28%)

### 2026-03-10: Per-Market Bet Sizing (v8)
- **Sports**: per-sport `max_per_bet_pct` now wired into bet calculation (was ignored, used DEFAULT_BET_PCT=20% for all)
  - WTA 28%, NBA 27%, NCAA_CBB 22%, ATP 18%, CWBB/NHL 15%, WTT 20%
- **Crypto**: replaced global BET_PCT=10%/BET_CAP=$50 with `MARKET_BET_PCT` dict (per-market %)
  - Tier 1: BTC 1h=30%, BTC 15m=29%, XRP 1h=28%
  - Tier 2: ETH/XRP 15m+1h=8% each
- **Crypto**: dropped SOL 15m (71% WR, -9.2% ROI, only net loser)
- **Crypto**: skip entries at prob ≥99% (BTC 1h rounding artifact — all 5 losses were this case)
- **Crypto**: MAX_CONCURRENT raised 4→6

### 2026-03-06: Crypto Trader Launch (Phase 2)
- Built `crypto_trader.py` — live trading for 15m/1h Up/Down markets
- Wallet isolation via `WALLET_ALLOCATION = $150` constant
- Auto-resolution: `_sell()` returns `"RESOLVED"` string for on-chain redeemed positions
- Stuck-at-99c retry: price−0.01 retry on failed win exits
- On-chain sweep: `_check_exits_from_api()` mirrors sports bot pattern
- State persisted to `data/crypto_bot_state.json` (separate from sports bot)

### 2026-03-04: Parameter Optimization v7
- Entry thresholds raised: ATP 93%→94%, NCAA_CBB 92%→93%, CWBB 85%→90%, NBA 88%→91%, WTT 83%→88%
- Stop-loss: sell at prob <= 40% (was 10%) — catches losses earlier
- MIN_LIQUIDITY: $20k→$50k — filters thin Challenger/ITF markets
- Scale-in: remains disabled
- Added `--no-confirm` flag to skip interactive GO prompt for background mode

## Notes for AI Assistants

- This is a new repository — verify what files exist before assuming project structure
- When adding new tooling or frameworks, update this CLAUDE.md with relevant commands and conventions
- Always read existing code before proposing modifications
- Prefer minimal, focused changes over large refactors
- **When assessing bot trading performance, ALWAYS use the trades CSV files** — these are the source of truth:
  - Sports bot: `data/sports_trades.csv`
  - Crypto bot: `data/crypto_trades.csv`
  - Tennis bot: `data/tennis_trades.csv`
  - Do NOT use `*_bot_state.json` files for performance reporting — they can have stale/incomplete data
  - Do NOT use `data/state.json` (monitor only), `data/crypto_state.json` (monitor only), or any other file unless explicitly asked to look at monitor data
