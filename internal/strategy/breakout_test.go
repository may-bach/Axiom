package strategy

import (
	"testing"
	"time"

	"github.com/may-bach/Axiom/internal/models"
)

func TestEvaluateLongExit(t *testing.T) {
	eng := NewEngine()
	strat := models.StockStrategy{
		Target: 0.02, // 2%
		SL:     0.01, // 1%
	}

	pos := models.Position{
		Symbol:       "TVSMOTOR",
		Direction:    "LONG",
		EntryPrice:   100.0,
		HighestPrice: 100.0,
		Qty:          10,
		EntryTime:    time.Now().Add(-5 * time.Minute),
	}

	// 1. Fixed SL test: price drops to 99.0
	shouldExit, reason := eng.EvaluateLongExit(pos, strat, 98.9, 45)
	if !shouldExit || reason != "Fixed SL 1.0%" {
		t.Fatalf("expected Fixed SL exit, got %v (%s)", shouldExit, reason)
	}

	// 2. Profit Target test: price rises to 102.1
	shouldExit, reason = eng.EvaluateLongExit(pos, strat, 102.1, 45)
	if !shouldExit || reason != "Target 2.0%" {
		t.Fatalf("expected Target exit, got %v (%s)", shouldExit, reason)
	}

	// 3. Trailing SL test: highest was 101.5, price drops to 100.4 (more than 1% from 101.5 = 100.485)
	pos.HighestPrice = 101.5
	shouldExit, reason = eng.EvaluateLongExit(pos, strat, 100.4, 45)
	if !shouldExit || reason != "Trailing SL" {
		t.Fatalf("expected Trailing SL exit, got %v (%s)", shouldExit, reason)
	}

	// 4. Stagnation Timeout test: open for 50 minutes, price flat at 100.1
	pos.EntryTime = time.Now().Add(-50 * time.Minute)
	pos.HighestPrice = 100.2
	shouldExit, reason = eng.EvaluateLongExit(pos, strat, 100.1, 45)
	if !shouldExit || reason != "Stagnation Timeout (45m flat)" {
		t.Fatalf("expected Stagnation Timeout exit, got %v (%s)", shouldExit, reason)
	}

	// 5. Normal hold: price 100.5, 10 mins old
	pos.EntryTime = time.Now().Add(-10 * time.Minute)
	shouldExit, reason = eng.EvaluateLongExit(pos, strat, 100.5, 45)
	if shouldExit {
		t.Fatalf("expected no exit, but got %v (%s)", shouldExit, reason)
	}
}

func TestBreakoutSignals(t *testing.T) {
	eng := NewEngine()
	hl := models.HighLow{High: 200.0, Low: 190.0}

	// Long breakout test: threshold 0.003 -> 200.6
	shouldEnter, _ := eng.CheckBreakoutLong("HAL", 200.8, hl, 0.003)
	if !shouldEnter {
		t.Fatalf("expected breakout long entry at 200.8")
	}

	shouldEnter, _ = eng.CheckBreakoutLong("HAL", 200.2, hl, 0.003)
	if shouldEnter {
		t.Fatalf("expected no breakout long at 200.2")
	}

	// Short breakdown test: threshold 0.003 -> 189.43
	shouldEnter, _ = eng.CheckBreakdownShort("HAL", 189.0, hl, 0.003)
	if !shouldEnter {
		t.Fatalf("expected breakdown short entry at 189.0")
	}
}

