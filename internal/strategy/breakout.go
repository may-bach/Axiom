package strategy

import (
	"fmt"
	"math"
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

// ResolveAllowedDirections evaluates macro directives against stock strategy settings
func (e *Engine) ResolveAllowedDirections(directive string, stratAllowShort bool) (allowLong bool, allowShort bool) {
	if directive == "BLOCKED" {
		return false, false
	}
	allowLong = (directive != "SHORT_ONLY")
	allowShort = stratAllowShort && (directive != "LONG_ONLY")
	return allowLong, allowShort
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

// CheckBaseBreakoutLong evaluates 09:35 Base Breakout + VWAP confirmation + RS Leader status
func (e *Engine) CheckBaseBreakoutLong(sym string, ltp, baseHigh, vwap float64, isRSLeader bool, threshold float64) (bool, string) {
	if !isRSLeader {
		return false, ""
	}
	if baseHigh > 0 && ltp >= baseHigh*(1+threshold) && ltp > vwap {
		return true, fmt.Sprintf("BASE BREAKOUT LONG BUY %s @ %.2f (base high %.2f, vwap %.2f, thresh %.3f)", sym, ltp, baseHigh, vwap, threshold)
	}
	return false, ""
}

// CheckBaseBreakdownShort evaluates 09:35 Base Breakdown + VWAP confirmation + RS Laggard status
func (e *Engine) CheckBaseBreakdownShort(sym string, ltp, baseLow, vwap float64, isRSLaggard bool, threshold float64) (bool, string) {
	if !isRSLaggard {
		return false, ""
	}
	if baseLow > 0 && ltp <= baseLow*(1-threshold) && ltp < vwap {
		return true, fmt.Sprintf("BASE BREAKDOWN SHORT SELL %s @ %.2f (base low %.2f, vwap %.2f, thresh %.3f)", sym, ltp, baseLow, vwap, threshold)
	}
	return false, ""
}

// CheckTrendAlignment validates market breadth across the active universe before entries.
// For LONG: requires advancePct >= 45.0% and avgReturn >= -0.15%.
// For SHORT: requires advancePct <= 55.0% and avgReturn <= +0.15%.
// If totalCount < 5, it gracefully permits the trade due to insufficient universe breadth data.
func (e *Engine) CheckTrendAlignment(dir string, advancePct, avgReturn float64, totalCount int) (bool, string) {
	if totalCount < 5 {
		return true, "insufficient breadth data; permitted by default"
	}

	if dir == "LONG" {
		if advancePct < 45.0 || avgReturn < -0.15 {
			return false, fmt.Sprintf("Blocked by Bearish Market Trend (Advance: %.1f%% < 45.0%%, Avg: %+.2f%% < -0.15%%)", advancePct, avgReturn)
		}
		return true, fmt.Sprintf("Market Trend Bullish (Advance: %.1f%%, Avg: %+.2f%%)", advancePct, avgReturn)
	}

	if dir == "SHORT" {
		if advancePct > 55.0 || avgReturn > 0.15 {
			return false, fmt.Sprintf("Blocked by Bullish Market Trend (Advance: %.1f%% > 55.0%%, Avg: %+.2f%% > +0.15%%)", advancePct, avgReturn)
		}
		return true, fmt.Sprintf("Market Trend Bearish (Advance: %.1f%%, Avg: %+.2f%%)", advancePct, avgReturn)
	}

	return false, "invalid direction"
}

// ----------------------------------------------------------------------
// Exit Signal Evaluations
// ----------------------------------------------------------------------

func (e *Engine) EvaluateLongExit(pos models.Position, strat models.StockStrategy, ltp float64, stagnationMin int) (bool, string) {
	pnlPct := (ltp - pos.EntryPrice) / pos.EntryPrice
	maxFav := (pos.HighestPrice - pos.EntryPrice) / pos.EntryPrice

	// 1. Profit Target
	if pnlPct >= strat.Target {
		return true, fmt.Sprintf("Target %.1f%%", strat.Target*100)
	}

	// 2. Fixed Stop-Loss (strictly capped at strat.SL)
	if pnlPct <= -strat.SL {
		return true, fmt.Sprintf("Fixed SL %.1f%%", strat.SL*100)
	}

	// 3. Breakeven Guard: if peak reached +0.5%, ratchet stop to breakeven (+0.05%)
	if maxFav >= 0.005 && pnlPct <= 0.0005 {
		return true, "Breakeven Guard"
	}

	// 4. Trailing Stop: if peak reached +0.8%, trail by 0.4% from peak
	if maxFav >= 0.008 && ((pos.HighestPrice-ltp)/pos.HighestPrice) >= 0.004 {
		return true, "Trailing SL"
	}

	// 5. Stagnation Timeout: flat chop after X minutes
	if stagnationMin > 0 && time.Since(pos.EntryTime) >= time.Duration(stagnationMin)*time.Minute {
		if math.Abs(pnlPct) <= 0.002 {
			return true, fmt.Sprintf("Stagnation Timeout (%dm flat)", stagnationMin)
		}
	}

	return false, ""
}

func (e *Engine) EvaluateShortExit(pos models.Position, strat models.StockStrategy, ltp float64, stagnationMin int) (bool, string) {
	pnlPct := (pos.EntryPrice - ltp) / pos.EntryPrice
	maxFav := (pos.EntryPrice - pos.LowestPrice) / pos.EntryPrice

	// 1. Profit Target
	if pnlPct >= strat.Target {
		return true, fmt.Sprintf("Target %.1f%%", strat.Target*100)
	}

	// 2. Fixed Stop-Loss
	if pnlPct <= -strat.SL {
		return true, fmt.Sprintf("Fixed SL %.1f%%", strat.SL*100)
	}

	// 3. Breakeven Guard: if peak reached +0.5%, ratchet stop to breakeven (+0.05%)
	if maxFav >= 0.005 && pnlPct <= 0.0005 {
		return true, "Breakeven Guard"
	}

	// 4. Trailing Stop: if trough reached +0.8%, trail by 0.4% from trough
	if maxFav >= 0.008 && ((ltp-pos.LowestPrice)/pos.LowestPrice) >= 0.004 {
		return true, "Trailing SL"
	}

	// 5. Stagnation Timeout: flat chop after X minutes
	if stagnationMin > 0 && time.Since(pos.EntryTime) >= time.Duration(stagnationMin)*time.Minute {
		if math.Abs(pnlPct) <= 0.002 {
			return true, fmt.Sprintf("Stagnation Timeout (%dm flat)", stagnationMin)
		}
	}

	return false, ""
}
