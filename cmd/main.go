package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/may-bach/Axiom/internal/auth"
	"github.com/may-bach/Axiom/internal/client"
	"github.com/may-bach/Axiom/internal/config"
	"github.com/may-bach/Axiom/internal/session"
	"github.com/may-bach/Axiom/internal/stocks"
)

var (
	symbolToToken map[string]string
	mu            sync.Mutex
	highLow       = make(map[string]struct{ High, Low float64 })
	ltpHistory    = make(map[string][]float64)
	longPositions = make(map[string]struct {
		EntryPrice, HighestPrice float64
		Qty                      int
		EntryTime                time.Time // added for better P&L tracking
	})
	shortPositions = make(map[string]struct {
		EntryPrice, LowestPrice float64
		Qty                     int
		EntryTime               time.Time // added
	})
	stockStrategies = make(map[string]StockStrategy)

	defaultBudget          = 15000.0 // Scaled for ₹10k capital + 5x margin
	defaultMaxPositions    = 3       // Max 2-3 positions concurrently
	defaultBuffer          = 0.002
	defaultBounceRebound   = 0.008
	defaultQuickDrop       = 0.012
	defaultFixedSLPercent  = 1.0
	defaultTargetPercent   = 2.0
	defaultTrailingPercent = 1.0
	defaultLeverage        = 1.0
	historyWindow          = 3

	// ────────────────────────────────────────────────
	// NEW FEATURES
	// ────────────────────────────────────────────────
	paperTrading   = true // ← Set to false for LIVE trading
	tradeLogFile   *os.File
	dailyPnL       float64
	lastDailyReset time.Time
	tradeHistory   []TradeRecord

	currentRegime = MarketRegime{
		Status:         "NORMAL",
		Color:          "GREEN",
		MaxPositions:   3,
		PositionBudget: 15000.0,
		DailyLossLimit: -750.0,
	}
)

type MarketRegime struct {
	Date              string   `json:"date"`
	Timestamp         string   `json:"timestamp"`
	RiskScore         int      `json:"risk_score"`
	Status            string   `json:"status"`
	Color             string   `json:"color"`
	MaxPositions      int      `json:"max_positions"`
	PositionBudget    float64  `json:"position_budget"`
	StagnationMinutes int      `json:"stagnation_minutes"`
	DailyLossLimit    float64  `json:"daily_loss_limit"`
	Reason            string   `json:"reason"`
	FlaggedHeadlines  []string `json:"flagged_headlines"`
}

type StockStrategy struct {
	Class         string  `json:"class"`
	AllowShort    bool    `json:"allow_short"`
	BreakoutLong  float64 `json:"breakout_long"`
	BreakoutShort float64 `json:"breakout_short"`
	Target        float64 `json:"target"`
	SL            float64 `json:"sl"`
	Leverage      float64 `json:"leverage"`
}

type TradeRecord struct {
	Symbol     string
	Direction  string // LONG / SHORT
	EntryTime  time.Time
	EntryPrice float64
	ExitTime   time.Time
	ExitPrice  float64
	Qty        int
	PnL        float64
	Reason     string
}

func init() {
	// Create logs directory and open trade log file
	logDir := "logs"
	os.MkdirAll(logDir, 0755)
	var err error
	tradeLogFile, err = os.OpenFile(filepath.Join(logDir, "trades.log"),
		os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		log.Fatalf("Failed to open trade log file: %v", err)
	}

	// Initialize daily reset
	lastDailyReset = time.Now().Truncate(24 * time.Hour)
	dailyPnL = 0
}

func logTrade(msg string) {
	timestamp := time.Now().Format("2006-01-02 15:04:05")
	line := fmt.Sprintf("[%s] %s\n", timestamp, msg)

	fmt.Print(line) // Console and stdout redirected to trades.log
}

func logTradeRecord(trade TradeRecord) {
	tradeHistory = append(tradeHistory, trade)
	dailyPnL += trade.PnL
}

