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

func TestStoreAccountCompounding(t *testing.T) {
	store := NewStore()

	// Initial baseline
	acc := store.GetAccount()
	if acc.InitialCapital != 10000.0 || acc.CurrentBalance != 10000.0 {
		t.Fatalf("expected 10000 baseline, got %+v", acc)
	}

	lossFloor := store.GetDailyLossLimit()
	if lossFloor != -750.0 {
		t.Fatalf("expected -750 loss floor, got %.2f", lossFloor)
	}

	// Normal regime budget test (1.5x balance per trade at lev 1.0)
	b1 := store.GetPositionBudget(1.0)
	if b1 != 15000.0 {
		t.Fatalf("expected 15000 budget, got %.2f", b1)
	}
	b15 := store.GetPositionBudget(1.5)
	if b15 != 22500.0 {
		t.Fatalf("expected 22500 budget, got %.2f", b15)
	}

	// Day 1: realize +500 PnL
	updatedAcc, applied := store.UpdateAccountDaily(500.0, "2026-09-19", "2026-09-19 15:30:00 IST")
	if !applied {
		t.Fatalf("expected daily update to be applied")
	}
	if updatedAcc.CurrentBalance != 10500.0 || updatedAcc.TotalRealizedPnL != 500.0 {
		t.Fatalf("unexpected balance after Day 1: %+v", updatedAcc)
	}

	// Idempotency: same date should not compound twice
	_, applied2 := store.UpdateAccountDaily(500.0, "2026-09-19", "2026-09-19 15:35:00 IST")
	if applied2 {
		t.Fatalf("expected second update on same date to be ignored")
	}
	if store.GetAccount().CurrentBalance != 10500.0 {
		t.Fatalf("balance should remain 10500.0")
	}

	// Dynamic loss floor on compounded capital
	newLossFloor := store.GetDailyLossLimit()
	expectedFloor := -0.075 * 10500.0 // -787.5
	if newLossFloor != expectedFloor {
		t.Fatalf("expected loss floor %.2f, got %.2f", expectedFloor, newLossFloor)
	}

	// Day 2 sizing should scale up
	b2 := store.GetPositionBudget(1.0)
	if b2 != 10500.0*1.5 {
		t.Fatalf("expected Day 2 budget %.2f, got %.2f", 10500.0*1.5, b2)
	}
}

func TestStockDirectives(t *testing.T) {
	store := NewStore()

	// Default without directives should return NEUTRAL
	if dir := store.GetStockDirective("HAL"); dir != "NEUTRAL" {
		t.Fatalf("expected default NEUTRAL, got %s", dir)
	}

	// Set regime with directives
	store.SetRegime(models.MarketRegime{
		Status:     "CAUTION",
		MacroTheme: "CRUDE_OIL_SHOCK",
		StockDirectives: map[string]string{
			"HAL":      "LONG_ONLY",
			"TVSMOTOR": "SHORT_ONLY",
			"TITAGARH": "BLOCKED",
		},
	})

	if dir := store.GetStockDirective("HAL"); dir != "LONG_ONLY" {
		t.Fatalf("expected LONG_ONLY for HAL, got %s", dir)
	}
	if dir := store.GetStockDirective("TVSMOTOR"); dir != "SHORT_ONLY" {
		t.Fatalf("expected SHORT_ONLY for TVSMOTOR, got %s", dir)
	}
	if dir := store.GetStockDirective("TITAGARH"); dir != "BLOCKED" {
		t.Fatalf("expected BLOCKED for TITAGARH, got %s", dir)
	}
	// Unmentioned symbol in directives map should return NEUTRAL
	if dir := store.GetStockDirective("RELIANCE"); dir != "NEUTRAL" {
		t.Fatalf("expected NEUTRAL for unmentioned RELIANCE, got %s", dir)
	}
}
