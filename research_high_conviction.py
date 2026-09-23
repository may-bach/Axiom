import json
from pathlib import Path
import numpy as np

data = json.loads(Path('data/intraday_15d_cache.json').read_text())
sector_matrix = json.loads(Path('data/sector_matrix.json').read_text())

dates = sorted(list(set(b['date'] for bars in data.values() for b in bars)))

# Let's inspect all days and see the true maximum intraday run from 09:35 to Day High / Low
print(f"{'DATE':<12} | {'TOP RUNNERS (>1.5% from 09:35)':<55} | {'TOP DUMPERS (<-1.5% from 09:35)'}")
print("-" * 110)

all_big_movers = []

for d in dates:
    day_runners = []
    day_dumpers = []
    for s, bars in data.items():
        day_bars = [b for b in bars if b['date'] == d]
        if not day_bars: continue
        
        base_bars = [b for b in day_bars if b['time_str'] <= '09:35']
        post_bars = [b for b in day_bars if b['time_str'] > '09:35']
        if not base_bars or not post_bars: continue
        
        p_0935 = base_bars[-1]['close']
        max_high = max(b['high'] for b in post_bars)
        min_low = min(b['low'] for b in post_bars)
        
        max_run_up = (max_high - p_0935) / p_0935 * 100
        max_run_down = (min_low - p_0935) / p_0935 * 100
        
        sec = sector_matrix.get(s, {}).get('sector', 'OTHER')
        
        if max_run_up >= 1.5:
            # find time of max high
            t_high = next(b['time_str'] for b in post_bars if b['high'] == max_high)
            day_runners.append((s, sec, max_run_up, t_high))
            all_big_movers.append(('LONG', d, s, sec, max_run_up, t_high, base_bars, post_bars))
        if max_run_down <= -1.5:
            t_low = next(b['time_str'] for b in post_bars if b['low'] == min_low)
            day_dumpers.append((s, sec, max_run_down, t_low))
            all_big_movers.append(('SHORT', d, s, sec, max_run_down, t_low, base_bars, post_bars))
            
    day_runners.sort(key=lambda x: x[2], reverse=True)
    day_dumpers.sort(key=lambda x: x[2])
    
    r_str = ", ".join([f"{s}(+{r:.1f}%)" for s, sec, r, t in day_runners[:4]])
    d_str = ", ".join([f"{s}({r:.1f}%)" for s, sec, r, t in day_dumpers[:4]])
    print(f"{d:<12} | {r_str:<55} | {d_str}")

print(f"\nTotal Big Mover Opportunities (>1.5% move from 09:35): {len(all_big_movers)}")
