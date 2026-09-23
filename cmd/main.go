package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
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
	effectiveBudget := store.GetPositionBudget(leverage)
	if effectiveBudget <= 0 {
		return
	}

	qty := int(effectiveBudget / ltp)
	if qty < 1 {
		logTrade(fmt.Sprintf("LONG skipped - insufficient budget %s (lev %.1f, budget ₹%.0f)", sym, leverage, effectiveBudget))
		return
	}

	// 5x MIS margin requirement (20% of gross trade value)
	requiredMargin := effectiveBudget / 5.0
	err := placeOrder(sym, token, "BUY", "MKT", qty, requiredMargin)
	if err != nil {
		logTrade(fmt.Sprintf("LONG ENTRY FAILED %s: %v", sym, err))
		return
	}

	store.OpenLong(sym, ltp, qty, time.Now())
	logTrade(fmt.Sprintf("ENTRY LONG %s @ %.2f Qty: %d Leverage: %.1f Budget: ₹%.0f (Margin: ₹%.0f)", sym, ltp, qty, leverage, effectiveBudget, requiredMargin))
}

func enterShort(sym, token string, ltp float64, leverage float64) {
	effectiveBudget := store.GetPositionBudget(leverage)
	if effectiveBudget <= 0 {
		return
	}

	qty := int(effectiveBudget / ltp)
	if qty < 1 {
		logTrade(fmt.Sprintf("SHORT skipped - insufficient budget %s (lev %.1f, budget ₹%.0f)", sym, leverage, effectiveBudget))
		return
	}

	// 5x MIS margin requirement (20% of gross trade value)
	requiredMargin := effectiveBudget / 5.0
	err := placeOrder(sym, token, "SELL", "MKT", qty, requiredMargin)
	if err != nil {
		logTrade(fmt.Sprintf("SHORT ENTRY FAILED %s: %v", sym, err))
		return
	}

	store.OpenShort(sym, ltp, qty, time.Now())
	logTrade(fmt.Sprintf("ENTRY SHORT %s @ %.2f Qty: %d Leverage: %.1f Budget: ₹%.0f (Margin: ₹%.0f)", sym, ltp, qty, leverage, effectiveBudget, requiredMargin))
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

type stockReturn struct {
	symbol    string
	returnPct float64
}

func finalizeBaseAndRSRanks() {
	if store.IsBaseEstablished() {
		return
	}

	baseRanges := store.GetAllBaseRanges()
	if len(baseRanges) < 5 {
		return
	}

	stockReturns := make([]stockReturn, 0, len(baseRanges))
	for sym, br := range baseRanges {
		if br.OpenPrice > 0 {
			stockReturns = append(stockReturns, stockReturn{
				symbol:    sym,
				returnPct: br.ReturnPct,
			})
		}
	}

	if len(stockReturns) == 0 {
		return
	}

	sort.Slice(stockReturns, func(i, j int) bool {
		return stockReturns[i].returnPct < stockReturns[j].returnPct
	})

	n := len(stockReturns)
	// Dynamic Moving Stock Window:
	// Strictly focus on the Top 5 momentum leaders for Long and Bottom 5 for Short.
	// Filter out stagnant stocks: Long candidates must have ReturnPct > +0.20%, Short candidates < -0.20%.
	windowSize := 5
	if n < 10 {
		windowSize = n / 3
		if windowSize < 1 {
			windowSize = 1
		}
	}

	leaders := make(map[string]bool)
	laggards := make(map[string]bool)

	leaderSyms := make([]string, 0)
	laggardSyms := make([]string, 0)

	// Bottom Moving Window (Weakest Laggards)
	for i := 0; i < windowSize && i < n; i++ {
		sr := stockReturns[i]
		if sr.returnPct < -0.20 {
			laggards[sr.symbol] = true
			laggardSyms = append(laggardSyms, fmt.Sprintf("%s(%+.2f%%)", sr.symbol, sr.returnPct))
		}
	}

	// Top Moving Window (Strongest Leaders)
	for i := n - windowSize; i < n; i++ {
		if i >= 0 {
			sr := stockReturns[i]
			if sr.returnPct > 0.20 {
				leaders[sr.symbol] = true
				leaderSyms = append(leaderSyms, fmt.Sprintf("%s(%+.2f%%)", sr.symbol, sr.returnPct))
			}
		}
	}

	store.SetRSRanks(leaders, laggards)
	store.SetBaseEstablished(true)

	logTrade("═══════════════════════════════════════════════════════")
	logTrade(fmt.Sprintf("[MOVING STOCK WINDOW] 09:35 IST Active Window Frozen (%d universe stocks)", n))
	logTrade(fmt.Sprintf("Top 5 Moving Leaders (Long Window): %s", strings.Join(leaderSyms, ", ")))
	logTrade(fmt.Sprintf("Bottom 5 Moving Laggards (Short Window): %s", strings.Join(laggardSyms, ", ")))
	logTrade("═══════════════════════════════════════════════════════")
}

func checkAllEntries(sym, token string, ltp float64) {
	if store.HasPosition(sym) {
		return
	}

	// Pillar 4: Max 1 trade per symbol per day
	if store.HasSymbolTradedToday(sym) {
		return
	}

	regime := store.GetRegime()
	maxPos := regime.MaxPositions
	if maxPos == 0 || regime.Status == "CRISIS" || regime.Status == "CALM_STAND_DOWN" {
		return
	}

	// Institutional Guard: Cap at max 3 total trades per day to prevent churn and statutory fee drag
	if len(store.GetTradeHistory()) >= 3 {
		return
	}

	// Circuit breaker check (dynamic -7.5% of current account balance)
	lossLimit := store.GetDailyLossLimit()
	currentPnL := store.GetDailyPnL()
	if currentPnL <= lossLimit {
		return
	}

	if store.GetOpenCount() >= maxPos {
		return
	}

	baseRange, ok := store.GetBaseRange(sym)
	if !ok || baseRange.High == 0 {
		return
	}

	vwap := store.GetVWAP(sym)
	if vwap == 0 {
		vwap = ltp
	}

	strat := engine.GetStrategy(sym)
	directive := store.GetStockDirective(sym) // "LONG_ONLY", "SHORT_ONLY", "BLOCKED", "NEUTRAL"

	allowLong, allowShort := engine.ResolveAllowedDirections(directive, strat.AllowShort)
	if !allowLong && !allowShort {
		return
	}

	isRSLeader := store.IsRSLeader(sym)
	isRSLaggard := store.IsRSLaggard(sym)

	advPct, avgRet, totalCount := store.GetMarketBreadth()

	// Long base breakout: requires RS Leader AND ltp > vwap AND ltp >= baseHigh * (1 + buf) AND Trend Alignment
	if allowLong && isRSLeader {
		trendOK, trendReason := engine.CheckTrendAlignment("LONG", advPct, avgRet, totalCount)
		if !trendOK {
			log.Printf("[TREND GATE] %s LONG blocked: %s", sym, trendReason)
		} else if shouldEnter, reason := engine.CheckBaseBreakoutLong(sym, ltp, baseRange.High, vwap, isRSLeader, strat.BreakoutLong); shouldEnter {
			logTrade(fmt.Sprintf("%s [Directive: %s, RS Leader, %s]", reason, directive, trendReason))
			enterLong(sym, token, ltp, strat.Leverage)
			store.MarkSymbolTradedToday(sym)
			return
		}
	}

	// Short base breakdown: requires RS Laggard AND ltp < vwap AND ltp <= baseLow * (1 - buf) AND Trend Alignment
	if allowShort && isRSLaggard {
		trendOK, trendReason := engine.CheckTrendAlignment("SHORT", advPct, avgRet, totalCount)
		if !trendOK {
			log.Printf("[TREND GATE] %s SHORT blocked: %s", sym, trendReason)
		} else if shouldEnter, reason := engine.CheckBaseBreakdownShort(sym, ltp, baseRange.Low, vwap, isRSLaggard, strat.BreakoutShort); shouldEnter {
			logTrade(fmt.Sprintf("%s [Directive: %s, RS Laggard, %s]", reason, directive, trendReason))
			enterShort(sym, token, ltp, strat.Leverage)
			store.MarkSymbolTradedToday(sym)
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
	todayStr := now.Format("2006-01-02")
	timeStr := now.Format("2006-01-02 15:04:05 IST")

	acc, applied := store.UpdateAccountDaily(pnl, todayStr, timeStr)
	if applied {
		saveAccountState(acc)
	}

	if len(history) == 0 {
		logTrade(fmt.Sprintf("Daily Summary (%s): No trades executed today | Compounded Capital: ₹%.2f (Purchasing Power 5x: ₹%.2f)",
			todayStr, acc.CurrentBalance, acc.CurrentBalance*5.0))
		store.ResetDaily(now)
		return
	}

	logTrade("═══════════════════════════════════════════════════════")
	logTrade("DAILY TRADE & P&L SUMMARY")
	logTrade(fmt.Sprintf("Date: %s", todayStr))
	logTrade(fmt.Sprintf("Total Trades: %d", len(history)))
	logTrade(fmt.Sprintf("Net Realized P&L: ₹%.2f", pnl))
	logTrade(fmt.Sprintf("Compounded Account Balance: ₹%.2f (Purchasing Power 5x: ₹%.2f)", acc.CurrentBalance, acc.CurrentBalance*5.0))
	logTrade(fmt.Sprintf("Next Day Dynamic Loss Floor: ₹%.2f (-7.5%%)", store.GetDailyLossLimit()))

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

var lastRegimeMtime time.Time

func checkAndReloadMarketRegime() {
	regimePath := filepath.Join("data", "regime.json")
	info, err := os.Stat(regimePath)
	if err != nil {
		return
	}
	if info.ModTime().After(lastRegimeMtime) {
		oldRegime := store.GetRegime()
		loadMarketRegime()
		newRegime := store.GetRegime()

		// Midday emergency crisis shutdown check
		if newRegime.Status == "CRISIS" && oldRegime.Status != "CRISIS" {
			logTrade("[EMERGENCY CRISIS SHIELD] Sentinel declared CRISIS during live session. Squaring off all open positions immediately!")
			now := time.Now()
			squareOffAllPositions(now)
		}
	}
}

func loadMarketRegime() {
	regimePath := filepath.Join("data", "regime.json")
	if info, err := os.Stat(regimePath); err == nil {
		lastRegimeMtime = info.ModTime()
	}

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
	if r.MacroTheme != "" {
		overrides := 0
		for _, d := range r.StockDirectives {
			if d != "NEUTRAL" && d != "" {
				overrides++
			}
		}
		logTrade(fmt.Sprintf("[SENTINEL] Macro Theme: %s | Active Directives: %d overrides", r.MacroTheme, overrides))
	}
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

	if symbolToToken == nil {
		symbolToToken = make(map[string]string)
	}
	for k, v := range saved.Map {
		symbolToToken[k] = v
	}

	allFound := true
	for _, sym := range stocks.Tickers {
		if _, ok := symbolToToken[sym]; !ok {
			allFound = false
			break
		}
	}

	return allFound
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

func loadAccountState() {
	path := filepath.Join("data", "account.json")
	data, err := os.ReadFile(path)
	if err != nil {
		log.Printf("[ACCOUNT] No account.json found; initializing baseline ₹10,000 capital")
		acc := models.AccountState{
			InitialCapital:     10000.0,
			CurrentBalance:     10000.0,
			PeakBalance:        10000.0,
			TotalRealizedPnL:   0.0,
			LastUpdated:        time.Now().Format("2006-01-02 15:04:05 IST"),
			LastCompoundedDate: "",
		}
		store.SetAccount(acc)
		saveAccountState(acc)
		return
	}

	var acc models.AccountState
	if err := json.Unmarshal(data, &acc); err != nil {
		log.Printf("[ACCOUNT] Error parsing account.json: %v; initializing baseline", err)
		return
	}

	store.SetAccount(acc)
	logTrade(fmt.Sprintf("[ACCOUNT] Initial Capital: ₹%.2f | Balance: ₹%.2f | 5x Purchasing Power: ₹%.2f | Loss Floor: ₹%.2f",
		acc.InitialCapital, acc.CurrentBalance, acc.CurrentBalance*5.0, store.GetDailyLossLimit()))
}

func saveAccountState(acc models.AccountState) {
	path := filepath.Join("data", "account.json")
	os.MkdirAll(filepath.Dir(path), 0755)
	data, err := json.MarshalIndent(acc, "", "  ")
	if err != nil {
		log.Printf("[ACCOUNT] Error serializing account state: %v", err)
		return
	}
	if err := os.WriteFile(path, data, 0644); err != nil {
		log.Printf("[ACCOUNT] Error writing account.json: %v", err)
		return
	}
	logTrade(fmt.Sprintf("[ACCOUNT] Saved compounded balance: ₹%.2f (5x power: ₹%.2f)", acc.CurrentBalance, acc.CurrentBalance*5.0))
}

// ----------------------------------------------------------------------
// Main Application Loop
// ----------------------------------------------------------------------

func main() {
	config.Load()
	loadAccountState()
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
		fmt.Printf("Loaded existing token map from file (%d/%d tickers ready)\n", len(symbolToToken), len(stocks.Tickers))
	} else {
		fmt.Printf("Resolving missing symbols via Flattrade API (%d already cached)...\n", len(symbolToToken))
		newResolved := 0
		for _, sym := range stocks.Tickers {
			if _, exists := symbolToToken[sym]; exists {
				continue
			}
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
						newResolved++
						break
					}
				}
			}
			time.Sleep(150 * time.Millisecond)
		}
		if newResolved > 0 {
			saveTokenMap()
		}
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
		canEnter := ((hour == 9 && min >= 35) || (hour > 9 && hour < 14)) // Pillar 4: 09:35 to 14:00 IST entry cutoff

		// Dynamic regime hot-reload: detects any asynchronous Sentinel updates during the day
		checkAndReloadMarketRegime()

		// Post-market daily summary at 15:30 IST (or anytime post-market if not yet compounded today)
		todayStr := now.Format("2006-01-02")
		isPostMarket := (hour == 15 && min >= 30) || hour > 15
		if isPostMarket && store.GetAccount().LastCompoundedDate != todayStr {
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
		lossLimit := store.GetDailyLossLimit()
		if store.GetDailyPnL() <= lossLimit {
			fmt.Printf("[%s] CIRCUIT BREAKER: Daily loss floor (₹%.2f) reached. New entries halted.\n", now.Format("15:04:05"), lossLimit)
		}

		fmt.Printf("\nPolling Quotes & Smart Signals at %s\n", now.Format("15:04:05"))
		successCount := 0

		for sym, token := range symbolToToken {
			quote, err := client.GetQuoteDetails("NSE", token)
			if err != nil {
				// Fallback to LTP if GetQuoteDetails has issues
				ltp, lerr := client.GetLTP("NSE", token)
				if lerr != nil {
					log.Printf("%s Quote error: %v", sym, err)
					if strings.Contains(err.Error(), "exceeds Limit") {
						time.Sleep(2 * time.Second)
					}
					continue
				}
				quote = models.QuoteData{LTP: ltp, VWAP: ltp, Open: ltp, High: ltp, Low: ltp}
			}

			ltp := quote.LTP
			vwap := quote.VWAP
			store.SetVWAP(sym, vwap)
			store.UpdateLTP(sym, ltp)

			successCount++

			// 1. Check exits for open positions
			checkExits(sym, token, ltp)

			// 2. Append tick history for mean-reversion filters
			store.AppendHistory(sym, ltp, historyWindow)

			// 3. During Base establishment window (09:15 to 09:35 IST): record and anchor Base Range
			if !store.IsBaseEstablished() {
				br, exists := store.GetBaseRange(sym)
				openP := quote.Open
				if openP <= 0 {
					openP = ltp
				}
				highP := quote.High
				if highP <= 0 || ltp > highP {
					highP = ltp
				}
				lowP := quote.Low
				if lowP <= 0 || ltp < lowP {
					lowP = ltp
				}
				if exists {
					if br.High > highP {
						highP = br.High
					}
					if br.Low > 0 && br.Low < lowP {
						lowP = br.Low
					}
					if br.OpenPrice > 0 {
						openP = br.OpenPrice
					}
				}
				retPct := 0.0
				if openP > 0 {
					retPct = ((ltp - openP) / openP) * 100.0
				}
				store.SetBaseRange(sym, openP, highP, lowP, retPct)
			}

			// 4. Evaluate new entries if market is open for entries (09:35 to 14:00 IST) and base is established
			if canEnter && store.IsBaseEstablished() {
				checkAllEntries(sym, token, ltp)
			}

			// 5. Update established High/Low during market hours only
			store.UpdateHighLow(sym, ltp)

			time.Sleep(200 * time.Millisecond)
		}

		// If past 09:35 and base not yet established, finalize it now
		if (hour > 9 || (hour == 9 && min >= 35)) && !store.IsBaseEstablished() {
			finalizeBaseAndRSRanks()
		}

		fmt.Printf("Successfully fetched LTP for %d stocks\n", successCount)
		fmt.Println("---")
	}
}