func TestResolveAllowedDirections(t *testing.T) {
	eng := NewEngine()

	// NEUTRAL with AllowShort = true
	long, short := eng.ResolveAllowedDirections("NEUTRAL", true)
	if !long || !short {
		t.Fatalf("expected both long and short allowed for NEUTRAL, got long=%v, short=%v", long, short)
	}

	// NEUTRAL with AllowShort = false (e.g. Class C stocks)
	long, short = eng.ResolveAllowedDirections("NEUTRAL", false)
	if !long || short {
		t.Fatalf("expected only long allowed when strat.AllowShort is false, got long=%v, short=%v", long, short)
	}

	// LONG_ONLY
	long, short = eng.ResolveAllowedDirections("LONG_ONLY", true)
	if !long || short {
		t.Fatalf("expected only long allowed for LONG_ONLY, got long=%v, short=%v", long, short)
	}

	// SHORT_ONLY
	long, short = eng.ResolveAllowedDirections("SHORT_ONLY", true)
	if long || !short {
		t.Fatalf("expected only short allowed for SHORT_ONLY, got long=%v, short=%v", long, short)
	}

	// SHORT_ONLY with AllowShort = false -> neither
	long, short = eng.ResolveAllowedDirections("SHORT_ONLY", false)
	if long || short {
		t.Fatalf("expected neither allowed when SHORT_ONLY and strat.AllowShort is false, got long=%v, short=%v", long, short)
	}

	// BLOCKED
	long, short = eng.ResolveAllowedDirections("BLOCKED", true)
	if long || short {
		t.Fatalf("expected neither allowed for BLOCKED, got long=%v, short=%v", long, short)
	}
}

func TestSmartAlgoExits(t *testing.T) {
	eng := NewEngine()
	strat := models.StockStrategy{
		Target: 0.02, // 2%
		SL:     0.01, // 1%
	}

	// 1. Long Breakeven Guard: Entry 1000, Peak reached 1006 (+0.6%), current price drops to 1000.4 (+0.04%)
	posLong := models.Position{
		Symbol:       "JINDALSTEL",
		Direction:    "LONG",
		EntryPrice:   1000.0,
		HighestPrice: 1006.0,
		Qty:          10,
		EntryTime:    time.Now().Add(-10 * time.Minute),
	}
	shouldExit, reason := eng.EvaluateLongExit(posLong, strat, 1000.4, 30)
	if !shouldExit || reason != "Breakeven Guard" {
		t.Fatalf("expected Breakeven Guard exit for long, got %v (%s)", shouldExit, reason)
	}

	// 2. Long Trailing SL: Entry 1000, Peak reached 1012 (+1.2%), price drops to 1007.5 (pullback 0.44% > 0.4%)
	posLong.HighestPrice = 1012.0
	shouldExit, reason = eng.EvaluateLongExit(posLong, strat, 1007.5, 30)
	if !shouldExit || reason != "Trailing SL" {
		t.Fatalf("expected Trailing SL exit for long, got %v (%s)", shouldExit, reason)
	}

	// 3. Short Breakeven Guard: Entry 1000, Lowest reached 994 (+0.6% profit), current price rises to 999.8 (+0.02%)
	posShort := models.Position{
		Symbol:       "TRENT",
		Direction:    "SHORT",
		EntryPrice:   1000.0,
		LowestPrice:  994.0,
		Qty:          10,
		EntryTime:    time.Now().Add(-10 * time.Minute),
	}
	shouldExit, reason = eng.EvaluateShortExit(posShort, strat, 999.8, 30)
	if !shouldExit || reason != "Breakeven Guard" {
		t.Fatalf("expected Breakeven Guard exit for short, got %v (%s)", shouldExit, reason)
	}

	// 4. Short Trailing SL: Entry 1000, Lowest reached 988 (+1.2% profit), price bounces to 993 (rebound 0.5% > 0.4%)
	posShort.LowestPrice = 988.0
	shouldExit, reason = eng.EvaluateShortExit(posShort, strat, 993.0, 30)
	if !shouldExit || reason != "Trailing SL" {
		t.Fatalf("expected Trailing SL exit for short, got %v (%s)", shouldExit, reason)
	}
}

