package strategy

import (
	"fmt"
	"sync"
	"time"

	"github.com/may-bach/Axiom/internal/models"
)

// Engine holds configuration and evaluates entry and exit signals.
type Engine struct {
	mu              sync.RWMutex
	stockStrategies map[string]models.StockStrategy

	defaultBuffer          float64
	defaultBounceRebound   float64
	defaultQuickDrop       float64
	defaultFixedSLPercent  float64
	defaultTargetPercent   float64
	defaultTrailingPercent float64
	defaultLeverage        float64
}

// NewEngine creates a new strategy engine with baseline defaults.
func NewEngine() *Engine {
	return &Engine{
		stockStrategies:        make(map[string]models.StockStrategy),
		defaultBuffer:          0.002,
		defaultBounceRebound:   0.008,
		defaultQuickDrop:       0.012,
		defaultFixedSLPercent:  1.0,
		defaultTargetPercent:   2.0,
		defaultTrailingPercent: 1.0,
		defaultLeverage:        1.0,
	}
}

func (e *Engine) SetStrategies(configs map[string]models.StockStrategy) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.stockStrategies = configs
}

func (e *Engine) GetStrategy(sym string) models.StockStrategy {
	e.mu.RLock()
	defer e.mu.RUnlock()

	if strat, ok := e.stockStrategies[sym]; ok {
		return strat
	}

	return models.StockStrategy{
		Class:         "B",
		AllowShort:    true,
		BreakoutLong:  e.defaultBuffer,
		BreakoutShort: e.defaultBuffer,
		Target:        e.defaultTargetPercent / 100,
		SL:            e.defaultFixedSLPercent / 100,
		Leverage:      e.defaultLeverage,
	}
}

// ----------------------------------------------------------------------
// Entry Signal Evaluations
// ----------------------------------------------------------------------

func (e *Engine) CheckBreakoutLong(sym string, ltp float64, hl models.HighLow, threshold float64) (bool, string) {
	if hl.High > 0 && ltp > hl.High*(1+threshold) {
		return true, fmt.Sprintf("BREAKOUT LONG BUY %s @ %.2f (threshold %.3f)", sym, ltp, threshold)
	}
	return false, ""
}

func (e *Engine) CheckBounceBuy(sym string, ltp float64, hl models.HighLow, hist []float64) (bool, string) {
	if len(hist) < 2 {
		return false, ""
	}
	prev := hist[len(hist)-2]
	if prev <= hl.Low*1.005 && ltp >= prev*(1+e.defaultBounceRebound) {
		return true, fmt.Sprintf("BOUNCE BACK BUY %s @ %.2f (prev %.2f, low %.2f)", sym, ltp, prev, hl.Low)
	}
	return false, ""
}

func (e *Engine) CheckBreakdownShort(sym string, ltp float64, hl models.HighLow, threshold float64) (bool, string) {
	if hl.Low > 0 && ltp < hl.Low*(1-threshold) {
		return true, fmt.Sprintf("BREAKDOWN SHORT SELL %s @ %.2f (threshold %.3f)", sym, ltp, threshold)
	}
	return false, ""
}

func (e *Engine) CheckQuickDropShort(sym string, ltp float64, hist []float64) (bool, string) {
	if len(hist) < 2 {
		return false, ""
	}
	prev := hist[len(hist)-2]
	drop := (prev - ltp) / prev
	if drop >= e.defaultQuickDrop {
		return true, fmt.Sprintf("QUICK DROP SHORT SELL %s @ %.2f (drop %.2f%%)", sym, ltp, drop*100)
	}
	return false, ""
}

// ----------------------------------------------------------------------
// Exit Signal Evaluations
// ----------------------------------------------------------------------

func (e *Engine) EvaluateLongExit(pos models.Position, strat models.StockStrategy, ltp float64, stagnationMin int) (bool, string) {
	// 1. Fixed Stop-Loss
	fixedSL := pos.EntryPrice * (1 - strat.SL)
	if ltp <= fixedSL {
		return true, fmt.Sprintf("Fixed SL %.1f%%", strat.SL*100)
	}

	// 2. Profit Target
	target := pos.EntryPrice * (1 + strat.Target)
	if ltp >= target {
		return true, fmt.Sprintf("Target %.1f%%", strat.Target*100)
	}

	// 3. Trailing Stop-Loss
	trailingSL := pos.HighestPrice * (1 - e.defaultTrailingPercent/100)
	if ltp <= trailingSL {
		return true, "Trailing SL"
	}

	// 4. Stagnation Timeout (flat chop after X minutes)
	if stagnationMin > 0 && time.Since(pos.EntryTime) >= time.Duration(stagnationMin)*time.Minute {
		pctChange := (ltp - pos.EntryPrice) / pos.EntryPrice
		if pctChange >= -0.004 && pctChange <= 0.004 {
			return true, fmt.Sprintf("Stagnation Timeout (%dm flat)", stagnationMin)
		}
	}

	return false, ""
}

func (e *Engine) EvaluateShortExit(pos models.Position, strat models.StockStrategy, ltp float64, stagnationMin int) (bool, string) {
	// 1. Fixed Stop-Loss
	fixedSL := pos.EntryPrice * (1 + strat.SL)
	if ltp >= fixedSL {
		return true, fmt.Sprintf("Fixed SL %.1f%%", strat.SL*100)
	}

	// 2. Profit Target
	target := pos.EntryPrice * (1 - strat.Target)
	if ltp <= target {
		return true, fmt.Sprintf("Target %.1f%%", strat.Target*100)
	}

	// 3. Trailing Stop-Loss
	trailingSL := pos.LowestPrice * (1 + e.defaultTrailingPercent/100)
	if ltp >= trailingSL {
		return true, "Trailing SL"
	}

	// 4. Stagnation Timeout (flat chop after X minutes)
	if stagnationMin > 0 && time.Since(pos.EntryTime) >= time.Duration(stagnationMin)*time.Minute {
		pctChange := (pos.EntryPrice - ltp) / pos.EntryPrice
		if pctChange >= -0.004 && pctChange <= 0.004 {
			return true, fmt.Sprintf("Stagnation Timeout (%dm flat)", stagnationMin)
		}
	}

	return false, ""
}
