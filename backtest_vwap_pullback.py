import json
from pathlib import Path
import numpy as np

data = json.loads(Path('data/intraday_15d_cache.json').read_text())
config = json.loads(Path('data/config.json').read_text())
sector_matrix = json.loads(Path('data/sector_matrix.json').read_text())

dates = sorted(list(set(b['date'] for bars in data.values() for b in bars)))

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

def test_vwap_pullback():
    print("Testing VWAP Pullback / Trend Continuation Strategy on 14 Days...")
    all_trades = []
    daily_results = {}

    for d in dates:
        day_data = {s: [b for b in bars if b['date'] == d] for s, bars in data.items()}
        day_times = sorted(list(set(b['time_str'] for s in day_data.values() for b in s)))
        if not day_times: continue

        vwap_map = {}
        for s, bars in day_data.items():
            if bars:
                vwap_map[s] = compute_vwap(bars)

        # 09:15 - 09:35 Base Range
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
                    'has_broken_out': False,
                    'has_broken_down': False
                }

        # RS Ranking
        all_rets = sorted([info['ret_base'] for info in base_hl.values()])
        top_threshold = all_rets[int(len(all_rets) * 0.65)]
        bottom_threshold = all_rets[int(len(all_rets) * 0.35)]

        trades = []
        open_pos = {}
        traded_symbols = set()

        for idx, t in enumerate(day_times):
            h, m = int(t.split(':')[0]), int(t.split(':')[1])
            is_mkt = (h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)
            can_enter = ((h == 9 and m >= 35) or (h > 9 and h < 14))
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
                entry = pos['entry']
                qty = pos['qty']
                dur_min = (bar['timestamp'] - pos['time']) / 60.0

                exit_now = False
                reason = ''

                if is_eod:
                    exit_now = True; reason = 'EOD'
                elif pos['dir'] == 'LONG':
                    if ltp > pos['peak']: pos['peak'] = ltp
                    pnl_pct = (ltp - entry) / entry
                    max_fav = (pos['peak'] - entry) / entry

                    # 1. Target 2.5%
                    if pnl_pct >= 0.025:
                        exit_now = True; reason = 'Target 2.5%'
                    # 2. Risk is tight: Stop Loss 0.6%
                    elif pnl_pct <= -0.006:
                        exit_now = True; reason = 'SL 0.6%'
                    # 3. Breakeven at +0.5%
                    elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'BE Guard'
                    # 4. Trailing at +0.8% with 0.4% trail
                    elif max_fav >= 0.008 and ((pos['peak'] - ltp) / pos['peak']) >= 0.004:
                        exit_now = True; reason = 'Trailing SL'
                    # 5. Stagnation exit at 25 min if flat
                    elif dur_min >= 25 and abs(pnl_pct) <= 0.0015:
                        exit_now = True; reason = 'Stagnation (25m)'

                elif pos['dir'] == 'SHORT':
                    if ltp < pos['trough']: pos['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos['trough']) / entry

                    if pnl_pct >= 0.025:
                        exit_now = True; reason = 'Target 2.5%'
                    elif pnl_pct <= -0.006:
                        exit_now = True; reason = 'SL 0.6%'
                    elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'BE Guard'
                    elif max_fav >= 0.008 and ((ltp - pos['trough']) / pos['trough']) >= 0.004:
                        exit_now = True; reason = 'Trailing SL'
                    elif dur_min >= 25 and abs(pnl_pct) <= 0.0015:
                        exit_now = True; reason = 'Stagnation (25m)'

                if exit_now:
                    pnl = (ltp - entry) * qty if pos['dir'] == 'LONG' else (entry - ltp) * qty
                    trades.append({
                        'sym': s, 'dir': pos['dir'], 'pnl': round(pnl, 2),
                        'entry': entry, 'exit': ltp, 'qty': qty,
                        'entry_t': pos['entry_t'], 'exit_t': t, 'reason': reason
                    })
                    to_close.append(s)

            for s in to_close: del open_pos[s]

            # --- Check Entries ---
            # Track if stock has broken out of morning base
            for s, hl in base_hl.items():
                bars = day_data[s]
                bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                if bar_idx is None: continue
                ltp = bars[bar_idx]['close']
                if ltp > hl['high'] * 1.002:
                    hl['has_broken_out'] = True
                if ltp < hl['low'] * 0.998:
                    hl['has_broken_down'] = True

            if can_enter and len(open_pos) < 2 and not is_eod:
                for s, hl in base_hl.items():
                    if len(open_pos) >= 2 or s in open_pos or s in traded_symbols: continue

                    bars = day_data[s]
                    bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                    if bar_idx is None or bar_idx < 2: continue
                    bar = bars[bar_idx]
                    prev_bar = bars[bar_idx - 1]
                    ltp = bar['close']
                    vwap = vwap_map[s][bar_idx]
                    
                    strat = config.get(s, {})
                    can_short = strat.get('allow_short', True)
                    eff_b = 10000.0 * strat.get('leverage', 1.0)

                    is_rs_leader = (hl['ret_base'] >= top_threshold)
                    is_rs_laggard = (hl['ret_base'] <= bottom_threshold)

                    # PULLBACK LONG:
                    # Stock has broken out earlier, then pulls back to touch near VWAP (within 0.3% above VWAP),
                    # and now prints a GREEN reversal candle (close > open) staying above VWAP!
                    if is_rs_leader and hl['has_broken_out']:
                        dist_to_vwap = (ltp - vwap) / vwap
                        is_near_vwap = (0.0 <= dist_to_vwap <= 0.0035)
                        is_green_rebound = (bar['close'] > bar['open'] and bar['close'] > prev_bar['high'])
                        if is_near_vwap and is_green_rebound:
                            qty = int(eff_b / ltp)
                            if qty >= 1:
                                open_pos[s] = {
                                    'sym': s, 'dir': 'LONG', 'entry': ltp, 'qty': qty,
                                    'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                                }
                                traded_symbols.add(s)
                                continue

                    # PULLBACK SHORT:
                    if can_short and is_rs_laggard and hl['has_broken_down']:
                        dist_to_vwap = (vwap - ltp) / vwap
                        is_near_vwap = (0.0 <= dist_to_vwap <= 0.0035)
                        is_red_continuation = (bar['close'] < bar['open'] and bar['close'] < prev_bar['low'])
                        if is_near_vwap and is_red_continuation:
                            qty = int(eff_b / ltp)
                            if qty >= 1:
                                open_pos[s] = {
                                    'sym': s, 'dir': 'SHORT', 'entry': ltp, 'qty': qty,
                                    'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                                }
                                traded_symbols.add(s)
                                continue

        day_pnl = sum(t['pnl'] for t in trades)
        daily_results[d] = {'trades': trades, 'pnl': day_pnl}
        all_trades.extend(trades)

    tot_pnl = sum(t['pnl'] for t in all_trades)
    wins = [t for t in all_trades if t['pnl'] > 0]
    losses = [t for t in all_trades if t['pnl'] < 0]
    wr = (len(wins) / len(all_trades) * 100) if all_trades else 0.0
    print(f"RESULTS: {len(all_trades)} Trades | {len(wins)}W/{len(losses)}L ({wr:.1f}% WR) | Net P&L: Rs.{tot_pnl:+,.2f}")
    return daily_results, all_trades

if __name__ == '__main__':
    test_vwap_pullback()
