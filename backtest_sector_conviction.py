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

def simulate_sector_conviction(
    target_pct=0.030,     # 3.0% runner target
    sl_pct=0.008,         # tight 0.8% stop loss
    be_pct=0.006,         # at +0.6%, move SL to Breakeven (+0.05%)
    trail_trigger=0.012,  # at +1.2%, trail by 0.5%
    trail_dist=0.005,
    max_daily_trades=3,   # MAX 2-3 TRADES PER DAY
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

        # VWAPs
        vwap_map = {}
        for s, bars in day_data.items():
            if bars:
                vwap_map[s] = compute_vwap(bars)

        # 1. 09:15 - 09:35 Base Range
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
                    'sector': sector_matrix.get(s, {}).get('sector', 'OTHER')
                }

        # 2. Sector Performance Ranking at 09:35
        # Calculate average return per sector
        sectors = {}
        for s, info in base_hl.items():
            sec = info['sector']
            sectors.setdefault(sec, []).append(info['ret_base'])
        
        sector_perf = {sec: np.mean(rets) for sec, rets in sectors.items()}
        sorted_sectors = sorted(sector_perf.items(), key=lambda x: x[1], reverse=True)
        
        top_sector = sorted_sectors[0][0] if sorted_sectors else None
        top_sector_ret = sorted_sectors[0][1] if sorted_sectors else 0.0
        bottom_sector = sorted_sectors[-1][0] if sorted_sectors else None
        bottom_sector_ret = sorted_sectors[-1][1] if sorted_sectors else 0.0

        # Only consider leading sectors with real momentum (>= +0.4% sector average)
        # and lagging sectors (<= -0.4% sector average)
        bullish_sectors = [sec for sec, r in sorted_sectors if r >= 0.35]
        bearish_sectors = [sec for sec, r in sorted_sectors if r <= -0.35]

        # Stock RS ranking
        all_rets = sorted([info['ret_base'] for info in base_hl.values()])
        top_stock_threshold = all_rets[-5] if len(all_rets) >= 5 else 0.5 # Top 5 stocks only
        bottom_stock_threshold = all_rets[4] if len(all_rets) >= 5 else -0.5 # Bottom 5 stocks only

        trades = []
        open_pos = {}
        traded_symbols = set()

        for idx, t in enumerate(day_times):
            h, m = int(t.split(':')[0]), int(t.split(':')[1])
            is_mkt = (h == 9 and m >= 15) or (h > 9 and h < 15) or (h == 15 and m <= 30)
            can_enter = ((h == 9 and m >= 35) or (h > 9 and h < 13 and m <= 30)) # 13:30 cutoff
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

                    if pnl_pct >= target_pct:
                        exit_now = True; reason = f'Target {target_pct*100:.1f}%'
                    elif pnl_pct <= -sl_pct:
                        exit_now = True; reason = f'SL -{sl_pct*100:.1f}%'
                    elif max_fav >= be_pct and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'Breakeven Guard'
                    elif max_fav >= trail_trigger and ((pos['peak'] - ltp) / pos['peak']) >= trail_dist:
                        exit_now = True; reason = 'Trailing SL'
                    elif dur_min >= 45 and abs(pnl_pct) <= 0.0015:
                        exit_now = True; reason = 'Stagnation (45m)'

                elif pos['dir'] == 'SHORT':
                    if ltp < pos['trough']: pos['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos['trough']) / entry

                    if pnl_pct >= target_pct:
                        exit_now = True; reason = f'Target {target_pct*100:.1f}%'
                    elif pnl_pct <= -sl_pct:
                        exit_now = True; reason = f'SL -{sl_pct*100:.1f}%'
                    elif max_fav >= be_pct and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'Breakeven Guard'
                    elif max_fav >= trail_trigger and ((ltp - pos['trough']) / pos['trough']) >= trail_dist:
                        exit_now = True; reason = 'Trailing SL'
                    elif dur_min >= 45 and abs(pnl_pct) <= 0.0015:
                        exit_now = True; reason = 'Stagnation (45m)'

                if exit_now:
                    pnl = (ltp - entry) * qty if pos['dir'] == 'LONG' else (entry - ltp) * qty
                    trades.append({
                        'sym': s, 'dir': pos['dir'], 'pnl': round(pnl, 2),
                        'entry': entry, 'exit': ltp, 'qty': qty,
                        'entry_t': pos['entry_t'], 'exit_t': t, 'reason': reason
                    })
                    to_close.append(s)

            for s in to_close: del open_pos[s]

            # --- Check Entries (High Conviction Only) ---
            if can_enter and len(open_pos) < 2 and len(trades) + len(open_pos) < max_daily_trades and not is_eod:
                # Find best breakout candidates
                for s, hl in base_hl.items():
                    if s in open_pos or s in traded_symbols: continue

                    sec = hl['sector']
                    ret_base = hl['ret_base']

                    # Sector alignment requirement:
                    # Longs: Stock must be in top 5 stocks AND in a bullish sector
                    # Shorts: Stock must be in bottom 5 stocks AND in a bearish sector
                    is_bull_candidate = (ret_base >= top_stock_threshold and sec in bullish_sectors)
                    is_bear_candidate = (ret_base <= bottom_stock_threshold and sec in bearish_sectors)

                    if not is_bull_candidate and not is_bear_candidate:
                        continue

                    bars = day_data[s]
                    bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                    if bar_idx is None: continue
                    bar = bars[bar_idx]
                    ltp = bar['close']
                    vwap = vwap_map[s][bar_idx]

                    strat = config.get(s, {})
                    buf = strat.get('breakout_long', 0.0025)
                    can_short = strat.get('allow_short', True)
                    eff_b = budget * strat.get('leverage', 1.0)

                    # LONG ENTRY: Sector Bullish + Top 5 Stock + Breakout Base High + Above VWAP
                    if is_bull_candidate and ltp >= hl['high'] * (1 + buf) and ltp > vwap:
                        qty = int(eff_b / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'LONG', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_daily_trades:
                                break

                    # SHORT ENTRY: Sector Bearish + Bottom 5 Stock + Breakdown Base Low + Below VWAP
                    elif can_short and is_bear_candidate and ltp <= hl['low'] * (1 - buf) and ltp < vwap:
                        qty = int(eff_b / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'SHORT', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_daily_trades:
                                break

        day_pnl = sum(t['pnl'] for t in trades)
        daily_results[d] = {'trades': trades, 'pnl': day_pnl}
        all_trades.extend(trades)

    return daily_results, all_trades

if __name__ == '__main__':
    daily_res, all_tr = simulate_sector_conviction()
    print("=" * 95)
    print("   SECTOR-DRIVEN HIGH CONVICTION INSTITUTIONAL ALPHA: 14-DAY AUDIT   ")
    print("===================================================================================")
    print(f"{'DATE':<12} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'KEY TRADES'}")
    print("-" * 95)

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
    print("=" * 95)
    print(f"GRAND TOTAL (14 DAYS): {len(all_tr)} Trades | {tot_wins}W/{tot_loss}L ({wr:.1f}% Win Rate) | Net P&L: Rs.{tot_pnl:+,.2f}")
    
    # Calculate transaction costs: approx Rs.18 per trade
    friction = len(all_tr) * 18.0
    net_after_friction = tot_pnl - friction
    print(f"Estimated Exchange Fees (STT, GST, SEBI @ Rs.18/trade): -Rs.{friction:.2f}")
    print(f"NET PROFIT AFTER ALL TRANSACTION FEES: Rs.{net_after_friction:+,.2f} ({(net_after_friction/10000.0)*100:+.2f}% on Capital)")
    print("=" * 95)
