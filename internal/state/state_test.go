package state

import (
	"sync"
	"testing"
	"time"

	"github.com/may-bach/Axiom/internal/models"
)

func TestStoreConcurrency(t *testing.T) {
	store := NewStore()
	var wg sync.WaitGroup

	// Test concurrent updates to high/low, history, positions, and trades
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			sym := "TEST_SYM"
			price := 100.0 + float64(idx)

			store.UpdateHighLow(sym, price)
			store.AppendHistory(sym, price, 5)
			_ = store.GetHistory(sym)

			store.OpenLong(sym, price, 10, time.Now())
			store.UpdateLongHighest(sym, price+5)
			pos, ok := store.CloseLong(sym)
			if ok {
				store.RecordTrade(models.TradeRecord{
					Symbol:     sym,
					Direction:  "LONG",
					EntryTime:  pos.EntryTime,
					EntryPrice: pos.EntryPrice,
					ExitTime:   time.Now(),
					ExitPrice:  price + 5,
					Qty:        pos.Qty,
					PnL:        float64(pos.Qty) * 5.0,
					Reason:     "Test",
				})
			}
		}(i)
	}

	wg.Wait()

	if store.GetOpenCount() != 0 {
		t.Fatalf("expected 0 open positions, got %d", store.GetOpenCount())
	}

	hl, exists := store.GetHighLow("TEST_SYM")
	if !exists {
		t.Fatalf("expected high/low to exist for TEST_SYM")
	}
	if hl.High <= 0 || hl.Low <= 0 {
		t.Fatalf("invalid high/low values: %+v", hl)
	}
}

func TestStoreAtomicClose(t *testing.T) {
	store := NewStore()
	store.OpenLong("INFY", 1500.0, 10, time.Now())

	// First close should succeed
	pos, ok := store.CloseLong("INFY")
	if !ok || pos.EntryPrice != 1500.0 {
		t.Fatalf("failed to close long position: %+v", pos)
	}

	// Second close should return false
	_, ok = store.CloseLong("INFY")
	if ok {
		t.Fatalf("expected second close to fail, but succeeded")
	}
}
