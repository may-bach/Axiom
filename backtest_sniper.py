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

def test_sniper_strategy():
    print("=" * 90)
    print("   TESTING THE 'SNIPER' PARADIGM: MAX 1 HIGH-CONVICTION TRADE PER DAY   ")
    print("=========================================================================")
    
    trades = []
    daily_results = {}

    for d in dates:
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
                vols = [b.get('volume', 1) for b in base_bars]
                base_hl[s] = {
                    'open': open_p,
                    'high': max(b['high'] for b in base_bars),
                    'low': min(b['low'] for b in base_bars),
                    'ret_base': (close_p - open_p) / open_p * 100,
                    'avg_vol': np.mean(vols)
                }

        # Rank by absolute return at 09:35
        ranked_longs = sorted([(s, info['ret_base']) for s, info in base_hl.items() if info['ret_base'] > 0.5], key=lambda x: x[1], reverse=True)
        ranked_shorts = sorted([(s, info['ret_base']) for s, info in base_hl.items() if info['ret_base'] < -0.5], key=lambda x: x[1])

        # Find the single best setup that triggers breakout with volume
        day_trade = None

        for t in day_times:
            if day_trade is not None: break
            h, m = int(t.split(':')[0]), int(t.split(':')[1])
            if not ((h == 9 and m >= 35) or (h > 9 and h < 13)): continue # Only 09:35 to 13:00

            # Check top 3 long leaders
            for s, r in ranked_longs[:3]:
                bars = day_data[s]
                bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                if bar_idx is None or bar_idx < 1: continue
                bar = bars[bar_idx]
                ltp = bar['close']
                vwap = vwap_map[s][bar_idx]
                hl = base_hl[s]

                # Check breakout above high + 0.3% and LTP > VWAP
                if ltp >= hl['high'] * 1.003 and ltp > vwap:
                    # Enter Sniper Long
                    day_trade = {
                        'date': d, 'sym': s, 'dir': 'LONG', 'entry': ltp, 'entry_t': t,
                        'peak': ltp, 'qty': int(10000.0 * 1.5 / ltp), 'time': bar['timestamp']
                    }
                    break

            if day_trade is not None: break

            # Check top 3 short laggards
            for s, r in ranked_shorts[:3]:
                bars = day_data[s]
                bar_idx = next((i for i, b in enumerate(bars) if b['time_str'] == t), None)
                if bar_idx is None or bar_idx < 1: continue
                bar = bars[bar_idx]
                ltp = bar['close']
                vwap = vwap_map[s][bar_idx]
                hl = base_hl[s]

                if ltp <= hl['low'] * 0.997 and ltp < vwap:
                    # Enter Sniper Short
                    day_trade = {
                        'date': d, 'sym': s, 'dir': 'SHORT', 'entry': ltp, 'entry_t': t,
                        'trough': ltp, 'qty': int(10000.0 * 1.5 / ltp), 'time': bar['timestamp']
                    }
                    break

        # Simulate the single trade through the rest of the day
        if day_trade is not None:
            s = day_trade['sym']
            bars = day_data[s]
            entry_t = day_trade['entry_t']
            entry_idx = next(i for i, b in enumerate(bars) if b['time_str'] == entry_t)
            entry = day_trade['entry']
            qty = day_trade['qty']
            
            trade_closed = False
            for bar_idx in range(entry_idx + 1, len(bars)):
                bar = bars[bar_idx]
                t = bar['time_str']
                h, m = int(t.split(':')[0]), int(t.split(':')[1])
                ltp = bar['close']
                is_eod = (h == 15 and m >= 10)

                if day_trade['dir'] == 'LONG':
                    if ltp > day_trade['peak']: day_trade['peak'] = ltp
                    pnl_pct = (ltp - entry) / entry
                    max_fav = (day_trade['peak'] - entry) / entry

                    # Runner target: 3.0%
                    if pnl_pct >= 0.030 or is_eod or pnl_pct <= -0.010:
                        pnl = (ltp - entry) * qty
                        reason = 'Target 3.0%' if pnl_pct >= 0.030 else ('SL -1.0%' if pnl_pct <= -0.010 else 'EOD')
                        trades.append({**day_trade, 'exit': ltp, 'exit_t': t, 'pnl': round(pnl, 2), 'reason': reason})
                        trade_closed = True
                        break
                    # Breakeven guard at +0.7%
                    elif max_fav >= 0.007 and pnl_pct <= 0.001:
                        pnl = (ltp - entry) * qty
                        trades.append({**day_trade, 'exit': ltp, 'exit_t': t, 'pnl': round(pnl, 2), 'reason': 'BE Guard'})
                        trade_closed = True
                        break
                    # Trailing at +1.2% by 0.5%
                    elif max_fav >= 0.012 and ((day_trade['peak'] - ltp) / day_trade['peak']) >= 0.005:
                        pnl = (ltp - entry) * qty
                        trades.append({**day_trade, 'exit': ltp, 'exit_t': t, 'pnl': round(pnl, 2), 'reason': 'Trailing SL'})
                        trade_closed = True
                        break

                elif day_trade['dir'] == 'SHORT':
                    if ltp < day_trade['trough']: day_trade['trough'] = ltp
                    pnl_pct = (entry - ltp) / entry
                    max_fav = (entry - day_trade['trough']) / entry

                    if pnl_pct >= 0.030 or is_eod or pnl_pct <= -0.010:
                        pnl = (entry - ltp) * qty
                        reason = 'Target 3.0%' if pnl_pct >= 0.030 else ('SL -1.0%' if pnl_pct <= -0.010 else 'EOD')
                        trades.append({**day_trade, 'exit': ltp, 'exit_t': t, 'pnl': round(pnl, 2), 'reason': reason})
                        trade_closed = True
                        break
                    elif max_fav >= 0.007 and pnl_pct <= 0.001:
                        pnl = (entry - ltp) * qty
                        trades.append({**day_trade, 'exit': ltp, 'exit_t': t, 'pnl': round(pnl, 2), 'reason': 'BE Guard'})
                        trade_closed = True
                        break
                    elif max_fav >= 0.012 and ((ltp - day_trade['trough']) / day_trade['trough']) >= 0.005:
                        pnl = (entry - ltp) * qty
                        trades.append({**day_trade, 'exit': ltp, 'exit_t': t, 'pnl': round(pnl, 2), 'reason': 'Trailing SL'})
                        trade_closed = True
                        break

    print(f"{'DATE':<12} | {'TRADE':<25} | {'ENTRY -> EXIT':<25} | {'NET PnL':<12} {'REASON'}")
    print("-" * 90)
    for t in trades:
        pnl_s = f"Rs.{t['pnl']:+8.2f}"
        print(f"{t['date']:<12} | {t['dir']} {t['sym']:<18} | [{t['entry_t']} -> {t['exit_t']}] @ {t['entry']:.1f}->{t['exit']:.1f} | {pnl_s:<12} {t['reason']}")

    tot_pnl = sum(t['pnl'] for t in trades)
    wins = [t for t in trades if t['pnl'] > 0]
    wr = len(wins) / len(trades) * 100 if trades else 0.0
    print("=" * 90)
    print(f"GRAND TOTAL: {len(trades)} Trades (1 trade/day) | {len(wins)}W/{len(trades)-len(wins)}L ({wr:.1f}% WR) | Gross PnL: Rs.{tot_pnl:+,.2f}")
    fees = len(trades) * 18.0
    print(f"Total Fees: Rs.{fees:.2f} | NET PROFIT AFTER FEES: Rs.{tot_pnl-fees:+,.2f} ({(tot_pnl-fees)/10000.0*100:+.2f}%)")
    print("=" * 90)

if __name__ == '__main__':
    test_sniper_strategy()
