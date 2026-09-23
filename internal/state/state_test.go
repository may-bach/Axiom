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

func TestSmartAlgoState(t *testing.T) {
	store := NewStore()

	// Base range initially not established
	if store.IsBaseEstablished() {
		t.Fatalf("expected base not established initially")
	}

	store.SetBaseRange("JINDALSTEL", 1150.0, 1170.0, 1145.0, 1.74)
	br, ok := store.GetBaseRange("JINDALSTEL")
	if !ok || br.High != 1170.0 || br.OpenPrice != 1150.0 {
		t.Fatalf("failed to retrieve base range: %+v", br)
	}

	store.SetBaseEstablished(true)
	if !store.IsBaseEstablished() {
		t.Fatalf("expected base to be established")
	}

	// RS ranks
	leaders := map[string]bool{"JINDALSTEL": true, "TATASTEEL": true}
	laggards := map[string]bool{"TRENT": true, "VOLTAS": true}
	store.SetRSRanks(leaders, laggards)

	if !store.IsRSLeader("JINDALSTEL") || store.IsRSLeader("TRENT") {
		t.Fatalf("RS Leader check failed")
	}
	if !store.IsRSLaggard("TRENT") || store.IsRSLaggard("JINDALSTEL") {
		t.Fatalf("RS Laggard check failed")
	}

	// VWAP
	store.SetVWAP("JINDALSTEL", 1160.5)
	if store.GetVWAP("JINDALSTEL") != 1160.5 {
		t.Fatalf("expected VWAP 1160.5, got %.2f", store.GetVWAP("JINDALSTEL"))
	}

	// Daily trade lock
	if store.HasSymbolTradedToday("JINDALSTEL") {
		t.Fatalf("expected symbol not traded initially")
	}
	store.MarkSymbolTradedToday("JINDALSTEL")
	if !store.HasSymbolTradedToday("JINDALSTEL") {
		t.Fatalf("expected symbol to be marked as traded")
	}

	// ResetDaily should wipe base, RS, VWAP, and tradedToday
	store.ResetDaily(time.Now())
	if store.IsBaseEstablished() {
		t.Fatalf("expected baseEstablished to be false after daily reset")
	}
	if store.HasSymbolTradedToday("JINDALSTEL") {
		t.Fatalf("expected tradedToday to be cleared after daily reset")
	}
	if store.IsRSLeader("JINDALSTEL") {
		t.Fatalf("expected RS leaders to be cleared after daily reset")
	}
}

func TestCalmStandDownRegime(t *testing.T) {
	store := NewStore()

	// CALM_STAND_DOWN should return 0 budget
	store.SetRegime(models.MarketRegime{
		Status:         "CALM_STAND_DOWN",
		Color:          "GRAY",
		MaxPositions:   0,
		PositionBudget: 0.0,
	})

	b := store.GetPositionBudget(1.0)
	if b != 0.0 {
		t.Fatalf("expected 0.0 budget in CALM_STAND_DOWN, got %.2f", b)
	}

	reg := store.GetRegime()
	if reg.Status != "CALM_STAND_DOWN" {
		t.Fatalf("expected CALM_STAND_DOWN status, got %s", reg.Status)
	}
}

func TestMarketBreadth(t *testing.T) {
	store := NewStore()

	// Initial empty state
	advPct, avgRet, count := store.GetMarketBreadth()
	if advPct != 50.0 || count != 0 {
		t.Fatalf("expected default 50%% and 0 count on empty store, got %.1f%%, %d", advPct, count)
	}

	// Setup 4 stocks in base range
	store.SetBaseRange("STOCK_A", 100.0, 105.0, 99.0, 0.0)
	store.SetBaseRange("STOCK_B", 200.0, 205.0, 198.0, 0.0)
	store.SetBaseRange("STOCK_C", 100.0, 102.0, 96.0, 0.0)
	store.SetBaseRange("STOCK_D", 50.0, 52.0, 48.0, 0.0)

	store.UpdateLTP("STOCK_A", 102.0) // +2.0%
	store.UpdateLTP("STOCK_B", 204.0) // +2.0%
	store.UpdateLTP("STOCK_C", 97.0)  // -3.0%
	store.UpdateLTP("STOCK_D", 49.0)  // -2.0%

	advPct, avgRet, count = store.GetMarketBreadth()
	if count != 4 {
		t.Fatalf("expected 4 stocks, got %d", count)
	}
	if advPct != 50.0 {
		t.Fatalf("expected 50%% advance, got %.1f%%", advPct)
	}
	expectedAvg := (2.0 + 2.0 - 3.0 - 2.0) / 4.0 // -0.25%
	if avgRet < expectedAvg-0.01 || avgRet > expectedAvg+0.01 {
		t.Fatalf("expected avg return %.2f%%, got %.2f%%", expectedAvg, avgRet)
	}

	// Now Stock D rallies: 49 -> 51 (+2.0%)
	store.UpdateLTP("STOCK_D", 51.0)
	advPct, avgRet, count = store.GetMarketBreadth()
	if advPct != 75.0 {
		t.Fatalf("expected 75%% advance (3/4), got %.1f%%", advPct)
	}
	expectedAvg = (2.0 + 2.0 - 3.0 + 2.0) / 4.0 // +0.75%
	if avgRet < expectedAvg-0.01 || avgRet > expectedAvg+0.01 {
		t.Fatalf("expected avg return %.2f%%, got %.2f%%", expectedAvg, avgRet)
	}

	// ResetDaily clears latestLTP and baseRanges
	store.ResetDaily(time.Now())
	_, _, countAfter := store.GetMarketBreadth()
	if countAfter != 0 {
		t.Fatalf("expected count 0 after daily reset, got %d", countAfter)
	}
}

