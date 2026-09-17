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
	EntryPrice   float64   `json:"entry_price"`
	HighestPrice float64   `json:"highest_price"`
	LowestPrice  float64   `json:"lowest_price"`
	Qty          int       `json:"qty"`
	EntryTime    time.Time `json:"entry_time"`
}
