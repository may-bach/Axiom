package models

import "time"

// StockStrategy defines the trading parameters for a stock
type StockStrategy struct {
	Class         string  `json:"class"`
	AllowShort    bool    `json:"allow_short"`
	BreakoutLong  float64 `json:"breakout_long"`
	BreakoutShort float64 `json:"breakout_short"`
	Target        float64 `json:"target"`
	SL            float64 `json:"sl"`
	Leverage      float64 `json:"leverage"`
}

// TradeRecord holds executed trade details for journaling and P&L
type TradeRecord struct {
	Symbol     string    `json:"symbol"`
	Direction  string    `json:"direction"` // LONG / SHORT
	EntryTime  time.Time `json:"entry_time"`
	EntryPrice float64   `json:"entry_price"`
	ExitTime   time.Time `json:"exit_time"`
	ExitPrice  float64   `json:"exit_price"`
	Qty        int       `json:"qty"`
	PnL        float64   `json:"pnl"`
	Reason     string    `json:"reason"`
}

// Position tracks an active open trade
type Position struct {
	Symbol       string    `json:"symbol"`
	Direction    string    `json:"direction"` // LONG / SHORT
	EntryPrice   float64   `json:"entry_price"`
	HighestPrice float64   `json:"highest_price"`
	LowestPrice  float64   `json:"lowest_price"`
	Qty          int       `json:"qty"`
	EntryTime    time.Time `json:"entry_time"`
}

// HighLow tracks daily established price boundaries
type HighLow struct {
	High float64 `json:"high"`
	Low  float64 `json:"low"`
}

// MarketRegime represents macro sentiment and volatility classification
type MarketRegime struct {
	Date              string   `json:"date"`
	Timestamp         string   `json:"timestamp"`
	RiskScore         int      `json:"risk_score"`
	Status            string   `json:"status"` // NORMAL / CAUTION / CRISIS
	Color             string   `json:"color"`  // GREEN / YELLOW / RED
	MaxPositions      int      `json:"max_positions"`
	PositionBudget    float64  `json:"position_budget"`
	StagnationMinutes int      `json:"stagnation_minutes"`
	DailyLossLimit    float64  `json:"daily_loss_limit"`
	Reason            string   `json:"reason"`
	FlaggedHeadlines  []string `json:"flagged_headlines"`
	IndiaVIX          float64  `json:"india_vix"`
	VIXChangePct      float64  `json:"vix_change_pct"`
	NiftyLTP          float64  `json:"nifty_ltp"`
}

// AccountState tracks capital balance, compounding, and drawdown across days
type AccountState struct {
	InitialCapital     float64 `json:"initial_capital"`
	CurrentBalance     float64 `json:"current_balance"`
	PeakBalance        float64 `json:"peak_balance"`
	TotalRealizedPnL   float64 `json:"total_realized_pnl"`
	LastUpdated        string  `json:"last_updated"`
	LastCompoundedDate string  `json:"last_compounded_date"`
}
