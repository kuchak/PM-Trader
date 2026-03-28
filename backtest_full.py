#!/usr/bin/env python3
"""
Full bankroll-based backtest for crypto Up/Down trading strategy.
Uses all available snapshot + resolution data (March 6-12, 2026).

Walk through ALL snapshots chronologically. Track bankroll, open positions,
and concurrent count. Each slug entered at most once. Exits checked before
entries on each snapshot.
"""

import csv
import sys
from collections import defaultdict
from itertools import product

DATA_DIR = "/Users/minikrys/polymarket-monitor/data"
SNAPSHOTS_FILE = f"{DATA_DIR}/crypto_snapshots.csv"
RESOLUTIONS_FILE = f"{DATA_DIR}/crypto_resolutions.csv"

# ── Default strategy parameters ──────────────────────────────────────────
DEFAULT_PARAMS = {
    "entry_threshold": 0.90,
    "skip_above": 0.99,
    "stop_loss_15m": 0.85,
    "stop_loss_1h": 0.85,
    "target_exit": 0.99,
    "max_concurrent": 6,
    "starting_bankroll": 150.0,
    "min_bet": 10.0,
    "bet_pct": {
        "BTC_1h": 0.30, "XRP_1h": 0.30, "ETH_1h": 0.15,
        "BTC_15m": 0.10, "ETH_15m": 0.10, "XRP_15m": 0.08,
    },
    "time_windows": {
        "15m": (3, 13),
        "1h": (2, 50),
    },
    "allowed_assets": {"BTC", "ETH", "XRP"},
    "allowed_timeframes": {"15m", "1h"},
}


# ── Data loading ─────────────────────────────────────────────────────────

def load_resolutions():
    """Load resolution outcomes keyed by event_slug."""
    res = {}
    with open(RESOLUTIONS_FILE) as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for parts in reader:
            if len(parts) < 8:
                continue
            event_slug = parts[1]
            timeframe = parts[4]
            mtype = parts[5]
            winning = parts[7].strip()
            if mtype == "up_down" and timeframe in ("15m", "1h"):
                res[event_slug] = winning  # "Up" or "Down"
    return res


def load_snapshots(allowed_assets, allowed_timeframes):
    """Load up_down Up snapshots for allowed assets/timeframes, sorted by timestamp."""
    rows = []
    with open(SNAPSHOTS_FILE) as f:
        f.readline()  # skip header
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 12:
                continue
            # 13-col up_down format:
            # 0:timestamp 1:slug 2:series_slug 3:asset 4:timeframe 5:market_type
            # 6:threshold(empty) 7:outcome 8:prob 9:liquidity 10:volume 11:minutes 12:empty
            if parts[5] != "up_down":
                continue
            asset = parts[3].upper()
            tf = parts[4]
            outcome = parts[7]
            if asset not in allowed_assets:
                continue
            if tf not in allowed_timeframes:
                continue
            if outcome != "Up":
                continue
            try:
                prob = float(parts[8])
                mins = float(parts[11])
            except (ValueError, IndexError):
                continue
            rows.append({
                "timestamp": parts[0],
                "slug": parts[1],
                "asset": asset,
                "timeframe": tf,
                "prob": prob,
                "mins": mins,
            })
    rows.sort(key=lambda r: r["timestamp"])
    return rows


# ── Backtest engine ──────────────────────────────────────────────────────