func main() {
	config.Load()
	fmt.Println("Axiom Protocol Initializing...")

	// Authenticate
	token, err := auth.GetSessionToken(config.C.APIKey, config.C.RequestCode, config.C.SecretKey)
	if err != nil {
		log.Fatalf("Auth failed: %v", err)
	}
	session.Set(token)
	fmt.Println("Session token set globally")

	// Load watchlist
	if err := stocks.Load("data/stocks.json"); err != nil {
		log.Printf("Warning: Could not load stocks.json - %v", err)
	}

	// Symbol → Token mapping
	symbolToToken = make(map[string]string)
	fmt.Println("Mapping symbols to tokens...")

	if loadSavedTokenMap() {
		fmt.Println("Loaded existing token map from file")
	} else {
		fmt.Println("Token map incomplete or missing — resolving symbols via Flattrade API...")

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
				found := false
				for _, v := range sr.Values {
					if strings.Contains(v.Tsym, "-EQ") {
						symbolToToken[sym] = v.Token
						fmt.Printf("Mapped %s → %s\n", sym, v.Token)
						found = true
						break
					}
				}
				if !found {
					fmt.Printf("No -EQ token found for %s\n", sym)
				}
			} else {
				fmt.Printf("Search failed for %s: %s\n", sym, sr.Stat)
			}

			time.Sleep(300 * time.Millisecond)
		}
		saveTokenMap()
	}

	fmt.Printf("Mapped %d/%d symbols successfully\n", len(symbolToToken), len(stocks.Tickers))

	// Load strategy config
	if err := loadStrategyConfig(); err != nil {
		log.Printf("Warning: Could not load config.json - using defaults: %v", err)
	} else {
		fmt.Printf("Loaded %d strategies from config\n", len(stockStrategies))
	}

	// Load Market Sentinel regime (Macro & Chop Detector)
	loadMarketRegime()

	if len(symbolToToken) > 0 {
		var firstSym, firstToken string
		for s, t := range symbolToToken {
			firstSym = s
			firstToken = t
			break
		}
		ltp, err := client.GetLTP("NSE", firstToken)
		if err != nil {
			log.Printf("Immediate LTP test for %s failed: %v", firstSym, err)
		} else {
			fmt.Printf("Immediate LTP test for %s OK: %.2f\n", firstSym, ltp)
		}
	}

	fmt.Println("Axiom Protocol Online")
	if paperTrading {
		fmt.Println("Mode selected - Paper Trading")
	}

	// Main polling loop (20s interval to stay safely under Flattrade 120 reqs/min limit for 32 stocks)
	ticker := time.NewTicker(20 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		now := time.Now().In(time.FixedZone("IST", 5*60*60+30*60))

		// Refresh Market Sentinel regime check at 09:14 IST
		if now.Hour() == 9 && now.Minute() == 14 && now.Second() < 25 {
			loadMarketRegime()
		}

		// Daily summary ~15:30 after square-off
		if now.Hour() == 15 && now.Minute() >= 30 && lastDailyReset.Format("2006-01-02") != now.Format("2006-01-02") {
			printDailySummary()
		}

		// Auto square-off at 15:10 IST
		if now.Hour() == 15 && now.Minute() >= 10 {
			squareOffAllPositions(now)
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

			// 1. Check exits for any existing open positions first
			checkLongExit(sym, ltp)
			checkShortExit(sym, ltp)

			// 2. Append to recent tick history so mean-reversion and quick-drop can examine previous ticks
			updateLTPHistory(sym, ltp)

			// 3. Check new breakout and mean-reversion entries against established High/Low (active from 09:15 open until 15:00 IST)
			if now.Hour() < 15 {
				checkAllEntries(sym, ltp)
			}

			// 4. Update established High/Low with the latest tick
			updateHighLow(sym, ltp)

			time.Sleep(200 * time.Millisecond)
		}

		fmt.Printf("Successfully fetched LTP for %d stocks\n", successCount)
		fmt.Println("---")
	}
}

// Paper + real order wrapper
func placeOrder(sym, token, side, orderType string, qty int) error {
	if paperTrading {
		logTrade(fmt.Sprintf("PAPER %s %s Qty:%d %s (token:%s)", side, orderType, qty, sym, token))
		return nil
	}
	// Real order (your actual implementation)
	return client.PlaceOrder(sym, token, side, orderType, qty)
}

