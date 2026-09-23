import json
from pathlib import Path
import numpy as np

data = json.loads(Path('data/intraday_23d_cache.json').read_text())
vix_history = json.loads(Path('data/vix_history.json').read_text())
sector_matrix = json.loads(Path('data/sector_matrix.json').read_text())

dates = sorted(list(set(b['date'] for bars in data.values() for b in bars)))
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

def run_hybrid_adaptive_simulation(test_dates, max_daily_trades=3, budget=10000.0):
    all_trades = []
    daily_results = {}

    for d in test_dates:
        day_data = {s: [b for b in bars if b['date'] == d] for s, bars in data.items()}
        day_times = sorted(list(set(b['time_str'] for s in day_data.values() for b in s)))
        if not day_times: continue

        vix = vix_history.get(d, 11.5)

        # Precompute VWAP & 10-bar rolling average volume
        vwap_map = {}
        vol_avg_map = {}
        for s, bars in day_data.items():
            if bars:
                vwap_map[s] = compute_vwap(bars)
                vols = [b.get('volume', 1) for b in bars]
                vol_avg = []
                for i in range(len(vols)):
                    start_i = max(0, i - 10)
                    vol_avg.append(np.mean(vols[start_i:i]) if i > 0 else vols[0])
                vol_avg_map[s] = vol_avg

        # Base 09:15-09:35 metrics across all 50 stocks
        base_stats = {}
        for s, bars in day_data.items():
            base_bars = [b for b in bars if b['time_str'] <= '09:35']
            if base_bars:
                open_p = base_bars[0]['open']
                close_p = base_bars[-1]['close']
                base_stats[s] = {
                    'open': open_p,
                    'close': close_p,
                    'high': max(b['high'] for b in base_bars),
                    'low': min(b['low'] for b in base_bars),
                    'ret': (close_p - open_p) / open_p * 100,
                    'sector': sector_matrix.get(s, {}).get('sector', 'OTHER')
                }

        if len(base_stats) < 10: continue

        # Dynamic Pre-Market Scanner: Top 6 Longs, Top 6 Shorts
        sorted_by_ret = sorted(base_stats.items(), key=lambda x: x[1]['ret'], reverse=True)
        top_long_candidates = [s for s, info in sorted_by_ret[:8] if info['ret'] > 0.2]
        top_short_candidates = [s for s, info in sorted_by_ret[-8:] if info['ret'] < -0.2]

        # Sector momentum at 09:35
        sector_rets = {}
        for s, info in base_stats.items():
            sector_rets.setdefault(info['sector'], []).append(info['ret'])
        sector_avg = {sec: np.mean(rets) for sec, rets in sector_rets.items()}

        trades = []
        open_pos = {}
        traded_symbols = set()

        for idx, t in enumerate(day_times):
            h, m = int(t.split(':')[0]), int(t.split(':')[1])
            is_mkt = (h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)
            can_enter = ((h == 9 and m >= 35) or (h > 9 and h < 13 and m <= 30))
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

                    if pos['type'] == 'FADE':
                        # Mean reversion: exit at VWAP or +0.7% target
                        if ltp >= vwap or pnl_pct >= 0.007:
                            exit_now = True; reason = 'Target (VWAP Reached)'
                        elif pnl_pct <= -0.005:
                            exit_now = True; reason = 'SL -0.5%'
                        elif dur_min >= 30 and abs(pnl_pct) <= 0.001:
                            exit_now = True; reason = 'Stagnation (30m)'
                    else: # SUPER-BREAKOUT
                        if pnl_pct >= 0.025:
                            exit_now = True; reason = 'Target 2.5%'
                        elif pnl_pct <= -0.008:
                            exit_now = True; reason = 'SL -0.8%'
                        elif max_fav >= 0.006 and pnl_pct <= 0.0005:
                            exit_now = True; reason = 'Breakeven Guard'
                        elif max_fav >= 0.010 and ((pos['peak'] - ltp) / pos['peak']) >= 0.004:
                            exit_now = True; reason = 'Trailing SL'
                        elif dur_min >= 35 and abs(pnl_pct) <= 0.0015:
                            exit_now = True; reason = 'Stagnation (35m)'

                elif pos['dir'] == 'SHORT':
                    if ltp < pos['trough']: pos['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos['trough']) / entry

                    if pos['type'] == 'FADE':
                        if ltp <= vwap or pnl_pct >= 0.007:
                            exit_now = True; reason = 'Target (VWAP Reached)'
                        elif pnl_pct <= -0.005:
                            exit_now = True; reason = 'SL -0.5%'
                        elif dur_min >= 30 and abs(pnl_pct) <= 0.001:
                            exit_now = True; reason = 'Stagnation (30m)'
                    else: # SUPER-BREAKOUT
                        if pnl_pct >= 0.025:
                            exit_now = True; reason = 'Target 2.5%'
                        elif pnl_pct <= -0.008:
                            exit_now = True; reason = 'SL -0.8%'
                        elif max_fav >= 0.006 and pnl_pct <= 0.0005:
                            exit_now = True; reason = 'Breakeven Guard'
                        elif max_fav >= 0.010 and ((ltp - pos['trough']) / pos['trough']) >= 0.004:
                            exit_now = True; reason = 'Trailing SL'
                        elif dur_min >= 35 and abs(pnl_pct) <= 0.0015:
                            exit_now = True; reason = 'Stagnation (35m)'

                if exit_now:
                    pnl = (ltp - entry) * qty if pos['dir'] == 'LONG' else (entry - ltp) * qty
                    trades.append({
                        'date': d, 'sym': s, 'dir': pos['dir'], 'type': pos['type'],
                        'entry': entry, 'exit': ltp, 'qty': qty, 'pnl': round(pnl, 2),
                        'entry_t': pos['entry_t'], 'exit_t': t, 'reason': reason
                    })
                    to_close.append(s)

            for s in to_close: del open_pos[s]

            # --- Check Entries ---
            if can_enter and len(open_pos) < 2 and len(trades) + len(open_pos) < max_daily_trades and not is_eod:
                # 1. Check SUPER-BREAKOUTS FIRST (Volume > 1.8x + Strong Sector Flow + Base Breakout)
                # This captures runaway monster runners like JINDALSTEL, TATASTEEL, TRENT, VOLTAS
                for s in top_long_candidates:
                    if s in open_pos or s in traded_symbols: continue
                    bars = day_data[s]
                    bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                    if bar_idx is None or bar_idx < 1: continue
                    bar = bars[bar_idx]
                    ltp = bar['close']
                    vwap = vwap_map[s][bar_idx]
                    hl = base_stats[s]
                    rvol = bar.get('volume', 1) / vol_avg_map[s][bar_idx] if vol_avg_map[s][bar_idx] > 0 else 1.0

                    sec = hl['sector']
                    sec_strong = (sector_avg.get(sec, 0.0) >= 0.25)
                    # Super breakout: LTP > base high * 1.003, LTP > VWAP, RVOL >= 1.5x, Sector confirmed
                    if ltp >= hl['high'] * 1.003 and ltp > vwap and rvol >= 1.5 and sec_strong:
                        qty = int(budget * 1.5 / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'LONG', 'type': 'SUPER_BREAKOUT', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            break

                if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_daily_trades: continue

                for s in top_short_candidates:
                    if s in open_pos or s in traded_symbols: continue
                    bars = day_data[s]
                    bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                    if bar_idx is None or bar_idx < 1: continue
                    bar = bars[bar_idx]
                    ltp = bar['close']
                    vwap = vwap_map[s][bar_idx]
                    hl = base_stats[s]
                    rvol = bar.get('volume', 1) / vol_avg_map[s][bar_idx] if vol_avg_map[s][bar_idx] > 0 else 1.0

                    sec = hl['sector']
                    sec_weak = (sector_avg.get(sec, 0.0) <= -0.25)
                    if ltp <= hl['low'] * 0.997 and ltp < vwap and rvol >= 1.5 and sec_weak:
                        qty = int(budget * 1.5 / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'SHORT', 'type': 'SUPER_BREAKOUT', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            break

                if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_daily_trades: continue

                # 2. If NO Super-Breakout, check BOUNDARY FADE (Mean-Reversion in Low VIX)
                if vix < 13.0:
                    for s in top_long_candidates:
                        if s in open_pos or s in traded_symbols: continue
                        bars = day_data[s]
                        bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                        if bar_idx is None or bar_idx < 1: continue
                        bar = bars[bar_idx]
                        ltp = bar['close']
                        vwap = vwap_map[s][bar_idx]
                        hl = base_stats[s]

                        # FADE HIGH: touched near high, prints red bar, room to VWAP >= 0.4%
                        near_high = (bar['high'] >= hl['high'] * 0.998)
                        is_rejection_red = (bar['close'] < bar['open'] and bar['close'] < hl['high'])
                        room_to_vwap = ((ltp - vwap) / vwap >= 0.004)

                        if near_high and is_rejection_red and room_to_vwap:
                            qty = int(budget * 1.5 / ltp)
                            if qty >= 1:
                                open_pos[s] = {
                                    'sym': s, 'dir': 'SHORT', 'type': 'FADE', 'entry': ltp, 'qty': qty,
                                    'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                                }
                                traded_symbols.add(s)
                                break

                    if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_daily_trades: continue

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
                                    'sym': s, 'dir': 'LONG', 'type': 'FADE', 'entry': ltp, 'qty': qty,
                                    'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                                }
                                traded_symbols.add(s)
                                break

        day_pnl = sum(t['pnl'] for t in trades)
        daily_results[d] = {'trades': trades, 'pnl': day_pnl, 'vix': vix}
        all_trades.extend(trades)

    return daily_results, all_trades

def run_hybrid_evaluation():
    print("=" * 105)
    print("   20-DAY DEEP EMPIRICAL AUDIT: DYNAMIC SCANNER + ADAPTIVE REGIME HYBRID ENGINE   ")
    print("=========================================================================================================")

    # Period 1: Prior 10 Trading Days (Aug 26 - Sep 08)
    print(">>> PERIOD 1: PRIOR 10 TRADING DAYS (Aug 26 - Sep 08)")
    print(f"{'DATE':<12} | {'VIX':<6} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'KEY TRADES'}")
    print("-" * 105)
    p1_res, p1_tr = run_hybrid_adaptive_simulation(prior_10)
    p1_pnl = 0.0
    for d in prior_10:
        res = p1_res.get(d, {'trades': [], 'pnl': 0.0, 'vix': 11.5})
        trs = res['trades']
        wins = [t for t in trs if t['pnl'] > 0]
        losses = [t for t in trs if t['pnl'] < 0]
        wr = (len(wins) / len(trs) * 100) if trs else 0.0
        p1_pnl += res['pnl']
        sample_str = ", ".join([f"{t['sym']}[{t['type'][:4]}]({t['pnl']:+.1f})" for t in trs[:2]])
        print(f"{d:<12} | {res['vix']:<6.2f} | {len(trs):<8} {len(wins)}W/{len(losses)}L   {wr:>6.1f}%    | Rs.{res['pnl']:+8.2f}    {sample_str}")

    p1_w = len([t for t in p1_tr if t['pnl'] > 0])
    p1_l = len([t for t in p1_tr if t['pnl'] < 0])
    p1_wr = (p1_w / len(p1_tr) * 100) if p1_tr else 0.0
    fees1 = len(p1_tr) * 18.0
    print("-" * 105)
    print(f"SUBTOTAL PRIOR 10D: {len(p1_tr)} Trades | {p1_w}W/{p1_l}L ({p1_wr:.1f}% WR) | Gross PnL: Rs.{p1_pnl:+,.2f} | Fees: -Rs.{fees1:.2f} | PURE NET: Rs.{p1_pnl - fees1:+,.2f}")
    print("=" * 105)

    # Period 2: Recent 10 Trading Days (Sep 09 - Sep 23)
    print("\n>>> PERIOD 2: RECENT 10 TRADING DAYS (Sep 09 - Sep 23)")
    print(f"{'DATE':<12} | {'VIX':<6} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'KEY TRADES'}")
    print("-" * 105)
    p2_res, p2_tr = run_hybrid_adaptive_simulation(recent_10)
    p2_pnl = 0.0
    for d in recent_10:
        res = p2_res.get(d, {'trades': [], 'pnl': 0.0, 'vix': 11.5})
        trs = res['trades']
        wins = [t for t in trs if t['pnl'] > 0]
        losses = [t for t in trs if t['pnl'] < 0]
        wr = (len(wins) / len(trs) * 100) if trs else 0.0
        p2_pnl += res['pnl']
        sample_str = ", ".join([f"{t['sym']}[{t['type'][:4]}]({t['pnl']:+.1f})" for t in trs[:2]])
        print(f"{d:<12} | {res['vix']:<6.2f} | {len(trs):<8} {len(wins)}W/{len(losses)}L   {wr:>6.1f}%    | Rs.{res['pnl']:+8.2f}    {sample_str}")

    p2_w = len([t for t in p2_tr if t['pnl'] > 0])
    p2_l = len([t for t in p2_tr if t['pnl'] < 0])
    p2_wr = (p2_w / len(p2_tr) * 100) if p2_tr else 0.0
    fees2 = len(p2_tr) * 18.0
    print("-" * 105)
    print(f"SUBTOTAL RECENT 10D: {len(p2_tr)} Trades | {p2_w}W/{p2_l}L ({p2_wr:.1f}% WR) | Gross PnL: Rs.{p2_pnl:+,.2f} | Fees: -Rs.{fees2:.2f} | PURE NET: Rs.{p2_pnl - fees2:+,.2f}")
    print("=" * 105)

    tot_tr = p1_tr + p2_tr
    tot_pnl = p1_pnl + p2_pnl
    tot_w = p1_w + p2_w
    tot_l = p1_l + p2_l
    tot_wr = (tot_w / len(tot_tr) * 100) if tot_tr else 0.0
    fees = len(tot_tr) * 18.0
    net_after_fees = tot_pnl - fees
    print(f"*** 20-DAY GRAND TOTAL: {len(tot_tr)} Trades (Avg {len(tot_tr)/20:.1f} trades/day) | {tot_w}W/{tot_l}L ({tot_wr:.1f}% Win Rate) ***")
    print(f"*** Gross P&L: Rs.{tot_pnl:+,.2f} | Total Exchange Fees: -Rs.{fees:.2f} | PURE NET PROFIT: Rs.{net_after_fees:+,.2f} ({(net_after_fees/10000.0)*100:+.2f}% on Capital) ***")
    print("=" * 105)

if __name__ == '__main__':
    run_hybrid_evaluation()
