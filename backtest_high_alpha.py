import json
from pathlib import Path
import numpy as np

data = json.loads(Path('data/intraday_15d_cache.json').read_text())
config = json.loads(Path('data/config.json').read_text())
sector_matrix = json.loads(Path('data/sector_matrix.json').read_text())

dates = sorted(list(set(b['date'] for bars in data.values() for b in bars)))
period_prev = ['2026-09-09', '2026-09-10', '2026-09-11', '2026-09-15', '2026-09-16']
period_recent = ['2026-09-17', '2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23']

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

def run_simulation(
    rvol_mult=1.2,
    rs_percentile=0.30, # Top 30% RS leaders only
    require_candle_close=True,
    target_pct=0.035, # 3.5% runner target
    sl_pct=0.010,     # strict 1.0% SL floor
    be_trigger=0.007, # at +0.7%, move SL to BE (+0.1%)
    trail_trigger=0.012, # at +1.2%, trail by 0.6%
    trail_dist=0.006,
    vwap_exit=True,   # exit if bar closes below VWAP (for long)
    max_positions=2,
    budget=10000.0,
    test_dates=None
):
    if test_dates is None:
        test_dates = dates

    all_trades = []
    daily_results = {}

    for d in test_dates:
        day_data = {s: [b for b in bars if b['date'] == d] for s, bars in data.items()}
        day_times = sorted(list(set(b['time_str'] for s in day_data.values() for b in s)))
        if not day_times: continue

        # VWAPs & 20-bar rolling volumes
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
                    'avg_vol': np.mean([b.get('volume', 1) for b in base_bars])
                }

        # RS Ranking (Strict Top / Bottom percentile)
        all_rets = sorted([info['ret_base'] for info in base_hl.values()])
        if all_rets:
            top_threshold = all_rets[int(len(all_rets) * (1.0 - rs_percentile))]
            bottom_threshold = all_rets[int(len(all_rets) * rs_percentile)]
        else:
            top_threshold = 0.5; bottom_threshold = -0.5

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

                    # 1. Target
                    if pnl_pct >= target_pct:
                        exit_now = True; reason = f'Target {target_pct*100:.1f}%'
                    # 2. Hard Stop Loss
                    elif pnl_pct <= -sl_pct:
                        exit_now = True; reason = f'SL -{sl_pct*100:.1f}%'
                    # 3. Breakeven Guard (moved to +0.1% once peak >= +0.7%)
                    elif max_fav >= be_trigger and pnl_pct <= 0.001:
                        exit_now = True; reason = 'Breakeven Guard'
                    # 4. Trailing Stop
                    elif max_fav >= trail_trigger and ((pos['peak'] - ltp) / pos['peak']) >= trail_dist:
                        exit_now = True; reason = 'Trailing SL'
                    # 5. Institutional Trend Filter (Exit if 2m bar closes below VWAP after holding > 15m)
                    elif vwap_exit and dur_min >= 15 and ltp < vwap and pnl_pct < 0:
                        exit_now = True; reason = 'VWAP Breakdown Exit'

                elif pos['dir'] == 'SHORT':
                    if ltp < pos['trough']: pos['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos['trough']) / entry

                    if pnl_pct >= target_pct:
                        exit_now = True; reason = f'Target {target_pct*100:.1f}%'
                    elif pnl_pct <= -sl_pct:
                        exit_now = True; reason = f'SL -{sl_pct*100:.1f}%'
                    elif max_fav >= be_trigger and pnl_pct <= 0.001:
                        exit_now = True; reason = 'Breakeven Guard'
                    elif max_fav >= trail_trigger and ((ltp - pos['trough']) / pos['trough']) >= trail_dist:
                        exit_now = True; reason = 'Trailing SL'
                    elif vwap_exit and dur_min >= 15 and ltp > vwap and pnl_pct < 0:
                        exit_now = True; reason = 'VWAP Reversal Exit'

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
                    bar_vol = bar.get('volume', 1)
                    avg_vol = vol_avg_map[s][bar_idx]
                    rvol = bar_vol / avg_vol if avg_vol > 0 else 1.0

                    strat = config.get(s, {})
                    buf = strat.get('breakout_long', 0.0025)
                    can_short = strat.get('allow_short', True)
                    eff_b = budget * strat.get('leverage', 1.0)

                    # Filters:
                    # A. Strict Relative Strength
                    is_rs_leader = (hl['ret_base'] >= top_threshold)
                    is_rs_laggard = (hl['ret_base'] <= bottom_threshold)

                    # B. Relative Volume (Institutional Participation)
                    has_vol_expansion = (rvol >= rvol_mult)

                    # C. Candle Body Close Check (bar close above high * (1+buf), not just high wick)
                    check_price_long = bar['close'] if require_candle_close else ltp
                    check_price_short = bar['close'] if require_candle_close else ltp

                    # LONG ENTRY
                    if is_rs_leader and has_vol_expansion and check_price_long >= hl['high'] * (1 + buf) and ltp > vwap:
                        qty = int(eff_b / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'LONG', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            continue

                    # SHORT ENTRY
                    if can_short and is_rs_laggard and has_vol_expansion and check_price_short <= hl['low'] * (1 - buf) and ltp < vwap:
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

    return daily_results, all_trades

if __name__ == '__main__':
    # Test across all 14 available days
    print("Testing High-Conviction Institutional Alpha Algorithm across ALL 14 DAYS...")
    daily_res, all_tr = run_simulation()

    print("\n" + "=" * 90)
    print(f"{'DATE':<12} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'KEY TRADES'}")
    print("-" * 90)

    for d in sorted(daily_res.keys()):
        res = daily_res[d]
        trs = res['trades']
        wins = [t for t in trs if t['pnl'] > 0]
        losses = [t for t in trs if t['pnl'] < 0]
        wr = (len(wins) / len(trs) * 100) if trs else 0.0
        best_t = max(trs, key=lambda x: x['pnl']) if trs else None
        best_str = f"{best_t['sym']} (+Rs.{best_t['pnl']:.1f})" if best_t and best_t['pnl'] > 0 else ""
        print(f"{d:<12} | {len(trs):<8} {len(wins)}W/{len(losses)}L   {wr:>6.1f}%    | Rs.{res['pnl']:+8.2f}    {best_str}")

    tot_wins = len([t for t in all_tr if t['pnl'] > 0])
    tot_loss = len([t for t in all_tr if t['pnl'] < 0])
    tot_pnl = sum(t['pnl'] for t in all_tr)
    wr = (tot_wins / len(all_tr) * 100) if all_tr else 0.0
    print("=" * 90)
    print(f"GRAND TOTAL (14 DAYS): {len(all_tr)} Trades | {tot_wins}W/{tot_loss}L ({wr:.1f}% Win Rate) | Net P&L: Rs.{tot_pnl:+,.2f}")
    print("=" * 90)