// ──────────────────────────────────────────────────────────────────────────────
// Entry functions with logging
// ──────────────────────────────────────────────────────────────────────────────

func enterLong(sym string, ltp float64, leverage float64) {
	effectiveBudget := defaultBudget * leverage
	qty := int(effectiveBudget / ltp)
	if qty < 1 {
		logTrade(fmt.Sprintf("LONG skipped - insufficient budget %s (lev %.1f)", sym, leverage))
		return
	}

	err := placeOrder(sym, symbolToToken[sym], "BUY", "MKT", qty)
	if err != nil {
		logTrade(fmt.Sprintf("LONG ENTRY FAILED %s: %v", sym, err))
		return
	}

	mu.Lock()
	longPositions[sym] = struct {
		EntryPrice, HighestPrice float64
		Qty                      int
		EntryTime                time.Time
	}{ltp, ltp, qty, time.Now()}
	mu.Unlock()

	logTrade(fmt.Sprintf("ENTRY LONG %s @ %.2f Qty: %d Leverage: %.1f", sym, ltp, qty, leverage))
}

func enterShort(sym string, ltp float64, leverage float64) {
	effectiveBudget := defaultBudget * leverage
	qty := int(effectiveBudget / ltp)
	if qty < 1 {
		logTrade(fmt.Sprintf("SHORT skipped - insufficient budget %s (lev %.1f)", sym, leverage))
		return
	}

	err := placeOrder(sym, symbolToToken[sym], "SELL", "MKT", qty)
	if err != nil {
		logTrade(fmt.Sprintf("SHORT ENTRY FAILED %s: %v", sym, err))
		return
	}

	mu.Lock()
	shortPositions[sym] = struct {
		EntryPrice, LowestPrice float64
		Qty                     int
		EntryTime               time.Time
	}{ltp, ltp, qty, time.Now()}
	mu.Unlock()

	logTrade(fmt.Sprintf("ENTRY SHORT %s @ %.2f Qty: %d Leverage: %.1f", sym, ltp, qty, leverage))
}

// ──────────────────────────────────────────────────────────────────────────────
// Exit functions with P&L calculation
// ──────────────────────────────────────────────────────────────────────────────

func exitLong(sym string, ltp float64, qty int, reason string) {
	err := placeOrder(sym, symbolToToken[sym], "SELL", "MKT", qty)
	if err != nil {
		logTrade(fmt.Sprintf("LONG EXIT FAILED %s: %v", sym, err))
		return
	}

	mu.Lock()
	pos := longPositions[sym]
	delete(longPositions, sym)
	mu.Unlock()

	pnl := float64(qty) * (ltp - pos.EntryPrice)
	logTrade(fmt.Sprintf("EXIT LONG %s @ %.2f Qty: %d P&L: ₹%.2f Reason: %s", sym, ltp, qty, pnl, reason))

	logTradeRecord(TradeRecord{
		Symbol:     sym,
		Direction:  "LONG",
		EntryTime:  pos.EntryTime,
		EntryPrice: pos.EntryPrice,
		ExitTime:   time.Now(),
		ExitPrice:  ltp,
		Qty:        qty,
		PnL:        pnl,
		Reason:     reason,
	})
}

func exitShort(sym string, ltp float64, qty int, reason string) {
	err := placeOrder(sym, symbolToToken[sym], "BUY", "MKT", qty)
	if err != nil {
		logTrade(fmt.Sprintf("SHORT EXIT FAILED %s: %v", sym, err))
		return
	}

	mu.Lock()
	pos := shortPositions[sym]
	delete(shortPositions, sym)
	mu.Unlock()

	pnl := float64(qty) * (pos.EntryPrice - ltp)
	logTrade(fmt.Sprintf("EXIT SHORT %s @ %.2f Qty: %d P&L: ₹%.2f Reason: %s", sym, ltp, qty, pnl, reason))

	logTradeRecord(TradeRecord{
		Symbol:     sym,
		Direction:  "SHORT",
		EntryTime:  pos.EntryTime,
		EntryPrice: pos.EntryPrice,
		ExitTime:   time.Now(),
		ExitPrice:  ltp,
		Qty:        qty,
		PnL:        pnl,
		Reason:     reason,
	})
}