func TestBaseBreakouts(t *testing.T) {
	eng := NewEngine()

	// Long Base Breakout: baseHigh 1160.0, buffer 0.003 -> threshold level 1163.48
	// Case A: RS Leader, above base, above VWAP (1162.0) -> Valid entry
	valid, _ := eng.CheckBaseBreakoutLong("JINDALSTEL", 1165.0, 1160.0, 1162.0, true, 0.003)
	if !valid {
		t.Fatalf("expected valid long breakout at 1165.0")
	}

	// Case B: Not an RS Leader -> Reject
	valid, _ = eng.CheckBaseBreakoutLong("JINDALSTEL", 1165.0, 1160.0, 1162.0, false, 0.003)
	if valid {
		t.Fatalf("expected rejection when not RS leader")
	}

	// Case C: Below VWAP (LTP 1165.0, VWAP 1166.0) -> Reject
	valid, _ = eng.CheckBaseBreakoutLong("JINDALSTEL", 1165.0, 1160.0, 1166.0, true, 0.003)
	if valid {
		t.Fatalf("expected rejection when LTP < VWAP")
	}

	// Short Base Breakdown: baseLow 1000.0, buffer 0.003 -> threshold level 997.0
	// Case A: RS Laggard, below base, below VWAP (998.0) -> Valid entry
	valid, _ = eng.CheckBaseBreakdownShort("VOLTAS", 995.0, 1000.0, 998.0, true, 0.003)
	if !valid {
		t.Fatalf("expected valid short breakdown at 995.0")
	}

	// Case B: Not an RS Laggard -> Reject
	valid, _ = eng.CheckBaseBreakdownShort("VOLTAS", 995.0, 1000.0, 998.0, false, 0.003)
	if valid {
		t.Fatalf("expected rejection when not RS laggard")
	}

	// Case C: Above VWAP (LTP 995.0, VWAP 994.0) -> Reject
	valid, _ = eng.CheckBaseBreakdownShort("VOLTAS", 995.0, 1000.0, 994.0, true, 0.003)
	if valid {
		t.Fatalf("expected rejection when LTP > VWAP")
	}
}

func TestCheckTrendAlignment(t *testing.T) {
	eng := NewEngine()

	// 1. Insufficient universe breadth data (<5) -> allowed by default
	ok, _ := eng.CheckTrendAlignment("LONG", 10.0, -2.0, 3)
	if !ok {
		t.Fatalf("expected fallback permission when count < 5")
	}

	// 2. LONG in Bullish Market: 65% advancing, +0.45% avg return -> OK
	ok, _ = eng.CheckTrendAlignment("LONG", 65.0, 0.45, 50)
	if !ok {
		t.Fatalf("expected LONG allowed in bullish market")
	}

	// 3. LONG in Crash/Selloff: 20% advancing, -1.2% avg return -> Blocked
	ok, reason := eng.CheckTrendAlignment("LONG", 20.0, -1.2, 50)
	if ok {
		t.Fatalf("expected LONG blocked during market crash")
	}
	if reason == "" {
		t.Fatalf("expected non-empty rejection reason")
	}

	// 4. LONG with low advance % (40% < 45%) -> Blocked
	ok, _ = eng.CheckTrendAlignment("LONG", 40.0, 0.05, 50)
	if ok {
		t.Fatalf("expected LONG blocked when advance %% < 45%%")
	}

	// 5. LONG with negative avg return (-0.25% < -0.15%) -> Blocked
	ok, _ = eng.CheckTrendAlignment("LONG", 50.0, -0.25, 50)
	if ok {
		t.Fatalf("expected LONG blocked when avg return < -0.15%%")
	}

	// 6. SHORT in Bearish Market: 30% advancing, -0.60% avg return -> OK
	ok, _ = eng.CheckTrendAlignment("SHORT", 30.0, -0.60, 50)
	if !ok {
		t.Fatalf("expected SHORT allowed in bearish market")
	}

	// 7. SHORT in Bullish Rally: 75% advancing, +0.80% avg return -> Blocked
	ok, _ = eng.CheckTrendAlignment("SHORT", 75.0, 0.80, 50)
	if ok {
		t.Fatalf("expected SHORT blocked during market rally")
	}

	// 8. SHORT with high advance % (60% > 55%) -> Blocked
	ok, _ = eng.CheckTrendAlignment("SHORT", 60.0, -0.05, 50)
	if ok {
		t.Fatalf("expected SHORT blocked when advance %% > 55%%")
	}

	// 9. SHORT with positive avg return (+0.25% > +0.15%) -> Blocked
	ok, _ = eng.CheckTrendAlignment("SHORT", 50.0, 0.25, 50)
	if ok {
		t.Fatalf("expected SHORT blocked when avg return > +0.15%%")
	}
}
