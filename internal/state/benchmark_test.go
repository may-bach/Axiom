package state

import (
	"fmt"
	"testing"
)

func BenchmarkMarketBreadth(b *testing.B) {
	store := NewStore()
	for i := 0; i < 100; i++ {
		sym := fmt.Sprintf("SYM%03d", i)
		openP := 1000.0 + float64(i)*10.0
		store.SetBaseRange(sym, openP, openP*1.02, openP*0.98, 0.5)
		store.UpdateLTP(sym, openP*1.01)
	}

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		store.GetMarketBreadth()
	}
}

func BenchmarkAppendHistory(b *testing.B) {
	store := NewStore()
	sym := "RELIANCE"

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		store.AppendHistory(sym, 2500.0+float64(i%10), 10)
	}
}

func BenchmarkCheckExitsLocking(b *testing.B) {
	store := NewStore()
	sym := "TCS"
	ltp := 3500.0

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		store.UpdateLongHighest(sym, ltp)
		store.UpdateShortLowest(sym, ltp)
	}
}

func BenchmarkGetPositionBudget(b *testing.B) {
	store := NewStore()

	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		store.GetPositionBudget(1.0)
	}
}
