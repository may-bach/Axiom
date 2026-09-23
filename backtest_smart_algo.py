import json
from pathlib import Path

data = json.loads(Path('data/intraday_15d_cache.json').read_text())
config = json.loads(Path('data/config.json').read_text())
sector_matrix = json.loads(Path('data/sector_matrix.json').read_text())

period_prev = ['2026-09-09', '2026-09-10', '2026-09-11', '2026-09-15', '2026-09-16']
period_recent = ['2026-09-17', '2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23']


def compute_vwap(bars):
    """Computes cumulative VWAP for each bar."""
    cum_pv = 0.0
    cum_vol = 0.0
    vwaps = []
    for b in bars:
        vol = max(1, b.get('volume', 1))
        typical_p = (b['high'] + b['low'] + b['close']) / 3.0
        cum_pv += typical_p * vol
        cum_vol += vol
        vwaps.append(cum_pv / cum_vol)
    return vwaps


def simulate_smart_day(d, max_positions=2, budget=10000.0):
    day_data = {s: [b for b in bars if b['date'] == d] for s, bars in data.items()}
    day_times = sorted(list(set(b['time_str'] for s in day_data.values() for b in s)))

    # Compute VWAP for each stock
    vwap_map = {}
    for s, bars in day_data.items():
        if bars:
            vwap_map[s] = compute_vwap(bars)

    # 1. Base range (09:15 - 09:35 IST base)
    base_hl = {}
    for s, bars in day_data.items():
        base_bars = [b for b in bars if b['time_str'] <= '09:35']
        if base_bars:
            open_p = base_bars[0]['open']
            close_p = base_bars[-1]['close']
            base_hl[s] = {
                'open': open_p,
                'high': max(b['high'] for b in base_bars),
                'low': min(b['low'] for b in base_bars),
                'ret_base': (close_p - open_p) / open_p * 100,
            }

    # 2. Relative Strength (RS) Ranking at 09:35
    all_rets = [info['ret_base'] for info in base_hl.values()]
    all_rets.sort()
    if all_rets:
        median_ret = all_rets[len(all_rets) // 2]
        top_threshold = all_rets[int(len(all_rets) * 0.65)]
        bottom_threshold = all_rets[int(len(all_rets) * 0.35)]
    else:
        median_ret = 0.0
        top_threshold = 0.3
        bottom_threshold = -0.3

    trades = []
    open_pos = {}
    traded_symbols = set()

    for idx, t in enumerate(day_times):
        h, m = int(t.split(':')[0]), int(t.split(':')[1])
        is_mkt = (h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)
        can_enter = ((h == 9 and m >= 35) or (h > 9 and h < 14)) # 14:00 PM entry cutoff
        is_eod = (h == 15 and m >= 10)
        if not is_mkt: continue

        # --- A. Check Exits ---
        to_close = []
        for s, pos in list(open_pos.items()):
            bars = day_data[s]
            bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
            if bar_idx is None: continue
            bar = bars[bar_idx]
            ltp = bar['close']
            entry = pos['entry']
            qty = pos['qty']
            strat = config.get(s, {})
            target = strat.get('target', 0.02)
            sl = 0.010 # strict 1.0% max loss
            dur_min = (bar['timestamp'] - pos['time']) / 60.0

            exit_now = False
            reason = ''

            if is_eod:
                exit_now = True
                reason = 'EOD Square-off'
            elif pos['dir'] == 'LONG':
                if ltp > pos['peak']: pos['peak'] = ltp
                pnl_pct = (ltp - entry) / entry
                max_fav = (pos['peak'] - entry) / entry

                if pnl_pct >= target:
                    exit_now = True; reason = f'Target {target*100:.1f}%'
                elif pnl_pct <= -sl:
                    exit_now = True; reason = f'SL -{sl*100:.1f}%'
                # Breakeven guard: if peak reached +0.5%, stop out at breakeven (+0.05%)
                elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                    exit_now = True; reason = 'Breakeven Guard'
                # Trailing SL: if peak reached +0.8%, trail by 0.4% from peak
                elif max_fav >= 0.008 and ((pos['peak'] - ltp) / pos['peak']) >= 0.004:
                    exit_now = True; reason = 'Trailing SL'
                # Stagnation cut at 30m if dead flat
                elif dur_min >= 30 and abs(pnl_pct) <= 0.002:
                    exit_now = True; reason = 'Stagnation (30m)'

            elif pos['dir'] == 'SHORT':
                if ltp < pos['trough']: pos['trough'] = ltp
                pnl_pct = (entry - ltp) / entry
                max_fav = (entry - pos['trough']) / entry

                if pnl_pct >= target:
                    exit_now = True; reason = f'Target {target*100:.1f}%'
                elif pnl_pct <= -sl:
                    exit_now = True; reason = f'SL -{sl*100:.1f}%'
                elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                    exit_now = True; reason = 'Breakeven Guard'
                elif max_fav >= 0.008 and ((ltp - pos['trough']) / pos['trough']) >= 0.004:
                    exit_now = True; reason = 'Trailing SL'
                elif dur_min >= 30 and abs(pnl_pct) <= 0.002:
                    exit_now = True; reason = 'Stagnation (30m)'

            if exit_now:
                pnl = (ltp - entry) * qty if pos['dir'] == 'LONG' else (entry - ltp) * qty
                trades.append({
                    'sym': s, 'dir': pos['dir'], 'pnl': round(pnl, 2),
                    'entry': entry, 'exit': ltp, 'qty': qty,
                    'entry_t': pos['entry_t'], 'exit_t': t, 'reason': reason
                })
                to_close.append(s)

        for s in to_close: del open_pos[s]

        # --- B. Check Entries ---
        if can_enter and len(open_pos) < max_positions and not is_eod:
            for s, hl in base_hl.items():
                if len(open_pos) >= max_positions or s in open_pos or s in traded_symbols:
                    continue

                bars = day_data[s]
                bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                if bar_idx is None: continue
                bar = bars[bar_idx]
                ltp = bar['close']
                vwap = vwap_map[s][bar_idx]

                strat = config.get(s, {})
                buf_long = strat.get('breakout_long', 0.0025)
                buf_short = strat.get('breakout_short', 0.0025)
                can_short = strat.get('allow_short', True)
                eff_b = budget * strat.get('leverage', 1.0)

                # Filter 1: Relative Strength Check
                ret_base = hl['ret_base']
                is_rs_leader = (ret_base >= top_threshold)
                is_rs_laggard = (ret_base <= bottom_threshold)

                # Filter 2: VWAP Confirmation
                # Long: Price must be breaking out above base AND above VWAP AND RS Leader
                if is_rs_leader and ltp >= hl['high'] * (1 + buf_long) and ltp > vwap:
                    qty = int(eff_b / ltp)
                    if qty >= 1:
                        open_pos[s] = {
                            'sym': s, 'dir': 'LONG', 'entry': ltp, 'qty': qty,
                            'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                        }
                        traded_symbols.add(s)
                        continue

                # Short: Price must be breaking down below base AND below VWAP AND RS Laggard
                if can_short and is_rs_laggard and ltp <= hl['low'] * (1 - buf_short) and ltp < vwap:
                    qty = int(eff_b / ltp)
                    if qty >= 1:
                        open_pos[s] = {
                            'sym': s, 'dir': 'SHORT', 'entry': ltp, 'qty': qty,
                            'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                        }
                        traded_symbols.add(s)
                        continue

    total_pnl = sum(t['pnl'] for t in trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] < 0]
    return {
        'trades': trades,
        'pnl': total_pnl,
        'count': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': (len(wins) / len(trades) * 100) if trades else 0.0
    }


def run_evaluation():
    print("=" * 85)
    print("   SMART ALGORITHM MULTI-WEEK AUDIT: VWAP + RS BASE BREAKOUT + BE GUARD   ")
    print("=========================================================================")
    print(f"{'PERIOD / DATE':<20} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'STATUS'}")
    print("-" * 85)

    # 1. Prior 5 Days (Out-of-sample backtest: Sep 09 to Sep 16)
    print(">>> PERIOD 1: PRIOR 5 DAYS (Sep 09 - Sep 16)")
    prev_pnl = 0.0
    prev_trades = 0
    prev_wins = 0
    prev_losses = 0
    for d in period_prev:
        res = simulate_smart_day(d)
        prev_pnl += res['pnl']
        prev_trades += res['count']
        prev_wins += res['wins']
        prev_losses += res['losses']
        pnl_s = f"Rs.{res['pnl']:+8.2f}"
        stat = "PROFIT" if res['pnl'] > 0 else ("FLAT" if abs(res['pnl']) < 10 else "LOSS")
        print(f"  {d:<18} | {res['count']:<8} {res['wins']}W/{res['losses']}L   {res['win_rate']:>6.1f}%    | {pnl_s:<14} {stat}")

    wr_prev = (prev_wins / prev_trades * 100) if prev_trades else 0.0
    print("-" * 85)
    print(f"  {'SUBTOTAL (PRIOR 5D)':<18} | {prev_trades:<8} {prev_wins}W/{prev_losses}L   {wr_prev:>6.1f}%    | Rs.{prev_pnl:+8.2f}")
    print("=" * 85)

    # 2. Recent 5 Days (Sep 17 to Sep 23)
    print(">>> PERIOD 2: RECENT 5 DAYS (Sep 17 - Sep 23)")
    rec_pnl = 0.0
    rec_trades = 0
    rec_wins = 0
    rec_losses = 0
    recent_details = {}
    for d in period_recent:
        res = simulate_smart_day(d)
        rec_pnl += res['pnl']
        rec_trades += res['count']
        rec_wins += res['wins']
        rec_losses += res['losses']
        recent_details[d] = res['trades']
        pnl_s = f"Rs.{res['pnl']:+8.2f}"
        stat = "PROFIT" if res['pnl'] > 0 else ("FLAT" if abs(res['pnl']) < 10 else "LOSS")
        print(f"  {d:<18} | {res['count']:<8} {res['wins']}W/{res['losses']}L   {res['win_rate']:>6.1f}%    | {pnl_s:<14} {stat}")

    wr_rec = (rec_wins / rec_trades * 100) if rec_trades else 0.0
    print("-" * 85)
    print(f"  {'SUBTOTAL (RECENT 5D)':<18} | {rec_trades:<8} {rec_wins}W/{rec_losses}L   {wr_rec:>6.1f}%    | Rs.{rec_pnl:+8.2f}")
    print("=" * 85)

    # Combined 10-day summary
    tot_trades = prev_trades + rec_trades
    tot_wins = prev_wins + rec_wins
    tot_pnl = prev_pnl + rec_pnl
    wr_tot = (tot_wins / tot_trades * 100) if tot_trades else 0.0
    print(f"*** 10-DAY GRAND TOTAL: {tot_trades} Trades | {tot_wins}W/{tot_trades-tot_wins}L ({wr_tot:.1f}% Win Rate) | Net P&L: Rs.{tot_pnl:+,.2f} ***")
    print("=" * 85)

    print("\n--- SAMPLE TRADES FROM PRIOR 5 DAYS ---")
    for d in period_prev:
        res = simulate_smart_day(d)
        if res['trades']:
            print(f"\nDate: {d} ({len(res['trades'])} trades, PnL: Rs.{res['pnl']:+.2f})")
            for t in res['trades']:
                p_s = f"+Rs.{t['pnl']:.2f}" if t['pnl'] > 0 else f"-Rs.{abs(t['pnl']):.2f}"
                print(f"  [{t['entry_t']} -> {t['exit_t']}] {t['dir']:<5} {t['sym']:<10} @ Rs.{t['entry']:<8.1f} -> Rs.{t['exit']:<8.1f} | {p_s:<10} | {t['reason']}")

    print("\n--- SAMPLE TRADES FROM RECENT 5 DAYS ---")
    for d, trs in recent_details.items():
        if trs:
            print(f"\nDate: {d} ({len(trs)} trades, PnL: Rs.{sum(t['pnl'] for t in trs):+.2f})")
            for t in trs:
                p_s = f"+Rs.{t['pnl']:.2f}" if t['pnl'] > 0 else f"-Rs.{abs(t['pnl']):.2f}"
                print(f"  [{t['entry_t']} -> {t['exit_t']}] {t['dir']:<5} {t['sym']:<10} @ Rs.{t['entry']:<8.1f} -> Rs.{t['exit']:<8.1f} | {p_s:<10} | {t['reason']}")

if __name__ == '__main__':
    run_evaluation()
