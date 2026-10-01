package strategy

import (
	"testing"
	"time"

	"github.com/may-bach/Axiom/internal/models"
)

func BenchmarkEvaluateLongExit(b *testing.B) {
	eng := NewEngine()
	strat := models.StockStrategy{
		Target: 0.02,
		SL:     0.01,
	}

	pos := models.Position{
		Symbol:       "TVSMOTOR",
		Direction:    "LONG",
		EntryPrice:   100.0,
		HighestPrice: 100.5,
		Qty:          10,
		EntryTime:    time.Now().Add(-10 * time.Minute),
	}

	ltp := 100.3
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		eng.EvaluateLongExit(pos, strat, ltp, 45)
	}
}

func BenchmarkCheckBaseBreakoutLong(b *testing.B) {
	eng := NewEngine()
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		eng.CheckBaseBreakoutLong("TCS", 3500.0, 3490.0, 3480.0, true, 0.002)
	}
}

func BenchmarkCheckTrendAlignment(b *testing.B) {
	eng := NewEngine()
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		eng.CheckTrendAlignment("LONG", 65.0, 0.35, 50)
	}
}