// ──────────────────────────────────────────────────────────────────────────────
// Updated exit checks (pass reason to exit functions)
// ──────────────────────────────────────────────────────────────────────────────

func checkLongExit(sym string, ltp float64) {
	mu.Lock()
	pos, exists := longPositions[sym]
	mu.Unlock()

	if !exists {
		return
	}

	strat := getStrategy(sym)

	mu.Lock()
	pos.HighestPrice = max(pos.HighestPrice, ltp)
	longPositions[sym] = pos
	mu.Unlock()

	fixedSL := pos.EntryPrice * (1 - strat.SL)
	if ltp <= fixedSL {
		exitLong(sym, ltp, pos.Qty, fmt.Sprintf("Fixed SL %.1f%%", strat.SL*100))
		return
	}

	target := pos.EntryPrice * (1 + strat.Target)
	if ltp >= target {
		exitLong(sym, ltp, pos.Qty, fmt.Sprintf("Target %.1f%%", strat.Target*100))
		return
	}

	trailingSL := pos.HighestPrice * (1 - defaultTrailingPercent/100)
	if ltp <= trailingSL {
		exitLong(sym, ltp, pos.Qty, "Trailing SL")
		return
	}

	// Stagnation Timeout: Exit if trade has gone nowhere after 45 minutes
	if time.Since(pos.EntryTime) >= 45*time.Minute {
		pctChange := (ltp - pos.EntryPrice) / pos.EntryPrice
		if pctChange >= -0.004 && pctChange <= 0.004 {
			exitLong(sym, ltp, pos.Qty, "Stagnation Timeout (45m flat)")
			return
		}
	}
}

func checkShortExit(sym string, ltp float64) {
	mu.Lock()
	pos, exists := shortPositions[sym]
	mu.Unlock()

	if !exists {
		return
	}

	strat := getStrategy(sym)

	mu.Lock()
	pos.LowestPrice = min(pos.LowestPrice, ltp)
	shortPositions[sym] = pos
	mu.Unlock()

	fixedSL := pos.EntryPrice * (1 + strat.SL)
	if ltp >= fixedSL {
		exitShort(sym, ltp, pos.Qty, fmt.Sprintf("Fixed SL %.1f%%", strat.SL*100))
		return
	}

	target := pos.EntryPrice * (1 - strat.Target)
	if ltp <= target {
		exitShort(sym, ltp, pos.Qty, fmt.Sprintf("Target %.1f%%", strat.Target*100))
		return
	}

	trailingSL := pos.LowestPrice * (1 + defaultTrailingPercent/100)
	if ltp >= trailingSL {
		exitShort(sym, ltp, pos.Qty, "Trailing SL")
		return
	}

	// Stagnation Timeout: Exit if trade has gone nowhere after 45 minutes
	if time.Since(pos.EntryTime) >= 45*time.Minute {
		pctChange := (pos.EntryPrice - ltp) / pos.EntryPrice
		if pctChange >= -0.004 && pctChange <= 0.004 {
			exitShort(sym, ltp, pos.Qty, "Stagnation Timeout (45m flat)")
			return
		}
	}
}

// ──────────────────────────────────────────────────────────────────────────────
// Daily summary at ~15:30
// ──────────────────────────────────────────────────────────────────────────────

func printDailySummary() {
	mu.Lock()
	defer mu.Unlock()

	lastDailyReset = time.Now().In(time.FixedZone("IST", 5*60*60+30*60))
	highLow = make(map[string]struct{ High, Low float64 })
	ltpHistory = make(map[string][]float64)

	if len(tradeHistory) == 0 {
		logTrade("Daily Summary: No trades executed today")
		return
	}

	logTrade("═══════════════════════════════════════════════════════")
	logTrade("DAILY TRADE & P&L SUMMARY")
	logTrade(fmt.Sprintf("Date: %s", time.Now().Format("2006-01-02")))
	logTrade(fmt.Sprintf("Total Trades: %d", len(tradeHistory)))
	logTrade(fmt.Sprintf("Net P&L: ₹%.2f", dailyPnL))

	var longPnL, shortPnL float64
	for _, t := range tradeHistory {
		if t.Direction == "LONG" {
			longPnL += t.PnL
		} else {
			shortPnL += t.PnL
		}
	}
	logTrade(fmt.Sprintf("Long Trades P&L: ₹%.2f", longPnL))
	logTrade(fmt.Sprintf("Short Trades P&L: ₹%.2f", shortPnL))
	logTrade("═══════════════════════════════════════════════════════")

	// Reset for next day
	tradeHistory = nil
	dailyPnL = 0
}

