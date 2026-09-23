import json
from pathlib import Path
import numpy as np

data = json.loads(Path('data/intraday_15d_cache.json').read_text())
config = json.loads(Path('data/config.json').read_text())

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

def test_breadth_filter():
    print("=" * 85)
    print("   TESTING MARKET DISPERSION & BREADTH FILTER ON SMART ALGO   ")
    print("=========================================================================")

    all_trades = []
    daily_results = {}
    skipped_days = []

    for d in dates:
        day_data = {s: [b for b in bars if b['date'] == d] for s, bars in data.items()}
        day_times = sorted(list(set(b['time_str'] for s in day_data.values() for b in s)))
        if not day_times: continue

        # VWAPs
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
                }

        all_rets = [info['ret_base'] for info in base_hl.values()]
        all_rets.sort()

        # BREADTH FILTER:
        # Measure market vitality: How many stocks have moved > 0.5% in either direction?
        active_movers = [r for r in all_rets if abs(r) >= 0.5]
        spread = all_rets[-1] - all_rets[0] if all_rets else 0.0 # top minus bottom return
        
        # If market has no vitality (fewer than 7 stocks moved >0.5% OR spread < 1.8%): CHOP DAY!
        is_vital_market = (len(active_movers) >= 7 and spread >= 1.8)

        if not is_vital_market:
            skipped_days.append((d, len(active_movers), spread))
            daily_results[d] = {'trades': [], 'pnl': 0.0, 'status': 'SKIPPED (CHOP FILTER)'}
            continue

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
                    exit_now = True; reason = 'EOD Square-off'
                elif pos['dir'] == 'LONG':
                    if ltp > pos['peak']: pos['peak'] = ltp
                    pnl_pct = (ltp - entry) / entry
                    max_fav = (pos['peak'] - entry) / entry

                    if pnl_pct >= 0.02:
                        exit_now = True; reason = 'Target 2.0%'
                    elif pnl_pct <= -0.01:
                        exit_now = True; reason = 'SL -1.0%'
                    elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'Breakeven Guard'
                    elif max_fav >= 0.008 and ((pos['peak'] - ltp) / pos['peak']) >= 0.004:
                        exit_now = True; reason = 'Trailing SL'
                    elif dur_min >= 30 and abs(pnl_pct) <= 0.002:
                        exit_now = True; reason = 'Stagnation (30m)'

                elif pos['dir'] == 'SHORT':
                    if ltp < pos['trough']: pos['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos['trough']) / entry

                    if pnl_pct >= 0.02:
                        exit_now = True; reason = 'Target 2.0%'
                    elif pnl_pct <= -0.01:
                        exit_now = True; reason = 'SL -1.0%'
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

            # --- Check Entries ---
            if can_enter and len(open_pos) < 2 and not is_eod:
                for s, hl in base_hl.items():
                    if len(open_pos) >= 2 or s in open_pos or s in traded_symbols: continue

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
                    eff_b = 10000.0 * strat.get('leverage', 1.0)

                    is_rs_leader = (hl['ret_base'] >= top_threshold)
                    is_rs_laggard = (hl['ret_base'] <= bottom_threshold)

                    if is_rs_leader and ltp >= hl['high'] * (1 + buf_long) and ltp > vwap:
                        qty = int(eff_b / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'LONG', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            continue

                    if can_short and is_rs_laggard and ltp <= hl['low'] * (1 - buf_short) and ltp < vwap:
                        qty = int(eff_b / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'SHORT', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            continue

        day_pnl = sum(t['pnl'] for t in trades)
        daily_results[d] = {'trades': trades, 'pnl': day_pnl, 'status': 'TRADED'}
        all_trades.extend(trades)

    print(f"{'DATE':<12} | {'TRADES':<8} {'W/L':<8} | {'NET PnL':<14} {'STATUS'}")
    print("-" * 85)
    for d, res in sorted(daily_results.items()):
        trs = res['trades']
        wins = len([t for t in trs if t['pnl'] > 0])
        losses = len([t for t in trs if t['pnl'] < 0])
        print(f"{d:<12} | {len(trs):<8} {wins}W/{losses}L   | Rs.{res['pnl']:+8.2f}    {res['status']}")

    tot_pnl = sum(t['pnl'] for t in all_trades)
    tot_w = len([t for t in all_trades if t['pnl'] > 0])
    tot_l = len([t for t in all_trades if t['pnl'] < 0])
    wr = (tot_w / len(all_trades) * 100) if all_trades else 0.0
    print("=" * 85)
    print(f"GRAND TOTAL: {len(all_trades)} Trades | {tot_w}W/{tot_l}L ({wr:.1f}% WR) | Net P&L: Rs.{tot_pnl:+,.2f}")
    fees = len(all_trades) * 18.0
    print(f"Estimated Fees (@ Rs.18/trade): -Rs.{fees:.2f} | Net After Fees: Rs.{tot_pnl-fees:+,.2f}")
    print("=" * 85)

if __name__ == '__main__':
    test_breadth_filter()
