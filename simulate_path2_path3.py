import json
from pathlib import Path
import numpy as np

data = json.loads(Path('data/intraday_23d_cache.json').read_text())
vix_history = json.loads(Path('data/vix_history.json').read_text())

dates = sorted(list(set(b['date'] for bars in data.values() for b in bars)))
# 20 trading days: 10 prior, 10 recent
recent_10 = dates[-10:]
prior_10 = dates[-20:-10]

def compute_vwap(bars):
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

def run_adaptive_simulation(test_dates, max_daily_trades=2, budget=10000.0):
    all_trades = []
    daily_results = {}

    for d in test_dates:
        day_data = {s: [b for b in bars if b['date'] == d] for s, bars in data.items()}
        day_times = sorted(list(set(b['time_str'] for s in day_data.values() for b in s)))
        if not day_times: continue

        # 1. India VIX for the day
        # Default to 11.5 if missing
        vix = vix_history.get(d, 11.5)
        is_low_vix = (vix < 13.0)
        mode = "MEAN_REVERSION_FADE" if is_low_vix else "MOMENTUM_BREAKOUT"

        # 2. VWAP Map & Base 09:15-09:30 metrics across all 50 stocks
        vwap_map = {}
        base_stats = {}
        for s, bars in day_data.items():
            if bars:
                vwap_map[s] = compute_vwap(bars)
                base_bars = [b for b in bars if b['time_str'] <= '09:30']
                if base_bars:
                    open_p = base_bars[0]['open']
                    close_p = base_bars[-1]['close']
                    high_p = max(b['high'] for b in base_bars)
                    low_p = min(b['low'] for b in base_bars)
                    vols = [b.get('volume', 1) for b in base_bars]
                    base_stats[s] = {
                        'open': open_p,
                        'close': close_p,
                        'high': high_p,
                        'low': low_p,
                        'ret': (close_p - open_p) / open_p * 100,
                        'vol': sum(vols)
                    }

        if len(base_stats) < 10: continue

        # 3. Path 2: Dynamic Pre-Market Scanner
        # Rank by morning momentum across all 50 stocks
        sorted_by_ret = sorted(base_stats.items(), key=lambda x: x[1]['ret'], reverse=True)
        top_long_candidates = [s for s, info in sorted_by_ret[:5] if info['ret'] > 0.2]
        top_short_candidates = [s for s, info in sorted_by_ret[-5:] if info['ret'] < -0.2]

        trades = []
        open_pos = {}
        traded_symbols = set()

        for idx, t in enumerate(day_times):
            h, m = int(t.split(':')[0]), int(t.split(':')[1])
            is_mkt = (h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)
            can_enter = ((h == 9 and m >= 30) or (h > 9 and h < 13 and m <= 30))
            is_eod = (h == 15 and m >= 10)
            if not is_mkt: continue

            # --- Check Exits ---
            to_close = []
            for s, pos in list(open_pos.items()):
                bars = day_data[s]
                bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                if bar_idx is None: continue
                bar = bars[bar_idx]
                ltp = bar['close']
                vwap = vwap_map[s][bar_idx]
                entry = pos['entry']
                qty = pos['qty']
                dur_min = (bar['timestamp'] - pos['time']) / 60.0

                exit_now = False
                reason = ''

                if is_eod:
                    exit_now = True; reason = 'EOD Square-off'
                elif pos['dir'] == 'LONG':
                    if ltp > pos['peak']: pos['peak'] = ltp
                    pnl_pct = (ltp - entry) / entry
                    max_fav = (pos['peak'] - entry) / entry

                    # Exit logic based on mode
                    if pos['mode'] == 'FADE':
                        # Mean-reversion target is VWAP (+0.5% to +0.8%)
                        if ltp >= vwap or pnl_pct >= 0.008:
                            exit_now = True; reason = 'Target (VWAP Reached)'
                        elif pnl_pct <= -0.005:
                            exit_now = True; reason = 'SL -0.5%'
                        elif dur_min >= 40 and abs(pnl_pct) <= 0.001:
                            exit_now = True; reason = 'Stagnation (40m)'
                    else: # BREAKOUT
                        if pnl_pct >= 0.030:
                            exit_now = True; reason = 'Target 3.0%'
                        elif pnl_pct <= -0.010:
                            exit_now = True; reason = 'SL -1.0%'
                        elif max_fav >= 0.007 and pnl_pct <= 0.001:
                            exit_now = True; reason = 'BE Guard'
                        elif max_fav >= 0.012 and ((pos['peak'] - ltp) / pos['peak']) >= 0.005:
                            exit_now = True; reason = 'Trailing SL'
                        elif dur_min >= 45 and abs(pnl_pct) <= 0.0015:
                            exit_now = True; reason = 'Stagnation (45m)'

                elif pos['dir'] == 'SHORT':
                    if ltp < pos['trough']: pos['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos['trough']) / entry

                    if pos['mode'] == 'FADE':
                        if ltp <= vwap or pnl_pct >= 0.008:
                            exit_now = True; reason = 'Target (VWAP Reached)'
                        elif pnl_pct <= -0.005:
                            exit_now = True; reason = 'SL -0.5%'
                        elif dur_min >= 40 and abs(pnl_pct) <= 0.001:
                            exit_now = True; reason = 'Stagnation (40m)'
                    else: # BREAKOUT
                        if pnl_pct >= 0.030:
                            exit_now = True; reason = 'Target 3.0%'
                        elif pnl_pct <= -0.010:
                            exit_now = True; reason = 'SL -1.0%'
                        elif max_fav >= 0.007 and pnl_pct <= 0.001:
                            exit_now = True; reason = 'BE Guard'
                        elif max_fav >= 0.012 and ((ltp - pos['trough']) / pos['trough']) >= 0.005:
                            exit_now = True; reason = 'Trailing SL'
                        elif dur_min >= 45 and abs(pnl_pct) <= 0.0015:
                            exit_now = True; reason = 'Stagnation (45m)'

                if exit_now:
                    pnl = (ltp - entry) * qty if pos['dir'] == 'LONG' else (entry - ltp) * qty
                    trades.append({
                        'date': d, 'sym': s, 'dir': pos['dir'], 'mode': pos['mode'],
                        'entry': entry, 'exit': ltp, 'qty': qty, 'pnl': round(pnl, 2),
                        'entry_t': pos['entry_t'], 'exit_t': t, 'reason': reason
                    })
                    to_close.append(s)

            for s in to_close: del open_pos[s]

            # --- Check Entries ---
            if can_enter and len(open_pos) < 2 and len(trades) + len(open_pos) < max_daily_trades and not is_eod:
                # PATH 3: REGIME ADAPTIVE EVALUATION
                if is_low_vix:
                    # MODE: MEAN-REVERSION / BOUNDARY FADE
                    # 1. Fade the High (SHORT): Price tested near base high, now prints red rejection bar below high, above VWAP
                    for s in top_long_candidates:
                        if s in open_pos or s in traded_symbols: continue
                        bars = day_data[s]
                        bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                        if bar_idx is None or bar_idx < 1: continue
                        bar = bars[bar_idx]
                        ltp = bar['close']
                        vwap = vwap_map[s][bar_idx]
                        hl = base_stats[s]

                        # Condition: Price reached within 0.2% of high, now closes red (close < open), still above VWAP
                        near_high = (bar['high'] >= hl['high'] * 0.998)
                        is_rejection_red = (bar['close'] < bar['open'] and bar['close'] < hl['high'])
                        room_to_vwap = ((ltp - vwap) / vwap >= 0.004) # at least 0.4% room to VWAP

                        if near_high and is_rejection_red and room_to_vwap:
                            qty = int(budget * 1.5 / ltp)
                            if qty >= 1:
                                open_pos[s] = {
                                    'sym': s, 'dir': 'SHORT', 'mode': 'FADE', 'entry': ltp, 'qty': qty,
                                    'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                                }
                                traded_symbols.add(s)
                                break

                    if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_daily_trades:
                        continue

                    # 2. Fade the Low (BUY): Price tested near base low, now prints green support bar above low, below VWAP
                    for s in top_short_candidates:
                        if s in open_pos or s in traded_symbols: continue
                        bars = day_data[s]
                        bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                        if bar_idx is None or bar_idx < 1: continue
                        bar = bars[bar_idx]
                        ltp = bar['close']
                        vwap = vwap_map[s][bar_idx]
                        hl = base_stats[s]

                        near_low = (bar['low'] <= hl['low'] * 1.002)
                        is_support_green = (bar['close'] > bar['open'] and bar['close'] > hl['low'])
                        room_to_vwap = ((vwap - ltp) / vwap >= 0.004)

                        if near_low and is_support_green and room_to_vwap:
                            qty = int(budget * 1.5 / ltp)
                            if qty >= 1:
                                open_pos[s] = {
                                    'sym': s, 'dir': 'LONG', 'mode': 'FADE', 'entry': ltp, 'qty': qty,
                                    'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                                }
                                traded_symbols.add(s)
                                break

                else:
                    # MODE: MOMENTUM BREAKOUT (VIX >= 13.0)
                    for s in top_long_candidates:
                        if s in open_pos or s in traded_symbols: continue
                        bars = day_data[s]
                        bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                        if bar_idx is None: continue
                        bar = bars[bar_idx]
                        ltp = bar['close']
                        vwap = vwap_map[s][bar_idx]
                        hl = base_stats[s]

                        if ltp >= hl['high'] * 1.0025 and ltp > vwap:
                            qty = int(budget * 1.5 / ltp)
                            if qty >= 1:
                                open_pos[s] = {
                                    'sym': s, 'dir': 'LONG', 'mode': 'BREAKOUT', 'entry': ltp, 'qty': qty,
                                    'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                                }
                                traded_symbols.add(s)
                                break

                    if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_daily_trades:
                        continue

                    for s in top_short_candidates:
                        if s in open_pos or s in traded_symbols: continue
                        bars = day_data[s]
                        bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                        if bar_idx is None: continue
                        bar = bars[bar_idx]
                        ltp = bar['close']
                        vwap = vwap_map[s][bar_idx]
                        hl = base_stats[s]

                        if ltp <= hl['low'] * 0.9975 and ltp < vwap:
                            qty = int(budget * 1.5 / ltp)
                            if qty >= 1:
                                open_pos[s] = {
                                    'sym': s, 'dir': 'SHORT', 'mode': 'BREAKOUT', 'entry': ltp, 'qty': qty,
                                    'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                                }
                                traded_symbols.add(s)
                                break

        day_pnl = sum(t['pnl'] for t in trades)
        daily_results[d] = {'trades': trades, 'pnl': day_pnl, 'vix': vix, 'mode': mode}
        all_trades.extend(trades)

    return daily_results, all_trades

def run_evaluation():
    print("=" * 95)
    print("   PATH 2 + PATH 3 INTEGRATED AUDIT: DYNAMIC SCANNER + REGIME-ADAPTIVE ENGINE   ")
    print("=======================================================================================")

    # Period 1: Prior 10 Trading Days (Aug 26 - Sep 08)
    print(">>> PERIOD 1: PRIOR 10 TRADING DAYS (Aug 26 - Sep 08)")
    print(f"{'DATE':<12} | {'VIX':<6} {'MODE':<18} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'SAMPLE TRADES'}")
    print("-" * 95)
    p1_res, p1_tr = run_adaptive_simulation(prior_10)
    p1_pnl = 0.0
    for d in prior_10:
        res = p1_res.get(d, {'trades': [], 'pnl': 0.0, 'vix': 11.5, 'mode': 'MEAN_REVERSION_FADE'})
        trs = res['trades']
        wins = [t for t in trs if t['pnl'] > 0]
        losses = [t for t in trs if t['pnl'] < 0]
        wr = (len(wins) / len(trs) * 100) if trs else 0.0
        p1_pnl += res['pnl']
        sample_str = ", ".join([f"{t['sym']}({t['pnl']:+.1f})" for t in trs[:2]])
        print(f"{d:<12} | {res['vix']:<6.2f} {res['mode']:<18} | {len(trs):<8} {len(wins)}W/{len(losses)}L   {wr:>6.1f}%    | Rs.{res['pnl']:+8.2f}    {sample_str}")

    p1_w = len([t for t in p1_tr if t['pnl'] > 0])
    p1_l = len([t for t in p1_tr if t['pnl'] < 0])
    p1_wr = (p1_w / len(p1_tr) * 100) if p1_tr else 0.0
    print("-" * 95)
    print(f"SUBTOTAL PRIOR 10D: {len(p1_tr)} Trades | {p1_w}W/{p1_l}L ({p1_wr:.1f}% WR) | Gross PnL: Rs.{p1_pnl:+,.2f} | Fees (@18): -Rs.{len(p1_tr)*18:.2f} | Net: Rs.{p1_pnl - len(p1_tr)*18:+,.2f}")
    print("=" * 95)

    # Period 2: Recent 10 Trading Days (Sep 09 - Sep 23)
    print("\n>>> PERIOD 2: RECENT 10 TRADING DAYS (Sep 09 - Sep 23)")
    print(f"{'DATE':<12} | {'VIX':<6} {'MODE':<18} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'SAMPLE TRADES'}")
    print("-" * 95)
    p2_res, p2_tr = run_adaptive_simulation(recent_10)
    p2_pnl = 0.0
    for d in recent_10:
        res = p2_res.get(d, {'trades': [], 'pnl': 0.0, 'vix': 11.5, 'mode': 'MEAN_REVERSION_FADE'})
        trs = res['trades']
        wins = [t for t in trs if t['pnl'] > 0]
        losses = [t for t in trs if t['pnl'] < 0]
        wr = (len(wins) / len(trs) * 100) if trs else 0.0
        p2_pnl += res['pnl']
        sample_str = ", ".join([f"{t['sym']}({t['pnl']:+.1f})" for t in trs[:2]])
        print(f"{d:<12} | {res['vix']:<6.2f} {res['mode']:<18} | {len(trs):<8} {len(wins)}W/{len(losses)}L   {wr:>6.1f}%    | Rs.{res['pnl']:+8.2f}    {sample_str}")

    p2_w = len([t for t in p2_tr if t['pnl'] > 0])
    p2_l = len([t for t in p2_tr if t['pnl'] < 0])
    p2_wr = (p2_w / len(p2_tr) * 100) if p2_tr else 0.0
    print("-" * 95)
    print(f"SUBTOTAL RECENT 10D: {len(p2_tr)} Trades | {p2_w}W/{p2_l}L ({p2_wr:.1f}% WR) | Gross PnL: Rs.{p2_pnl:+,.2f} | Fees (@18): -Rs.{len(p2_tr)*18:.2f} | Net: Rs.{p2_pnl - len(p2_tr)*18:+,.2f}")
    print("=" * 95)

    tot_tr = p1_tr + p2_tr
    tot_pnl = p1_pnl + p2_pnl
    tot_w = p1_w + p2_w
    tot_l = p1_l + p2_l
    tot_wr = (tot_w / len(tot_tr) * 100) if tot_tr else 0.0
    fees = len(tot_tr) * 18.0
    net_after_fees = tot_pnl - fees
    print(f"*** 20-DAY GRAND TOTAL: {len(tot_tr)} Trades (Avg {len(tot_tr)/20:.1f} trades/day) | {tot_w}W/{tot_l}L ({tot_wr:.1f}% Win Rate) ***")
    print(f"*** Gross P&L: Rs.{tot_pnl:+,.2f} | Exchange Fees: -Rs.{fees:.2f} | PURE NET PROFIT: Rs.{net_after_fees:+,.2f} ({(net_after_fees/10000.0)*100:+.2f}%) ***")
    print("=" * 95)

if __name__ == '__main__':
    run_evaluation()