func loadMarketRegime() {
	regimePath := filepath.Join("data", "regime.json")
	data, err := os.ReadFile(regimePath)
	if err != nil {
		log.Printf("[REGIME] No regime.json found; defaulting to NORMAL (Max Pos: %d, Budget: ₹%.0f)", defaultMaxPositions, defaultBudget)
		return
	}

	var r MarketRegime
	if err := json.Unmarshal(data, &r); err != nil {
		log.Printf("[REGIME] Error parsing regime.json: %v; maintaining current settings", err)
		return
	}

	mu.Lock()
	currentRegime = r
	if r.Status == "CRISIS" {
		defaultMaxPositions = 0
		defaultBudget = 0.0
	} else if r.Status == "CAUTION" {
		defaultMaxPositions = 1
		defaultBudget = 10000.0
	} else {
		defaultMaxPositions = 3
		defaultBudget = 15000.0
	}
	mu.Unlock()

	logTrade(fmt.Sprintf("[SENTINEL] Regime: [%s] %s | Score: %d/100 | Max Pos: %d | Sizing: ₹%.0f | %s",
		r.Color, r.Status, r.RiskScore, defaultMaxPositions, defaultBudget, r.Reason))
}

func loadStrategyConfig() error {
	dataPath := filepath.Join("data", "config.json")
	data, err := os.ReadFile(dataPath)
	if err != nil {
		return err
	}

	var configs map[string]StockStrategy
	if err := json.Unmarshal(data, &configs); err != nil {
		return err
	}

	mu.Lock()
	stockStrategies = configs
	mu.Unlock()

	return nil
}

func getStrategy(sym string) StockStrategy {
	mu.Lock()
	defer mu.Unlock()

	if strat, ok := stockStrategies[sym]; ok {
		return strat
	}

	return StockStrategy{
		Class:         "B",
		AllowShort:    true,
		BreakoutLong:  defaultBuffer,
		BreakoutShort: defaultBuffer,
		Target:        defaultTargetPercent / 100,
		SL:            defaultFixedSLPercent / 100,
		Leverage:      defaultLeverage,
	}
}

func updateHighLow(sym string, ltp float64) {
	mu.Lock()
	defer mu.Unlock()

	hl, exists := highLow[sym]
	if !exists {
		hl = struct{ High, Low float64 }{0, 0}
	}
	if hl.High == 0 || ltp > hl.High {
		hl.High = ltp
	}
	if hl.Low == 0 || ltp < hl.Low {
		hl.Low = ltp
	}
	highLow[sym] = hl
}

func updateLTPHistory(sym string, ltp float64) {
	mu.Lock()
	defer mu.Unlock()

	hist, exists := ltpHistory[sym]
	if !exists {
		hist = []float64{}
	}
	hist = append(hist, ltp)
	if len(hist) > historyWindow {
		hist = hist[1:]
	}
	ltpHistory[sym] = hist
}

func checkAllEntries(sym string, ltp float64) {
	mu.Lock()
	totalOpen := len(longPositions) + len(shortPositions)
	currentLoss := dailyPnL
	regimeStatus := currentRegime.Status
	maxPos := defaultMaxPositions
	mu.Unlock()

	// Circuit Breaker: Daily loss floor (-₹750)
	if currentLoss <= -750.0 {
		fmt.Printf("[CIRCUIT BREAKER] Daily loss floor -₹750 reached (Current: ₹%.2f). New entries halted for today.\n", currentLoss)
		return
	}

	// Market Shield: Crisis detected
	if regimeStatus == "CRISIS" || maxPos == 0 {
		fmt.Printf("[MARKET SHIELD] Crisis active - 0 trades allowed (%s)\n", currentRegime.Reason)
		return
	}

	if totalOpen >= maxPos {
		fmt.Printf("Max positions (%d/%d) reached - skipping %s\n", totalOpen, maxPos, sym)
		return
	}

	strat := getStrategy(sym)

	checkBreakoutLong(sym, ltp, strat.BreakoutLong)
	checkBounceBackBuy(sym, ltp)
	if strat.AllowShort {
		checkBreakdownShort(sym, ltp, strat.BreakoutShort)
		checkQuickDropShort(sym, ltp)
	}
}

