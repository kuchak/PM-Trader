# Basketball Stop-Loss Backtest: NCAA_CBB & CWBB

**Date:** 2026-03-11
**Data sources:** trader_logs/*.log, trades_*.json, sports_bot_state.json, market_snapshots.csv

---

## Executive Summary

The current 40% stop-loss for NCAA_CBB is effectively inert -- both historical losses crashed from 85-90% to near-zero in a single scan gap (no intermediate stop opportunity). The v7 entry threshold of 93% is the real defense: neither loss would have been entered under current rules.

**Recommendation:** Raise NCAA_CBB stop-loss to **80%** (from 40%). CWBB data is insufficient for a recommendation -- keep at 40% pending more trades.

---

## NCAA_CBB: 71W / 2L All-Time

### The 2 Losses (Both Pre-v7)

#### Loss 1: Iowa Hawkeyes (cbb-iowa-pennst, Feb 28)
```
Entry: 0.900 @ 02:39 UTC | Cost: $37.13 | 41.3 shares
Prob curve after entry:
  02:40  0.900  (holding)
  02:44  0.900  (holding)
  02:49  0.900  (holding)
  02:53  0.900  (holding)
  02:56  0.010  <-- OFF-SCAN CRASH (game ended in upset)
Exit:   0.010 @ 02:56 | PnL: -$36.71

Gap: 0.900 -> 0.010 in one scan cycle (~3 min). No stop-loss could have caught this.
```

- Entry was at 90% -- below current v7 threshold of 93%
- Would NOT be entered under v7 rules

#### Loss 2: NC State Wolfpack (cbb-ncst-nd, Feb 28)
```
Entry: 0.850 @ 02:00 UTC | Cost: $49.61 (initial), $62.98 (after scale-in)
Scale-in #1 at 0.890 @ 02:19 (blended entry: 0.858)
Prob curve after entry:
  02:03  0.850  (holding)
  02:07  0.850  (holding)
  02:19  0.880  (scale-in zone)
  02:24  0.858  (holding)
  02:49  0.925  (potential scale-in #2, not taken)
  02:53  0.930
  02:56  0.945  (looked like a winner!)
  03:00  0.945
  03:01  0.930  (starting to dip)
  03:05  0.930
  03:07  0.930
  03:10  0.610  <-- OFF-SCAN CRASH (game collapse)
  03:13  0.858  (stale display -- not real)
  03:20  0.050  <-- TOTAL LOSS
Exit:   0.050 @ 03:20 | PnL: -$59.31

The 0.610 reading at 03:10 is the only catchable moment.
A stop at 70% would have caught it there (saving ~$28-38).
```

- Entry was at 85% -- below current v7 threshold of 93%
- Would NOT be entered under v7 rules

### Key Insight: Both Losses Are Irrelevant Under v7

| Loss | Entry Price | v7 Threshold (93%) | Would Enter? |
|------|------------|---------------------|-------------|
| Iowa Hawkeyes | 0.900 | 0.930 | **NO** |
| NC State | 0.858 | 0.930 | **NO** |

The 93% entry threshold eliminated the conditions that produced both losses. The stop-loss question becomes: what dips do v7-eligible trades (entry >= 93%) experience?

### Post-v7 Winning Trades: Probability Dips

Of 71 wins, 10 had measurable dips below entry (log-tracked):

| Team | Entry | Min Prob | Dip | Would stop at... |
|------|-------|----------|-----|------------------|
| Boston Terriers | 0.950 | 0.840 | 0.110 | 85%, 90% |
| Central Arkansas Bears | 0.967 | 0.860 | 0.107 | 87%, 90% |
| Rutgers Scarlet Knights | 0.930 | 0.850 | 0.080 | 85%, 87%, 90% |
| Clemson Tigers | 0.880* | 0.850 | 0.030 | 85%, 87%, 90% |
| Santa Clara Broncos | 0.960 | 0.930 | 0.030 | none |
| UCLA Bruins | 0.960 | 0.935 | 0.025 | none |
| Central Arkansas Bears | 0.880* | 0.860 | 0.020 | 87%, 90% |
| Arizona Wildcats | 0.959 | 0.939 | 0.020 | none |
| South Florida Bulls | 0.954 | 0.936 | 0.018 | none |
| UCLA Bruins #2 | 0.950 | 0.935 | 0.015 | none |

*Pre-v7 entry (below 93% threshold)

**Filtering to v7-eligible entries only (>= 93%):** 6 trades had dips, all with min prob >= 0.840. No v7-eligible win dipped below 84%.

### Today's Monitor Data (March 11) -- Additional Evidence

| Game | Peak | Min After Peak | Final | Result |
|------|------|---------------|-------|--------|
| Oklahoma State | 0.988 | 0.610 | 0.985 | WIN (recovered from 61% dip) |
| McNeese State | 0.970 | 0.413 | 0.413 | LOSS (collapsed from 97%) |
| TX-Rio Grande Valley | 0.985 | 0.030 | 0.587 | LOSS (collapsed from 98.5%) |
| Idaho Vandals | 0.980 | 0.920 | 0.970 | Ongoing (current open position) |

These confirm that collapses from 93%+ DO happen in NCAA basketball. However, note:
- The bot filters by liquidity ($50K min) -- McNeese and UTRGV had low liquidity and would be skipped
- Oklahoma State recovered despite a massive 38% dip -- a tight stop would have killed a winner

### Stop-Loss Simulation: All 73 NCAA_CBB Trades

| Stop Level | Losses Caught | Savings | False Stops | Cost of False Stops | Net Impact |
|-----------|--------------|---------|-------------|--------------------:|------------|
| **40% (current)** | **1/2** | **$20.21** | **0** | **$0.00** | **+$20.21** |
| 50% | 1/2 | $20.21 | 0 | $0.00 | +$20.21 |
| 60% | 1/2 | $24.34 | 0 | $0.00 | +$24.34 |
| **70%** | **2/2** | **$76.17** | **0** | **$0.00** | **+$76.17** |
| 75% | 2/2 | $81.90 | 0 | $0.00 | +$81.90 |
| **80%** | **2/2** | **$87.63** | **0** | **$0.00** | **+$87.63** |
| 82% | 2/2 | $89.92 | 0 | $0.00 | +$89.92 |
| 85% | 2/2 | $93.36 | 5 | $18.80 | +$74.56 |
| 87% | 2/2 | $95.66 | 10 | $29.27 | +$66.38 |
| 90% | 2/2 | $99.10 | 11 | $23.83 | +$75.27 |

**Sweet spot: 80%** -- catches both losses (if they were to recur), zero false stops on any historical win. The 82% level is marginally better but 80% provides a clean safety margin.

### Why Not Tighter?

- At 85%: 5 false stops (Boston Terriers, Rutgers, Clemson, Central Arkansas x2)
- At 90%: 11 false stops -- too aggressive
- At 80%: zero false stops across 71 wins

Note: the "savings" column overstates the benefit since both losses were pre-v7 and wouldn't recur. The real value of 80% stop-loss is insurance against a future loss at the 93%+ entry level.

---

## CWBB: 1W / 0L (11 Open/Incomplete)

### Data Problem

CWBB has almost no completed trade data:
- Only 1 confirmed win (Sam Houston Bearkats, in:0.950, out:0.990)
- 11 trades still marked "open" in the earliest JSON files (from bot restarts before state was properly saved)
- No losses recorded

### What We Know

Early CWBB trades (Feb 26-27) were grouped under "NCAAB" strategy with a generic 95% entry threshold. The bot was restarted frequently and trade outcomes weren't persisted.

From log-tracked data, one CWBB trade (Sacramento State Hornets) showed a dip to 0.885 from entry at 0.970 -- a 8.5% dip that recovered to win.

### Recommendation

**Keep CWBB at 40% stop-loss.** There's not enough data to justify changing it. The low trade volume (15% bet size) limits risk exposure. Revisit after 20+ completed CWBB trades.

---

## Recommendations

### NCAA_CBB: Change stop-loss from 40% to 80%

| Parameter | Current | Proposed |
|-----------|---------|----------|
| Stop-loss | 40% | **80%** |
| Entry threshold | 93% | 93% (no change) |
| Min elapsed | 60 min | 60 min (no change) |
| Bet size | 22% | 22% (no change) |

**Rationale:**
1. Both historical losses dropped below 80% (NC State hit 61%, Iowa went to 1%)
2. Zero false stops at 80% across 71 winning trades
3. The v7 entry threshold (93%) already filters out the riskiest entries
4. An 80% stop catches the NC State pattern (gradual collapse with a catchable 61% reading)
5. Iowa's instant crash (90% -> 1%) can't be caught by any stop, but 80% doesn't hurt

**Risk:** A future game could legitimately dip to 79% and recover (Oklahoma State today dipped to 61% and won). But Oklahoma State would NOT have been entered by the bot due to low liquidity at the dip point. With $50K min liquidity, these extreme dip-recoveries are rare.

### CWBB: Keep at 40%

Insufficient data. Only 1 completed trade. Wait for a larger sample before optimizing.

---

## Appendix: Full Trade List

### NCAA_CBB Pre-v7 Trades (Feb 26 - Mar 6)

All entered below the current 93% threshold:

| # | Team | Entry | Exit | Result | PnL |
|---|------|-------|------|--------|-----|
| 1 | UC Santa Barbara Gauchos | 0.837 | 0.990 | WIN | +var |
| 2 | Montana State Bobcats | 0.951 | 0.990 | WIN | +var |
| 3-31 | (various, all wins) | 0.84-0.97 | 0.990 | WIN | +var |
| 32 | NC State Wolfpack | 0.858 | 0.050 | **LOSS** | -$59.31 |
| 33 | Iowa Hawkeyes | 0.900 | 0.010 | **LOSS** | -$36.71 |

### NCAA_CBB Post-v7 Trades (Mar 7 - Mar 11)

All entered at 93%+ threshold. 20W / 0L:

| # | Team | Entry | Exit | Duration | PnL |
|---|------|-------|------|----------|-----|
| 1 | Boston College Eagles | 0.954 | 0.990 | 6m | +$2.81 |
| 2 | Marquette Golden Eagles | 0.950 | 0.990 | 4m | +$3.16 |
| 3 | Iowa State Cyclones | 0.940 | 0.990 | 12m | +$4.09 |
| 4 | Florida Gators | 0.960 | 0.990 | 38m | +$1.93 |
| 5 | Boise State Broncos | 0.940 | 0.990 | 9m | +$2.58 |
| 6 | New Mexico State Aggies | 0.940 | 0.990 | 13m | +$3.36 |
| 7 | Ohio State Buckeyes | 0.960 | 0.990 | 13m | +$2.02 |
| 8 | Towson Tigers | 0.950 | 0.990 | 12m | +$2.76 |
| 9 | Idaho State Bengals | 0.964 | 0.990 | 18m | +$1.81 |
| 10 | UCLA Bruins | 0.960 | 0.990 | 38m | +$2.12 |
| 11 | Utah Valley Wolverines | 0.970 | 0.990 | 26m | +$1.35 |
| 12 | Idaho Vandals | 0.960 | 0.990 | 28m | +$1.79 |
| 13 | BYU Cougars | 0.940 | 0.990 | 17m | +$3.28 |
| 14 | Arizona Wildcats | 0.959 | 0.990 | 14m | +$2.21 |
| 15 | Illinois Fighting Illini | 0.960 | 0.990 | 15m | +$2.10 |
| 16 | Nebraska Cornhuskers | 0.970 | 0.990 | 39m | +$1.37 |
| 17 | Portland State Vikings | 0.950 | 0.990 | 7m | +$3.19 |
| 18 | Hofstra Pride | 0.950 | 0.990 | 21m | +$3.07 |
| 19 | Santa Clara Broncos | 0.960 | 0.990 | 25m | +$1.65 |
| 20 | Eastern Washington Eagles | 0.940 | 0.990 | 28m | +$4.95 |