def run_backtest(snapshots, resolutions, params, track_trades=False):
    """
    Walk through ALL snapshots chronologically.
    Returns dict with bankroll, trades, equity_curve, stats.
    """
    bankroll = params["starting_bankroll"]
    peak_bankroll = bankroll
    max_drawdown = 0.0
    positions = {}      # slug -> {cost, shares, asset, tf, market_key, entry_prob}
    entered_slugs = set()
    trades = []
    equity_events = []  # (timestamp, bankroll) at each trade event

    entry_thresh = params["entry_threshold"]
    skip_above = params.get("skip_above", 0.99)
    sl_15m = params.get("stop_loss_15m", 0.85)
    sl_1h = params.get("stop_loss_1h", 0.85)
    target_exit = params.get("target_exit", 0.99)
    max_conc = params.get("max_concurrent", 6)
    min_bet = params.get("min_bet", 10.0)
    bet_pcts = params.get("bet_pct", DEFAULT_PARAMS["bet_pct"])
    time_windows = params.get("time_windows", DEFAULT_PARAMS["time_windows"])

    for snap in snapshots:
        slug = snap["slug"]
        prob = snap["prob"]
        mins = snap["mins"]
        asset = snap["asset"]
        tf = snap["timeframe"]
        ts = snap["timestamp"]
        market_key = f"{asset}_{tf}"
        stop_loss = sl_15m if tf == "15m" else sl_1h

        # ── Check exits first ────────────────────────────────────
        if slug in positions:
            pos = positions[slug]
            exited = False
            exit_type = None
            revenue = 0.0

            if prob >= target_exit:
                revenue = pos["shares"] * 0.99
                exit_type = "target"
                exited = True
            elif stop_loss > 0 and prob <= stop_loss:
                revenue = pos["shares"] * prob
                exit_type = "stop"
                exited = True

            if exited:
                bankroll += revenue
                pnl = revenue - pos["cost"]
                result = "win" if pnl > 0 else "loss"
                if track_trades:
                    trades.append({
                        "slug": slug, "asset": asset, "tf": tf,
                        "market_key": market_key, "cost": pos["cost"],
                        "revenue": revenue, "pnl": pnl, "result": result,
                        "exit_type": exit_type, "ts": ts,
                        "entry_prob": pos["entry_prob"],
                    })
                else:
                    trades.append({
                        "market_key": market_key, "cost": pos["cost"],
                        "pnl": pnl, "result": result, "exit_type": exit_type,
                    })
                del positions[slug]
                if bankroll > peak_bankroll:
                    peak_bankroll = bankroll
                dd = (peak_bankroll - bankroll) / peak_bankroll if peak_bankroll > 0 else 0
                if dd > max_drawdown:
                    max_drawdown = dd
                equity_events.append((ts, bankroll))
            continue  # don't re-enter same snapshot tick

        # ── Check entries ────────────────────────────────────────
        if slug in entered_slugs:
            continue
        if prob < entry_thresh or prob >= skip_above:
            continue
        tw = time_windows.get(tf)
        if tw is None:
            continue
        if mins < tw[0] or mins > tw[1]:
            continue
        if len(positions) >= max_conc:
            continue
        if bankroll < min_bet:
            continue

        bp = bet_pcts.get(market_key, 0.0)
        if bp <= 0:
            continue
        cost = bp * bankroll
        if cost < min_bet:
            continue

        shares = cost / prob
        bankroll -= cost
        positions[slug] = {
            "cost": cost, "shares": shares, "asset": asset,
            "timeframe": tf, "market_key": market_key, "entry_prob": prob,
        }
        entered_slugs.add(slug)

        if bankroll > peak_bankroll:
            peak_bankroll = bankroll
        dd = (peak_bankroll - bankroll) / peak_bankroll if peak_bankroll > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd
        equity_events.append((ts, bankroll))

    # ── Resolve remaining open positions via resolutions CSV ──────
    for slug, pos in list(positions.items()):
        outcome = resolutions.get(slug)
        if outcome == "Up":
            revenue = pos["shares"] * 1.0
            pnl = revenue - pos["cost"]
            result = "win"
        elif outcome == "Down":
            revenue = 0.0
            pnl = -pos["cost"]
            result = "loss"
        else:
            revenue = 0.0
            pnl = -pos["cost"]
            result = "unresolved"
        bankroll += revenue
        if track_trades:
            trades.append({
                "slug": slug, "asset": pos["asset"], "tf": pos["timeframe"],
                "market_key": pos["market_key"], "cost": pos["cost"],
                "revenue": revenue, "pnl": pnl, "result": result,
                "exit_type": "resolution", "ts": "end",
                "entry_prob": pos["entry_prob"],
            })
        else:
            trades.append({
                "market_key": pos["market_key"], "cost": pos["cost"],
                "pnl": pnl, "result": result, "exit_type": "resolution",
            })
        del positions[slug]

    if bankroll > peak_bankroll:
        peak_bankroll = bankroll
    dd = (peak_bankroll - bankroll) / peak_bankroll if peak_bankroll > 0 else 0
    if dd > max_drawdown:
        max_drawdown = dd

    wins = sum(1 for t in trades if t["result"] == "win")
    losses = sum(1 for t in trades if t["result"] == "loss")
    unresolved = sum(1 for t in trades if t["result"] == "unresolved")
    total = len(trades)
    wr = wins / total * 100 if total > 0 else 0

    return {
        "final_bankroll": bankroll,
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "unresolved": unresolved,
        "win_rate": wr,
        "net_pnl": bankroll - params["starting_bankroll"],
        "max_drawdown_pct": max_drawdown * 100,
        "trades": trades,
        "equity_curve": equity_events,
    }


