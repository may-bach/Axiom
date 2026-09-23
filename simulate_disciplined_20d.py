import json
from pathlib import Path
import numpy as np

data = json.loads(Path('data/intraday_23d_cache.json').read_text())
config = json.loads(Path('data/config.json').read_text())

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

def simulate_disciplined_smart_algo(test_dates, max_trades_per_day=2, budget=10000.0):
    all_trades = []
    daily_results = {}

    for d in test_dates:
        day_data = {s: [b for b in bars if b['date'] == d] for s, bars in data.items()}
        day_times = sorted(list(set(b['time_str'] for s in day_data.values() for b in s)))
        if not day_times: continue

        vwap_map = {s: compute_vwap(bars) for s, bars in day_data.items() if bars}

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

        # Top 20% RS Ranking (Strict elite leaders)
        all_rets = sorted([info['ret_base'] for info in base_hl.values()])
        top_threshold = all_rets[int(len(all_rets) * 0.75)]
        bottom_threshold = all_rets[int(len(all_rets) * 0.25)]

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

                    # Target: 2.2%
                    if pnl_pct >= 0.022:
                        exit_now = True; reason = 'Target 2.2%'
                    # Hard SL: 1.0%
                    elif pnl_pct <= -0.010:
                        exit_now = True; reason = 'SL -1.0%'
                    # Breakeven Guard: if peak >= +0.5%, ratchet stop to breakeven (+0.05%)
                    elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'Breakeven Guard'
                    # Trailing SL: if peak >= +0.8%, trail by 0.4% from peak
                    elif max_fav >= 0.008 and ((pos['peak'] - ltp) / pos['peak']) >= 0.004:
                        exit_now = True; reason = 'Trailing SL'
                    # Stagnation exit: if flat after 30 mins
                    elif dur_min >= 30 and abs(pnl_pct) <= 0.0015:
                        exit_now = True; reason = 'Stagnation (30m)'

                elif pos['dir'] == 'SHORT':
                    if ltp < pos['trough']: pos['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - pos['trough']) / entry

                    if pnl_pct >= 0.022:
                        exit_now = True; reason = 'Target 2.2%'
                    elif pnl_pct <= -0.010:
                        exit_now = True; reason = 'SL -1.0%'
                    elif max_fav >= 0.005 and pnl_pct <= 0.0005:
                        exit_now = True; reason = 'Breakeven Guard'
                    elif max_fav >= 0.008 and ((ltp - pos['trough']) / pos['trough']) >= 0.004:
                        exit_now = True; reason = 'Trailing SL'
                    elif dur_min >= 30 and abs(pnl_pct) <= 0.0015:
                        exit_now = True; reason = 'Stagnation (30m)'

                if exit_now:
                    pnl = (ltp - entry) * qty if pos['dir'] == 'LONG' else (entry - ltp) * qty
                    trades.append({
                        'date': d, 'sym': s, 'dir': pos['dir'], 'entry': entry, 'exit': ltp,
                        'qty': qty, 'pnl': round(pnl, 2), 'entry_t': pos['entry_t'],
                        'exit_t': t, 'reason': reason
                    })
                    to_close.append(s)

            for s in to_close: del open_pos[s]

            # --- Check Entries (Strict Max 2 Trades Per Day) ---
            if can_enter and len(open_pos) < 2 and len(trades) + len(open_pos) < max_trades_per_day and not is_eod:
                for s, hl in base_hl.items():
                    if s in open_pos or s in traded_symbols: continue

                    bars = day_data[s]
                    bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                    if bar_idx is None: continue
                    bar = bars[bar_idx]
                    ltp = bar['close']
                    vwap = vwap_map[s][bar_idx]

                    strat = config.get(s, {})
                    buf = strat.get('breakout_long', 0.003)
                    eff_b = budget * strat.get('leverage', 1.0)
                    if eff_b <= 0: eff_b = budget * 1.5

                    is_rs_leader = (hl['ret_base'] >= top_threshold)
                    is_rs_laggard = (hl['ret_base'] <= bottom_threshold)

                    # LONG ENTRY
                    if is_rs_leader and ltp >= hl['high'] * (1 + buf) and ltp > vwap:
                        qty = int(eff_b / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'LONG', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_trades_per_day:
                                break

                    # SHORT ENTRY
                    elif is_rs_laggard and ltp <= hl['low'] * (1 - buf) and ltp < vwap:
                        qty = int(eff_b / ltp)
                        if qty >= 1:
                            open_pos[s] = {
                                'sym': s, 'dir': 'SHORT', 'entry': ltp, 'qty': qty,
                                'peak': ltp, 'trough': ltp, 'time': bar['timestamp'], 'entry_t': t
                            }
                            traded_symbols.add(s)
                            if len(open_pos) >= 2 or len(trades) + len(open_pos) >= max_trades_per_day:
                                break

        day_pnl = sum(t['pnl'] for t in trades)
        daily_results[d] = {'trades': trades, 'pnl': day_pnl}
        all_trades.extend(trades)

    return daily_results, all_trades

def run_evaluation():
    print("=" * 100)
    print("   DISCIPLINED SMART ALGO 20-DAY AUDIT (MAX 2 TRADES/DAY, TOP 20% RS, BE GUARD)   ")
    print("=========================================================================================")

    # Period 1: Prior 10 Trading Days (Aug 26 - Sep 08)
    print(">>> PERIOD 1: PRIOR 10 TRADING DAYS (Aug 26 - Sep 08)")
    print(f"{'DATE':<12} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'TRADES'}")
    print("-" * 100)
    p1_res, p1_tr = simulate_disciplined_smart_algo(prior_10)
    p1_pnl = 0.0
    for d in prior_10:
        res = p1_res.get(d, {'trades': [], 'pnl': 0.0})
        trs = res['trades']
        wins = [t for t in trs if t['pnl'] > 0]
        losses = [t for t in trs if t['pnl'] < 0]
        wr = (len(wins) / len(trs) * 100) if trs else 0.0
        p1_pnl += res['pnl']
        sample_str = ", ".join([f"{t['sym']}({t['pnl']:+.1f})" for t in trs])
        print(f"{d:<12} | {len(trs):<8} {len(wins)}W/{len(losses)}L   {wr:>6.1f}%    | Rs.{res['pnl']:+8.2f}    {sample_str}")

    p1_w = len([t for t in p1_tr if t['pnl'] > 0])
    p1_l = len([t for t in p1_tr if t['pnl'] < 0])
    p1_wr = (p1_w / len(p1_tr) * 100) if p1_tr else 0.0
    fees1 = len(p1_tr) * 18.0
    print("-" * 100)
    print(f"SUBTOTAL PRIOR 10D: {len(p1_tr)} Trades | {p1_w}W/{p1_l}L ({p1_wr:.1f}% WR) | Gross PnL: Rs.{p1_pnl:+,.2f} | Fees: -Rs.{fees1:.2f} | PURE NET: Rs.{p1_pnl - fees1:+,.2f}")
    print("=" * 100)

    # Period 2: Recent 10 Trading Days (Sep 09 - Sep 23)
    print("\n>>> PERIOD 2: RECENT 10 TRADING DAYS (Sep 09 - Sep 23)")
    print(f"{'DATE':<12} | {'TRADES':<8} {'W/L':<8} {'WIN RATE':<10} | {'NET PnL':<14} {'TRADES'}")
    print("-" * 100)
    p2_res, p2_tr = simulate_disciplined_smart_algo(recent_10)
    p2_pnl = 0.0
    for d in recent_10:
        res = p2_res.get(d, {'trades': [], 'pnl': 0.0})
        trs = res['trades']
        wins = [t for t in trs if t['pnl'] > 0]
        losses = [t for t in trs if t['pnl'] < 0]
        wr = (len(wins) / len(trs) * 100) if trs else 0.0
        p2_pnl += res['pnl']
        sample_str = ", ".join([f"{t['sym']}({t['pnl']:+.1f})" for t in trs])
        print(f"{d:<12} | {len(trs):<8} {len(wins)}W/{len(losses)}L   {wr:>6.1f}%    | Rs.{res['pnl']:+8.2f}    {sample_str}")

    p2_w = len([t for t in p2_tr if t['pnl'] > 0])
    p2_l = len([t for t in p2_tr if t['pnl'] < 0])
    p2_wr = (p2_w / len(p2_tr) * 100) if p2_tr else 0.0
    fees2 = len(p2_tr) * 18.0
    print("-" * 100)
    print(f"SUBTOTAL RECENT 10D: {len(p2_tr)} Trades | {p2_w}W/{p2_l}L ({p2_wr:.1f}% WR) | Gross PnL: Rs.{p2_pnl:+,.2f} | Fees: -Rs.{fees2:.2f} | PURE NET: Rs.{p2_pnl - fees2:+,.2f}")
    print("=" * 100)

    tot_tr = p1_tr + p2_tr
    tot_pnl = p1_pnl + p2_pnl
    tot_w = p1_w + p2_w
    tot_l = p1_l + p2_l
    tot_wr = (tot_w / len(tot_tr) * 100) if tot_tr else 0.0
    fees = len(tot_tr) * 18.0
    net_after_fees = tot_pnl - fees
    print(f"*** 20-DAY GRAND TOTAL: {len(tot_tr)} Trades (Avg {len(tot_tr)/20:.1f} trades/day) | {tot_w}W/{tot_l}L ({tot_wr:.1f}% Win Rate) ***")
    print(f"*** Gross P&L: Rs.{tot_pnl:+,.2f} | Total Exchange Fees: -Rs.{fees:.2f} | PURE NET PROFIT: Rs.{net_after_fees:+,.2f} ({(net_after_fees/10000.0)*100:+.2f}% on Capital) ***")
    print("=" * 100)

if __name__ == '__main__':
    run_evaluation()