func checkBreakoutLong(sym string, ltp, threshold float64) {
	mu.Lock()
	hl := highLow[sym]
	pos := longPositions[sym]
	mu.Unlock()

	if pos.EntryPrice > 0 {
		return
	}

	if hl.High > 0 && ltp > hl.High*(1+threshold) {
		fmt.Printf("BREAKOUT LONG BUY %s @ %.2f (threshold %.3f)\n", sym, ltp, threshold)
		enterLong(sym, ltp, getStrategy(sym).Leverage)
	}
}

func checkBounceBackBuy(sym string, ltp float64) {
	mu.Lock()
	hl := highLow[sym]
	hist := ltpHistory[sym]
	pos := longPositions[sym]
	mu.Unlock()

	if pos.EntryPrice > 0 || len(hist) < 2 {
		return
	}

	prev := hist[len(hist)-2]
	if prev <= hl.Low*1.005 && ltp >= prev*(1+defaultBounceRebound) {
		fmt.Printf("BOUNCE BACK BUY %s @ %.2f (prev %.2f, low %.2f)\n", sym, ltp, prev, hl.Low)
		enterLong(sym, ltp, getStrategy(sym).Leverage)
	}
}

func checkBreakdownShort(sym string, ltp, threshold float64) {
	mu.Lock()
	hl := highLow[sym]
	pos := shortPositions[sym]
	mu.Unlock()

	if pos.EntryPrice > 0 {
		return
	}

	if hl.Low > 0 && ltp < hl.Low*(1-threshold) {
		fmt.Printf("BREAKDOWN SHORT SELL %s @ %.2f (threshold %.3f)\n", sym, ltp, threshold)
		enterShort(sym, ltp, getStrategy(sym).Leverage)
	}
}

func checkQuickDropShort(sym string, ltp float64) {
	mu.Lock()
	hist := ltpHistory[sym]
	pos := shortPositions[sym]
	mu.Unlock()

	if pos.EntryPrice > 0 || len(hist) < 2 {
		return
	}

	prev := hist[len(hist)-2]
	drop := (prev - ltp) / prev
	if drop >= defaultQuickDrop {
		fmt.Printf("QUICK DROP SHORT SELL %s @ %.2f (drop %.2f%%)\n", sym, ltp, drop*100)
		enterShort(sym, ltp, getStrategy(sym).Leverage)
	}
}

func squareOffAllPositions(now time.Time) {
	type toCloseItem struct {
		sym    string
		qty    int
		isLong bool
	}
	var items []toCloseItem

	mu.Lock()
	for sym, pos := range longPositions {
		if pos.Qty > 0 {
			items = append(items, toCloseItem{sym: sym, qty: pos.Qty, isLong: true})
		}
	}
	for sym, pos := range shortPositions {
		if pos.Qty > 0 {
			items = append(items, toCloseItem{sym: sym, qty: pos.Qty, isLong: false})
		}
	}
	mu.Unlock()

	if len(items) == 0 {
		return
	}

	fmt.Printf("Square-off time (%s) - exiting %d open positions\n", now.Format("15:04"), len(items))

	for _, item := range items {
		ltp, err := client.GetLTP("NSE", symbolToToken[item.sym])
		if err != nil {
			log.Printf("Square-off LTP failed for %s: %v", item.sym, err)
			continue
		}
		if item.isLong {
			exitLong(item.sym, ltp, item.qty, "EOD Square-off")
		} else {
			exitShort(item.sym, ltp, item.qty, "EOD Square-off")
		}
	}

	fmt.Println("All positions squared off.")
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

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func min(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