# ── Reporting ────────────────────────────────────────────────────────────

def print_detailed_results(results, params):
    print("=" * 80)
    print("BACKTEST RESULTS — CURRENT STRATEGY")
    print("=" * 80)
    print(f"Period:             March 6-12, 2026 (~6 days)")
    print(f"Starting bankroll:  ${params['starting_bankroll']:.2f}")
    print(f"Final bankroll:     ${results['final_bankroll']:.2f}")
    pnl_pct = results['net_pnl'] / params['starting_bankroll'] * 100
    print(f"Net PnL:            ${results['net_pnl']:.2f} ({pnl_pct:+.1f}%)")
    print(f"Total trades:       {results['total_trades']}")
    print(f"Wins:               {results['wins']}")
    print(f"Losses:             {results['losses']}")
    print(f"Unresolved:         {results['unresolved']}")
    print(f"Win rate:           {results['win_rate']:.1f}%")
    print(f"Max drawdown:       {results['max_drawdown_pct']:.1f}%")
    print()

    # Per-market breakdown
    market_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0,
                                         "total_pnl": 0.0, "win_pnl": [], "loss_pnl": []})
    worst_trade = None
    for t in results["trades"]:
        mk = t["market_key"]
        market_stats[mk]["trades"] += 1
        market_stats[mk]["total_pnl"] += t["pnl"]
        if t["result"] == "win":
            market_stats[mk]["wins"] += 1
            market_stats[mk]["win_pnl"].append(t["pnl"])
        else:
            market_stats[mk]["losses"] += 1
            market_stats[mk]["loss_pnl"].append(t["pnl"])
        if worst_trade is None or t["pnl"] < worst_trade["pnl"]:
            worst_trade = t

    print("PER-MARKET BREAKDOWN")
    print("-" * 85)
    print(f"{'Market':<12} {'Trades':>6} {'Wins':>5} {'Loss':>5} {'WR':>6} {'AvgWin':>9} {'AvgLoss':>9} {'NetPnL':>10}")
    print("-" * 85)
    for mk in sorted(market_stats.keys()):
        s = market_stats[mk]
        wr = s["wins"] / s["trades"] * 100 if s["trades"] > 0 else 0
        avg_win = sum(s["win_pnl"]) / len(s["win_pnl"]) if s["win_pnl"] else 0
        avg_loss = sum(s["loss_pnl"]) / len(s["loss_pnl"]) if s["loss_pnl"] else 0
        print(f"{mk:<12} {s['trades']:>6} {s['wins']:>5} {s['losses']:>5} {wr:>5.1f}%"
              f" ${avg_win:>8.2f} ${avg_loss:>8.2f} ${s['total_pnl']:>9.2f}")

    # Exit type breakdown
    print()
    exit_stats = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for t in results["trades"]:
        et = t["exit_type"]
        exit_stats[et]["count"] += 1
        exit_stats[et]["pnl"] += t["pnl"]
        if t["result"] == "win":
            exit_stats[et]["wins"] += 1
        else:
            exit_stats[et]["losses"] += 1
    print("EXIT TYPE BREAKDOWN")
    print("-" * 60)
    for et in sorted(exit_stats.keys()):
        s = exit_stats[et]
        print(f"  {et:<14} {s['count']:>4} trades  (W:{s['wins']} L:{s['losses']})  PnL: ${s['pnl']:>8.2f}")

    print()
    if worst_trade:
        print(f"WORST TRADE: {worst_trade['market_key']} slug={worst_trade.get('slug','?')}")
        print(f"  Cost: ${worst_trade['cost']:.2f}, PnL: ${worst_trade['pnl']:.2f},"
              f" Exit: {worst_trade['exit_type']}, Entry@{worst_trade.get('entry_prob',0):.1%}")

    # Equity curve
    print()
    print("EQUITY CURVE (sampled)")
    print("-" * 55)
    ec = results["equity_curve"]
    if ec:
        step = max(1, len(ec) // 40)
        for i in range(0, len(ec), step):
            ts, bal = ec[i]
            ts_str = ts[:19] if ts and ts != "end" else "END"
            bar_len = max(0, int(bal / params["starting_bankroll"] * 30))
            bar = "#" * bar_len
            print(f"  {ts_str:<22} ${bal:>8.2f}  {bar}")
        ts, bal = ec[-1]
        ts_str = ts[:19] if ts and ts != "end" else "END"
        bar_len = max(0, int(bal / params["starting_bankroll"] * 30))
        bar = "#" * bar_len
        print(f"  {ts_str:<22} ${bal:>8.2f}  {bar}")

    # Trade log
    print()
    print("FULL TRADE LOG")
    print("-" * 110)
    print(f"{'#':>3} {'Timestamp':<22} {'Market':<10} {'Entry%':>7} {'Cost':>8} {'Revenue':>8} {'PnL':>8}"
          f" {'Result':<6} {'Exit':<11} {'Slug'}")
    print("-" * 110)
    sorted_trades = sorted(results["trades"], key=lambda t: t.get("ts", "zzz"))
    for i, t in enumerate(sorted_trades, 1):
        ts_str = t.get("ts", "?")[:19] if t.get("ts", "?") != "end" else "END"
        ep = t.get("entry_prob", 0)
        print(f"{i:>3} {ts_str:<22} {t['market_key']:<10} {ep:>6.1%} ${t['cost']:>7.2f}"
              f" ${t.get('revenue',0):>7.2f} ${t['pnl']:>7.2f} {t['result']:<6}"
              f" {t['exit_type']:<11} {t.get('slug','?')[:40]}")


def run_sensitivity_grid(snapshots, resolutions, base_params):
    """Run full backtest for each entry_threshold x stop_loss combination."""
    entry_thresholds = [0.90, 0.91, 0.92, 0.93, 0.94, 0.95]
    stop_losses = [0, 0.75, 0.80, 0.82, 0.85, 0.88, 0.90, 0.92]

    print()
    print("=" * 80)
    print("SENSITIVITY GRID — Entry Threshold x Stop-Loss (uniform SL for 15m and 1h)")
    print("=" * 80)
    header = f"{'Entry':>6} {'SL':>5} | {'Final$':>9} {'Trades':>6} {'WR':>6} {'MaxDD':>6} {'NetPnL':>9}"
    print(header)
    print("-" * len(header))

    best = None
    grid = []

    for et in entry_thresholds:
        for sl in stop_losses:
            p = dict(base_params)
            p["entry_threshold"] = et
            p["stop_loss_15m"] = sl
            p["stop_loss_1h"] = sl
            r = run_backtest(snapshots, resolutions, p, track_trades=False)
            row = {
                "entry": et, "sl": sl,
                "final": r["final_bankroll"], "trades": r["total_trades"],
                "wr": r["win_rate"], "dd": r["max_drawdown_pct"],
                "pnl": r["net_pnl"],
            }
            grid.append(row)

            sl_str = f"{sl:.0%}" if sl > 0 else "none"
            print(f"{et:>5.0%} {sl_str:>5} | ${r['final_bankroll']:>8.2f} {r['total_trades']:>6}"
                  f" {r['win_rate']:>5.1f}% {r['max_drawdown_pct']:>5.1f}% ${r['net_pnl']:>8.2f}")

            if r["total_trades"] > 0:
                if (best is None or r["final_bankroll"] > best["final"]) and r["max_drawdown_pct"] < 25:
                    best = row

    print()
    if best:
        sl_str = f"{best['sl']:.0%}" if best['sl'] > 0 else "none"
        print(f"*** BEST COMBO (highest final bankroll with DD<25%):")
        print(f"    Entry={best['entry']:.0%}, SL={sl_str}")
        print(f"    Final=${best['final']:.2f}, {best['trades']} trades,"
              f" {best['wr']:.1f}% WR, {best['dd']:.1f}% DD, PnL=${best['pnl']:.2f}")
    else:
        print("*** No combo with DD < 25%. Showing best overall:")
        best_all = max(grid, key=lambda x: x["final"])
        sl_str = f"{best_all['sl']:.0%}" if best_all['sl'] > 0 else "none"
        print(f"    Entry={best_all['entry']:.0%}, SL={sl_str}")
        print(f"    Final=${best_all['final']:.2f}, DD={best_all['dd']:.1f}%")

    return grid


def run_per_market_grid(snapshots, resolutions, base_params):
    """Run grid per market to find optimal parameters for each."""
    print()
    print("=" * 80)
    print("PER-MARKET OPTIMAL PARAMETERS")
    print("=" * 80)

    entry_thresholds = [0.90, 0.91, 0.92, 0.93, 0.94, 0.95]
    stop_losses = [0, 0.75, 0.80, 0.82, 0.85, 0.88, 0.90, 0.92]
    markets = ["BTC_15m", "ETH_15m", "XRP_15m", "BTC_1h", "ETH_1h", "XRP_1h"]

    for market in markets:
        asset, tf = market.split("_")
        # Filter snapshots to this market only
        market_snaps = [s for s in snapshots if s["asset"] == asset and s["timeframe"] == tf]
        if not market_snaps:
            print(f"\n{market}: No snapshot data")
            continue

        best = None
        best_rows = []
        print(f"\n{'='*60}")
        print(f"{market} ({len(market_snaps)} snapshots)")
        print(f"{'='*60}")
        print(f"  {'Entry':>6} {'SL':>5} | {'Final$':>9} {'Trades':>6} {'WR':>6} {'DD':>6} {'PnL':>9}")
        print(f"  {'-'*55}")

        # Use original bet_pct for this market, zero out others
        orig_bp = base_params["bet_pct"].get(market, 0.10)

        for et in entry_thresholds:
            for sl in stop_losses:
                p = dict(base_params)
                p["entry_threshold"] = et
                p["stop_loss_15m"] = sl
                p["stop_loss_1h"] = sl
                p["bet_pct"] = {market: orig_bp}
                r = run_backtest(market_snaps, resolutions, p, track_trades=False)
                if r["total_trades"] == 0:
                    continue
                sl_str = f"{sl:.0%}" if sl > 0 else "none"
                print(f"  {et:>5.0%} {sl_str:>5} | ${r['final_bankroll']:>8.2f} {r['total_trades']:>6}"
                      f" {r['win_rate']:>5.1f}% {r['max_drawdown_pct']:>5.1f}% ${r['net_pnl']:>8.2f}")
                if best is None or r["final_bankroll"] > best["final"]:
                    best = {"entry": et, "sl": sl, "final": r["final_bankroll"],
                            "trades": r["total_trades"], "wr": r["win_rate"],
                            "dd": r["max_drawdown_pct"], "pnl": r["net_pnl"]}

        if best:
            sl_str = f"{best['sl']:.0%}" if best['sl'] > 0 else "none"
            print(f"\n  >>> BEST for {market}: Entry={best['entry']:.0%}, SL={sl_str}")
            print(f"      Final=${best['final']:.2f}, {best['trades']}t,"
                  f" {best['wr']:.1f}%WR, {best['dd']:.1f}%DD, PnL=${best['pnl']:.2f}")


def risk_assessment(results, params):
    print()
    print("=" * 80)
    print("RISK ASSESSMENT")
    print("=" * 80)
    final = results["final_bankroll"]
    bet_pcts = params["bet_pct"]

    print(f"\nAt final bankroll of ${final:.2f}:")
    print(f"{'Market':<12} {'Bet%':>5} {'BetSize':>9} {'FullLoss':>10} {'%ofBankroll':>12}")
    print("-" * 55)
    for mk in sorted(bet_pcts.keys()):
        pct = bet_pcts[mk]
        bet_size = pct * final
        print(f"{mk:<12} {pct:>4.0%}  ${bet_size:>8.2f}  -${bet_size:>8.2f}  {pct*100:>10.1f}%")

    # Actual worst trades from history
    losses = [t for t in results["trades"] if t["result"] != "win"]
    if losses:
        worst = min(losses, key=lambda t: t["pnl"])
        avg_loss = sum(t["pnl"] for t in losses) / len(losses)
        print(f"\nHistorical worst single loss: ${worst['pnl']:.2f}"
              f" ({worst['market_key']}, {worst['exit_type']})")
        print(f"Average loss across {len(losses)} losing trades: ${avg_loss:.2f}")

    # Max concurrent risk
    max_at_risk = sum(sorted(bet_pcts.values(), reverse=True)[:params["max_concurrent"]])
    print(f"\nMax concurrent exposure ({params['max_concurrent']} positions):"
          f" {max_at_risk:.0%} of bankroll = ${max_at_risk * final:.2f}")
    print(f"If ALL concurrent positions hit stop-loss simultaneously:")
    for sl_label, sl_val in [("85%", 0.85), ("80%", 0.80), ("no stop", 0)]:
        if sl_val > 0:
            # At stop: you recover shares*stop_loss, so loss = cost - shares*stop_loss
            # shares = cost/entry_prob, entry ~= 0.92 avg
            avg_entry = 0.92
            loss_frac = 1 - sl_val / avg_entry  # fraction of cost lost
            total_loss = max_at_risk * final * loss_frac
        else:
            total_loss = max_at_risk * final
        print(f"  SL={sl_label}: loss ~${total_loss:.2f} ({total_loss/final*100:.1f}% of bankroll)")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("Loading data...")
    resolutions = load_resolutions()
    print(f"  Resolutions: {len(resolutions)} up_down 15m/1h markets")

    snapshots = load_snapshots(DEFAULT_PARAMS["allowed_assets"],
                                DEFAULT_PARAMS["allowed_timeframes"])
    print(f"  Snapshots: {len(snapshots)} tradeable Up rows (BTC/ETH/XRP, 15m/1h)")

    # Show sample slugs for verification
    sample_slugs = set()
    for s in snapshots[:5000]:
        sample_slugs.add((s["slug"], s["asset"], s["timeframe"]))
    print(f"\n  Sample slugs (first few unique from data):")
    for slug, asset, tf in sorted(sample_slugs)[:15]:
        has_res = "RES:YES" if slug in resolutions else "RES:no"
        print(f"    {slug:<55} {asset:>4} {tf:>4}  {has_res}")

    # Verify resolution match rate
    all_trade_slugs = set(s["slug"] for s in snapshots)
    matched = sum(1 for s in all_trade_slugs if s in resolutions)
    print(f"\n  Unique slugs in snapshots: {len(all_trade_slugs)}")
    print(f"  Resolution match rate: {matched}/{len(all_trade_slugs)}"
          f" = {matched/len(all_trade_slugs)*100:.1f}%")

    # ── 1. Current strategy performance ──────────────────────
    print()
    results = run_backtest(snapshots, resolutions, DEFAULT_PARAMS, track_trades=True)
    print_detailed_results(results, DEFAULT_PARAMS)
    risk_assessment(results, DEFAULT_PARAMS)

    # ── 2. Sensitivity grid ──────────────────────────────────
    print("\n\nRunning sensitivity grid (48 combinations)...")
    run_sensitivity_grid(snapshots, resolutions, DEFAULT_PARAMS)

    # ── 3. Per-market optimal ────────────────────────────────
    print("\n\nRunning per-market optimization...")
    run_per_market_grid(snapshots, resolutions, DEFAULT_PARAMS)

    print()
    print("=" * 80)
    print("BACKTEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
