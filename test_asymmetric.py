import json
from pathlib import Path
import numpy as np

data = json.loads(Path('data/intraday_23d_cache.json').read_text())
vix_history = json.loads(Path('data/vix_history.json').read_text())

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

def test_asymmetric_edge(target_pct=0.014, sl_pct=0.0045, be_trigger=0.005, max_trades=2):
    all_trades = []
    daily_results = {}

    for d in dates[-20:]:
        day_data = {s: [b for b in bars if b['date'] == d] for s, bars in data.items()}
        day_times = sorted(list(set(b['time_str'] for s in day_data.values() for b in s)))
        if not day_times: continue

        vwap_map = {s: compute_vwap(bars) for s, bars in day_data.items() if bars}

        base_stats = {}
        for s, bars in day_data.items():
            base_bars = [b for b in bars if b['time_str'] <= '09:30']
            if base_bars:
                open_p = base_bars[0]['open']
                close_p = base_bars[-1]['close']
                base_stats[s] = {
                    'open': open_p,
                    'close': close_p,
                    'high': max(b['high'] for b in base_bars),
                    'low': min(b['low'] for b in base_bars),
                    'ret': (close_p - open_p) / open_p * 100,
                }

        sorted_by_ret = sorted(base_stats.items(), key=lambda x: x[1]['ret'], reverse=True)
        top_longs = [s for s, info in sorted_by_ret[:6] if info['ret'] > 0.2]
        top_shorts = [s for s, info in sorted_by_ret[-6:] if info['ret'] < -0.2]

        trades = []
        open_pos = {}
        traded_symbols = set()

        for idx, t in enumerate(day_times):
            h, m = int(t.split(':')[0]), int(t.split(':')[1])
            is_mkt = (h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)
            can_enter = ((h == 9 and m >= 30) or (h > 9 and h < 13 and m <= 30))
            is_eod = (h == 15 and m >= 10)
            if not is_mkt: continue

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

                    if pnl_pct >= target_pct:
                        exit_now = True; reason = f'Target {target_pct*100:.1f}%'
                    elif pnl_pct <= -sl_pct:
                        exit_now = True; reason = f'SL -{sl_pct*100:.2f}%'
                    elif max_fav >= be_trigger and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'BE Guard'
                    elif dur_min >= 35 and abs(pnl_pct) <= 0.001:
                        exit_now = True; reason = 'Stagnation (35m)'

                elif pos['dir'] == 'SHORT':
                    if ltp < pos['trough']: pos['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos['trough']) / entry

                    if pnl_pct >= target_pct:
                        exit_now = True; reason = f'Target {target_pct*100:.1f}%'
                    elif pnl_pct <= -sl_pct:
                        exit_now = True; reason = f'SL -{sl_pct*100:.2f}%'
                    elif max_fav >= be_trigger and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'BE Guard'
                    elif dur_min >= 35 and abs(pnl_pct) <= 0.001:
                        exit_now = True; reason = 'Stagnation (35m)'

                if exit_now:
                    pnl = (ltp - entry) * qty if pos['dir'] == 'LONG' else (entry - ltp) * qty
                    trades.append({
                        'date': d, 'sym': s, 'dir': pos['dir'], 'entry': entry, 'exit': ltp,
                        'qty': qty, 'pnl': round(pnl, 2), 'entry_t': pos['entry_t'],
                        'exit_t': t, 'reason': reason
                    })
                    to_close.append(s)

            for s in to_close: del open_pos[s]

            if can_enter and len(open_pos) < 2 and len(trades) + len(open_pos) < max_trades and not is_eod:
                # Fade the high
                for s in top_longs:
                    if s in open_pos or s in traded_symbols: continue
                    bars = day_data[s]
                    bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                    if bar_idx is None or bar_idx < 1: continue
                    bar = bars[bar_idx]
                    ltp = bar['close']
                    vwap = vwap_map[s][bar_idx]
                    hl = base_stats[s]

                    near_high = (bar['high'] >= hl['high'] * 0.998)
                    is_rejection_red = (bar['close'] < bar['open'] and bar['close'] < hl['high'])
                    room_to_vwap = ((ltp - vwap) / vwap >= 0.005)

                    if near_high and is_rejection_red and room_to_vwap:
                        qty = int(10000.0 * 1.5 / ltp)
                        if qty >= 1:
                            open_pos[s] = {'sym': s, 'dir': 'SHORT', 'entry': ltp, 'qty': qty, 'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t}
                            traded_symbols.add(s)
                            break

                if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_trades: continue

                # Fade the low
                for s in top_shorts:
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
                    room_to_vwap = ((vwap - ltp) / vwap >= 0.005)

                    if near_low and is_support_green and room_to_vwap:
                        qty = int(10000.0 * 1.5 / ltp)
                        if qty >= 1:
                            open_pos[s] = {'sym': s, 'dir': 'LONG', 'entry': ltp, 'qty': qty, 'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t}
                            traded_symbols.add(s)
                            break

        all_trades.extend(trades)

    tot_pnl = sum(t['pnl'] for t in all_trades)
    wins = [t for t in all_trades if t['pnl'] > 0]
    losses = [t for t in all_trades if t['pnl'] < 0]
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = np.mean([abs(t['pnl']) for t in losses]) if losses else 0
    wr = len(wins) / len(all_trades) * 100 if all_trades else 0
    fees = len(all_trades) * 18.0
    print(f"Trades: {len(all_trades)} | Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {wr:.1f}%")
    print(f"Avg Win: Rs.{avg_win:.1f} | Avg Loss: Rs.{avg_loss:.1f} | Profit Factor: {avg_win*len(wins)/(avg_loss*len(losses) if losses else 1):.2f}")
    print(f"Gross P&L: Rs.{tot_pnl:+,.2f} | Fees: -Rs.{fees:.2f} | NET PROFIT: Rs.{tot_pnl - fees:+,.2f}")

if __name__ == '__main__':
    test_asymmetric_edge()
