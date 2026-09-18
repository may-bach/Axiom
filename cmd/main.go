package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/may-bach/Axiom/internal/auth"
	"github.com/may-bach/Axiom/internal/client"
	"github.com/may-bach/Axiom/internal/config"
	"github.com/may-bach/Axiom/internal/models"
	"github.com/may-bach/Axiom/internal/session"
	"github.com/may-bach/Axiom/internal/state"
	"github.com/may-bach/Axiom/internal/stocks"
	"github.com/may-bach/Axiom/internal/strategy"
)

var (
	symbolToToken map[string]string
	store         *state.Store
	engine        *strategy.Engine

	defaultBudget       = 15000.0 // Scaled for ₹10k capital + 5x margin
	defaultMaxPositions = 3       // Normal regime allows up to 3 positions
	historyWindow       = 3

	paperTrading = true // Set to false for LIVE trading
)

func init() {
	logDir := "logs"
	os.MkdirAll(logDir, 0755)
	store = state.NewStore()
	engine = strategy.NewEngine()
}

func logTrade(msg string) {
	timestamp := time.Now().Format("2006-01-02 15:04:05")
	line := fmt.Sprintf("[%s] %s\n", timestamp, msg)
	fmt.Print(line)
}

func appendToLedgerCSV(trade models.TradeRecord) {
	ledgerPath := filepath.Join("data", "trades_ledger.csv")
	os.MkdirAll("data", 0755)

	needsHeader := false
	if _, err := os.Stat(ledgerPath); os.IsNotExist(err) {
		needsHeader = true
	}

	f, err := os.OpenFile(ledgerPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer f.Close()

	if needsHeader {
		f.WriteString("Date,EntryTime,ExitTime,Symbol,Direction,Qty,EntryPrice,ExitPrice,PnL,ReturnPct,Reason\n")
	}

	retPct := 0.0
	if trade.EntryPrice > 0 {
		if trade.Direction == "LONG" {
			retPct = ((trade.ExitPrice - trade.EntryPrice) / trade.EntryPrice) * 100
		} else {
			retPct = ((trade.EntryPrice - trade.ExitPrice) / trade.EntryPrice) * 100
		}
	}

	cleanReason := strings.ReplaceAll(trade.Reason, ",", ";")
	line := fmt.Sprintf("%s,%s,%s,%s,%s,%d,%.2f,%.2f,%.2f,%.2f%%,%s\n",
		trade.ExitTime.Format("2006-01-02"),
		trade.EntryTime.Format("15:04:05"),
		trade.ExitTime.Format("15:04:05"),
		trade.Symbol,
		trade.Direction,
		trade.Qty,
		trade.EntryPrice,
		trade.ExitPrice,
		trade.PnL,
		retPct,
		cleanReason,
	)
	f.WriteString(line)
}

// Order placement wrapper with live margin check
func placeOrder(sym, token, side, orderType string, qty int, requiredMargin float64) error {
	if paperTrading {
		logTrade(fmt.Sprintf("PAPER %s %s Qty:%d %s (token:%s)", side, orderType, qty, sym, token))
		return nil
	}

	// Live mode: verify real margin with broker before placing order
	hasMargin, available, err := client.CheckAvailableMargin(requiredMargin)
	if err != nil {
		logTrade(fmt.Sprintf("MARGIN CHECK FAILED %s: %v", sym, err))
		return err
	}
	if !hasMargin {
		errMsg := fmt.Sprintf("INSUFFICIENT MARGIN for %s: Required ₹%.2f, Available ₹%.2f", sym, requiredMargin, available)
		logTrade(errMsg)
		return fmt.Errorf("%s", errMsg)
	}

	return client.PlaceOrder(sym, token, side, orderType, qty)
}

// ----------------------------------------------------------------------
// Entry Handlers
// ----------------------------------------------------------------------

func enterLong(sym, token string, ltp float64, leverage float64) {
	regime := store.GetRegime()
	activeBudget := regime.PositionBudget
	if activeBudget <= 0 {
		activeBudget = defaultBudget
	}

	effectiveBudget := activeBudget * leverage
	qty := int(effectiveBudget / ltp)
	if qty < 1 {
		logTrade(fmt.Sprintf("LONG skipped - insufficient budget %s (lev %.1f)", sym, leverage))
		return
	}

	err := placeOrder(sym, token, "BUY", "MKT", qty, effectiveBudget)
	if err != nil {
		logTrade(fmt.Sprintf("LONG ENTRY FAILED %s: %v", sym, err))
		return
	}

	store.OpenLong(sym, ltp, qty, time.Now())
	logTrade(fmt.Sprintf("ENTRY LONG %s @ %.2f Qty: %d Leverage: %.1f", sym, ltp, qty, leverage))
}

func enterShort(sym, token string, ltp float64, leverage float64) {
	regime := store.GetRegime()
	activeBudget := regime.PositionBudget
	if activeBudget <= 0 {
		activeBudget = defaultBudget
	}

	effectiveBudget := activeBudget * leverage
	qty := int(effectiveBudget / ltp)
	if qty < 1 {
		logTrade(fmt.Sprintf("SHORT skipped - insufficient budget %s (lev %.1f)", sym, leverage))
		return
	}

	err := placeOrder(sym, token, "SELL", "MKT", qty, effectiveBudget)
	if err != nil {
		logTrade(fmt.Sprintf("SHORT ENTRY FAILED %s: %v", sym, err))
		return
	}

	store.OpenShort(sym, ltp, qty, time.Now())
	logTrade(fmt.Sprintf("ENTRY SHORT %s @ %.2f Qty: %d Leverage: %.1f", sym, ltp, qty, leverage))
}

// ----------------------------------------------------------------------
// Exit Handlers
// ----------------------------------------------------------------------

func exitLong(sym, token string, ltp float64, qty int, reason string) {
	pos, ok := store.CloseLong(sym)
	if !ok {
		return
	}

	err := placeOrder(sym, token, "SELL", "MKT", qty, 0)
	if err != nil {
		logTrade(fmt.Sprintf("LONG EXIT FAILED %s: %v", sym, err))
		return
	}

	pnl := float64(qty) * (ltp - pos.EntryPrice)
	logTrade(fmt.Sprintf("EXIT LONG %s @ %.2f Qty: %d P&L: ₹%.2f Reason: %s", sym, ltp, qty, pnl, reason))

	record := models.TradeRecord{
		Symbol:     sym,
		Direction:  "LONG",
		EntryTime:  pos.EntryTime,
		EntryPrice: pos.EntryPrice,
		ExitTime:   time.Now(),
		ExitPrice:  ltp,
		Qty:        qty,
		PnL:        pnl,
		Reason:     reason,
	}
	store.RecordTrade(record)
	go appendToLedgerCSV(record)
}

func exitShort(sym, token string, ltp float64, qty int, reason string) {
	pos, ok := store.CloseShort(sym)
	if !ok {
		return
	}

	err := placeOrder(sym, token, "BUY", "MKT", qty, 0)
	if err != nil {
		logTrade(fmt.Sprintf("SHORT EXIT FAILED %s: %v", sym, err))
		return
	}

	pnl := float64(qty) * (pos.EntryPrice - ltp)
	logTrade(fmt.Sprintf("EXIT SHORT %s @ %.2f Qty: %d P&L: ₹%.2f Reason: %s", sym, ltp, qty, pnl, reason))

	record := models.TradeRecord{
		Symbol:     sym,
		Direction:  "SHORT",
		EntryTime:  pos.EntryTime,
		EntryPrice: pos.EntryPrice,
		ExitTime:   time.Now(),
		ExitPrice:  ltp,
		Qty:        qty,
		PnL:        pnl,
		Reason:     reason,
	}
	store.RecordTrade(record)
	go appendToLedgerCSV(record)
}

func checkExits(sym, token string, ltp float64) {
	strat := engine.GetStrategy(sym)
	regime := store.GetRegime()
	stagnationMin := regime.StagnationMinutes
	if stagnationMin <= 0 {
		stagnationMin = 45
	}

	// Long exit check
	if pos, ok := store.UpdateLongHighest(sym, ltp); ok {
		if shouldExit, reason := engine.EvaluateLongExit(pos, strat, ltp, stagnationMin); shouldExit {
			exitLong(sym, token, ltp, pos.Qty, reason)
			return
		}
	}

	// Short exit check
	if pos, ok := store.UpdateShortLowest(sym, ltp); ok {
		if shouldExit, reason := engine.EvaluateShortExit(pos, strat, ltp, stagnationMin); shouldExit {
			exitShort(sym, token, ltp, pos.Qty, reason)
			return
		}
	}
}

func checkAllEntries(sym, token string, ltp float64) {
	if store.HasPosition(sym) {
		return
	}

	regime := store.GetRegime()
	maxPos := regime.MaxPositions
	if maxPos == 0 || regime.Status == "CRISIS" {
		return
	}

	// Circuit breaker check
	currentPnL := store.GetDailyPnL()
	if currentPnL <= -750.0 {
		return
	}

	if store.GetOpenCount() >= maxPos {
		return
	}

	hl, ok := store.GetHighLow(sym)
	if !ok {
		return
	}

	hist := store.GetHistory(sym)
	strat := engine.GetStrategy(sym)

	// Long breakout
	if shouldEnter, reason := engine.CheckBreakoutLong(sym, ltp, hl, strat.BreakoutLong); shouldEnter {
		fmt.Printf("%s\n", reason)
		enterLong(sym, token, ltp, strat.Leverage)
		return
	}

	// Long bounce
	if shouldEnter, reason := engine.CheckBounceBuy(sym, ltp, hl, hist); shouldEnter {
		fmt.Printf("%s\n", reason)
		enterLong(sym, token, ltp, strat.Leverage)
		return
	}

	if strat.AllowShort {
		// Short breakdown
		if shouldEnter, reason := engine.CheckBreakdownShort(sym, ltp, hl, strat.BreakoutShort); shouldEnter {
			fmt.Printf("%s\n", reason)
			enterShort(sym, token, ltp, strat.Leverage)
			return
		}

		// Short quick drop
		if shouldEnter, reason := engine.CheckQuickDropShort(sym, ltp, hist); shouldEnter {
			fmt.Printf("%s\n", reason)
			enterShort(sym, token, ltp, strat.Leverage)
			return
		}
	}
}

func squareOffAllPositions(now time.Time) {
	openPositions := store.GetAllOpenPositions()
	if len(openPositions) == 0 {
		return
	}

	fmt.Printf("Square-off time (%s) - exiting %d open positions\n", now.Format("15:04"), len(openPositions))
	for _, pos := range openPositions {
		token := symbolToToken[pos.Symbol]
		ltp, err := client.GetLTP("NSE", token)
		if err != nil {
			log.Printf("Square-off LTP failed for %s: %v", pos.Symbol, err)
			continue
		}

		if pos.Direction == "LONG" {
			exitLong(pos.Symbol, token, ltp, pos.Qty, "EOD Square-off")
		} else {
			exitShort(pos.Symbol, token, ltp, pos.Qty, "EOD Square-off")
		}
	}
	fmt.Println("All positions squared off.")
}

func printDailySummary(now time.Time) {
	history := store.GetTradeHistory()
	pnl := store.GetDailyPnL()

	if len(history) == 0 {
		logTrade("Daily Summary: No trades executed today")
		store.ResetDaily(now)
		return
	}

	logTrade("═══════════════════════════════════════════════════════")
	logTrade("DAILY TRADE & P&L SUMMARY")
	logTrade(fmt.Sprintf("Date: %s", now.Format("2006-01-02")))
	logTrade(fmt.Sprintf("Total Trades: %d", len(history)))
	logTrade(fmt.Sprintf("Net P&L: ₹%.2f", pnl))

	var longPnL, shortPnL float64
	for _, t := range history {
		if t.Direction == "LONG" {
			longPnL += t.PnL
		} else {
			shortPnL += t.PnL
		}
	}
	logTrade(fmt.Sprintf("Long Trades P&L: ₹%.2f", longPnL))
	logTrade(fmt.Sprintf("Short Trades P&L: ₹%.2f", shortPnL))
	logTrade("═══════════════════════════════════════════════════════")

	store.ResetDaily(now)
}

func loadStrategyConfig() error {
	dataPath := filepath.Join("data", "config.json")
	data, err := os.ReadFile(dataPath)
	if err != nil {
		return err
	}

	var configs map[string]models.StockStrategy
	if err := json.Unmarshal(data, &configs); err != nil {
		return err
	}

	engine.SetStrategies(configs)
	return nil
}

func loadMarketRegime() {
	regimePath := filepath.Join("data", "regime.json")
	data, err := os.ReadFile(regimePath)
	if err != nil {
		log.Printf("[REGIME] No regime.json found; defaulting to NORMAL")
		return
	}

	var r models.MarketRegime
	if err := json.Unmarshal(data, &r); err != nil {
		log.Printf("[REGIME] Error parsing regime.json: %v", err)
		return
	}

	store.SetRegime(r)
	logTrade(fmt.Sprintf("[SENTINEL] Regime: [%s] %s | Score: %d/100 | Max Pos: %d | Sizing: ₹%.0f | %s",
		r.Color, r.Status, r.RiskScore, r.MaxPositions, r.PositionBudget, r.Reason))
}

func loadSavedTokenMap() bool {
	path := filepath.Join("data", "token_map.json")
	data, err := os.ReadFile(path)
	if err != nil {
		return false
	}

	var saved struct {
		Map map[string]string `json:"map"`
	}
	if err := json.Unmarshal(data, &saved); err != nil {
		return false
	}

	for _, sym := range stocks.Tickers {
		if _, ok := saved.Map[sym]; !ok {
			return false
		}
	}

	symbolToToken = saved.Map
	return true
}

func saveTokenMap() {
	data, _ := json.MarshalIndent(struct {
		Map map[string]string `json:"map"`
	}{Map: symbolToToken}, "", "  ")

	path := filepath.Join("data", "token_map.json")
	os.MkdirAll(filepath.Dir(path), 0755)
	os.WriteFile(path, data, 0644)
	fmt.Println("Token map saved to data/token_map.json")
}

// ----------------------------------------------------------------------
// Main Application Loop
// ----------------------------------------------------------------------

func main() {
	config.Load()
	fmt.Println("Axiom Protocol Initializing...")

	// 1. Authenticate with Flattrade
	token, err := auth.GetSessionToken(config.C.APIKey, config.C.RequestCode, config.C.SecretKey)
	if err != nil {
		log.Fatalf("Auth failed: %v", err)
	}
	session.Set(token)
	fmt.Println("Session token set globally")

	// 2. Load watchlist
	if err := stocks.Load("data/stocks.json"); err != nil {
		log.Printf("Warning: Could not load stocks.json: %v", err)
	}

	// 3. Resolve Symbol to Token mapping
	symbolToToken = make(map[string]string)
	if loadSavedTokenMap() {
		fmt.Println("Loaded existing token map from file")
	} else {
		fmt.Println("Token map missing - resolving symbols via Flattrade API...")
		for _, sym := range stocks.Tickers {
			respBytes, err := client.SearchScrip("NSE", sym+"-EQ")
			if err != nil {
				log.Printf("Search failed for %s: %v", sym, err)
				continue
			}

			var sr client.SearchResult
			if err := json.Unmarshal(respBytes, &sr); err != nil {
				log.Printf("JSON parse error for %s: %v", sym, err)
				continue
			}

			if sr.Stat == "Ok" {
				for _, v := range sr.Values {
					if strings.Contains(v.Tsym, "-EQ") {
						symbolToToken[sym] = v.Token
						fmt.Printf("Mapped %s → %s\n", sym, v.Token)
						break
					}
				}
			}
			time.Sleep(200 * time.Millisecond)
		}
		saveTokenMap()
	}

	fmt.Printf("Mapped %d/%d symbols successfully\n", len(symbolToToken), len(stocks.Tickers))

	// 4. Load strategy config and macro regime
	if err := loadStrategyConfig(); err != nil {
		log.Printf("Warning: Could not load config.json: %v", err)
	}
	loadMarketRegime()

	// 5. LTP sanity check
	if len(symbolToToken) > 0 {
		for s, t := range symbolToToken {
			ltp, err := client.GetLTP("NSE", t)
			if err != nil {
				log.Printf("Immediate LTP test for %s failed: %v", s, err)
			} else {
				fmt.Printf("Immediate LTP test for %s OK: %.2f\n", s, ltp)
			}
			break
		}
	}

	fmt.Println("Axiom Protocol Online")
	if paperTrading {
		fmt.Println("Mode selected - Paper Trading")
	} else {
		fmt.Println("Mode selected - LIVE TRADING")
	}

	istZone := time.FixedZone("IST", 5*60*60+30*60)
	ticker := time.NewTicker(20 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		now := time.Now().In(istZone)
		hour, min := now.Hour(), now.Minute()

		// ------------------------------------------------------------------
		// Explicit Market-Hours Gating
		// NSE Normal Trading Hours: 09:15 to 15:30 IST
		// ------------------------------------------------------------------
		isMarketOpen := (hour == 9 && min >= 15) || (hour > 9 && hour < 15) || (hour == 15 && min <= 30)
		canEnter := (hour == 9 && min >= 15) || (hour > 9 && hour < 15)

		// Pre-market sentinel refresh at 09:14 IST
		if hour == 9 && min == 14 && now.Second() < 25 {
			loadMarketRegime()
		}

		// Post-market daily summary at 15:30 IST
		if hour == 15 && min >= 30 && store.GetLastReset().Format("2006-01-02") != now.Format("2006-01-02") {
			printDailySummary(now)
		}

		// Auto square-off at 15:10 IST
		if hour == 15 && min >= 10 {
			squareOffAllPositions(now)
		}

		// Outside trading hours: Idle safely without corrupting High/Low
		if !isMarketOpen {
			fmt.Printf("[%s] Market closed (Regular hours: 09:15 - 15:30 IST). Idling...\n", now.Format("15:04:05"))
			continue
		}

		// Circuit breaker warning
		if store.GetDailyPnL() <= -750.0 {
			fmt.Printf("[%s] CIRCUIT BREAKER: Daily loss floor (-₹750) reached. New entries halted.\n", now.Format("15:04:05"))
		}

		fmt.Printf("\nPolling LTP at %s\n", now.Format("15:04:05"))
		successCount := 0

		for sym, token := range symbolToToken {
			ltp, err := client.GetLTP("NSE", token)
			if err != nil {
				log.Printf("%s LTP error: %v", sym, err)
				if strings.Contains(err.Error(), "exceeds Limit") {
					time.Sleep(2 * time.Second)
				}
				continue
			}

			successCount++

			// 1. Check exits for open positions
			checkExits(sym, token, ltp)

			// 2. Append tick history for mean-reversion filters
			store.AppendHistory(sym, ltp, historyWindow)

			// 3. Evaluate new entries if market is open for entries
			if canEnter {
				checkAllEntries(sym, token, ltp)
			}

			// 4. Update established High/Low during market hours only
			store.UpdateHighLow(sym, ltp)

			time.Sleep(200 * time.Millisecond)
		}

		fmt.Printf("Successfully fetched LTP for %d stocks\n", successCount)
		fmt.Println("---")
	}
}
