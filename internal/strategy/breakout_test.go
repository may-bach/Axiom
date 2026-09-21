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
