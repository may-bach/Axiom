package strategy

import "github.com/may-bach/Axiom/internal/models"

// StrategyEngine holds configuration for breakout and mean reversion evaluation
type StrategyEngine struct {
	StockStrategies map[string]models.StockStrategy
}

// NewStrategyEngine creates a new strategy engine
func NewStrategyEngine() *StrategyEngine {
	return &StrategyEngine{
		StockStrategies: make(map[string]models.StockStrategy),
	}
}
