package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
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

	defaultBudget          = 100000.0
	defaultMaxPositions    = 8
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
)

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

	fmt.Print(line) // console

	if tradeLogFile != nil {
		tradeLogFile.WriteString(line)
		tradeLogFile.Sync()
	}
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
	} else {
		fmt.Printf("Loaded %d stocks to monitor\n", len(stocks.Tickers))
	}

	// Symbol → Token mapping
	symbolToToken = make(map[string]string)
	fmt.Println("Mapping symbols to tokens...")

	if loadSavedTokenMap() {
		fmt.Println("Loaded existing token map from file")
	} else {
		fmt.Println("Token map not found or expired — re-authenticating...")
		newToken, err := auth.GetSessionToken(config.C.APIKey, config.C.RequestCode, config.C.SecretKey)
		if err != nil {
			log.Fatalf("Re-auth failed during mapping: %v", err)
		}
		session.Set(newToken)
		fmt.Println("Re-authenticated — fresh session token set")

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
				fmt.Printf("Search failed for %s: %s\n", sym)
			}

			time.Sleep(300 * time.Millisecond)
		}
		saveTokenMap()
	}

	fmt.Printf("Mapped %d/%d symbols successfully\n", len(symbolToToken), len(stocks.Tickers))

	// Load brain config
	if err := loadBrainConfig(); err != nil {
		log.Printf("Warning: Could not load config.json - using defaults: %v", err)
	} else {
		fmt.Printf("Loaded %d stock-specific strategies from config.json\n", len(stockStrategies))
	}

	// Immediate LTP test
	fmt.Println("Testing LTP immediately after auth...")
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
		fmt.Println("PAPER TRADING MODE ACTIVE — No real orders will be placed")
	}

	// Main polling loop
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	lastBrainUpdate := time.Now()

	for range ticker.C {
		now := time.Now().In(time.FixedZone("IST", 5*60*60+30*60))

		// Daily summary ~15:30 after square-off
		if now.Hour() == 15 && now.Minute() >= 30 && now.Sub(lastDailyReset) >= 24*time.Hour {
			printDailySummary()
		}

		// Auto square-off at 15:10 IST
		if now.Hour() == 15 && now.Minute() >= 10 {
			squareOffAllPositions(now)
		}

		// Refresh brain.py config every 15 minutes
		if time.Since(lastBrainUpdate) >= 15*time.Minute {
			runBrainAndReload()
			lastBrainUpdate = time.Now()
		}

		fmt.Printf("\nPolling LTP at %s\n", now.Format("15:04:05"))

		successCount := 0
		for sym, token := range symbolToToken {
			if sym == "TATAMOTORS" {
				continue
			}

			ltp, err := client.GetLTP("NSE", token)
			if err != nil {
				log.Printf("%s LTP error: %v", sym, err)
				if strings.Contains(err.Error(), "exceeds Limit") {
					time.Sleep(2 * time.Second)
				}
				continue
			}

			fmt.Printf("%s LTP: %.2f\n", sym, ltp)
			successCount++

			updateHighLow(sym, ltp)
			updateLTPHistory(sym, ltp)
			checkAllEntries(sym, ltp)
			checkLongExit(sym, ltp)
			checkShortExit(sym, ltp)

			time.Sleep(200 * time.Millisecond)
		}

		fmt.Printf("Successfully fetched LTP for %d/%d stocks\n", successCount, len(symbolToToken))
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
	}
}

// ──────────────────────────────────────────────────────────────────────────────
// Daily summary at ~15:30
// ──────────────────────────────────────────────────────────────────────────────

func printDailySummary() {
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
	lastDailyReset = time.Now().Truncate(24 * time.Hour)
}

func runBrainAndReload() {
	brainPath := filepath.Join("data", "brain.py")

	cmd := exec.Command("python", brainPath)
	cmd.Dir = filepath.Dir(brainPath)

	output, err := cmd.CombinedOutput()
	if err != nil {
		log.Printf("Failed to run brain.py: %v\nOutput: %s", err, string(output))
		return
	}

	fmt.Println("brain.py executed successfully - refreshing config...")
	if err := loadBrainConfig(); err == nil {
		fmt.Printf("Reloaded config.json - %d strategies\n", len(stockStrategies))
	} else {
		log.Printf("Reload failed: %v", err)
	}
}

func loadBrainConfig() error {
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
	mu.Unlock()

	if totalOpen >= defaultMaxPositions {
		fmt.Printf("Max positions (%d/%d) reached - skipping %s\n", totalOpen, defaultMaxPositions, sym)
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
	fmt.Printf("Square-off time (%s) - exiting all\n", now.Format("15:04"))

	mu.Lock()
	defer mu.Unlock()

	for sym, pos := range longPositions {
		ltp, _ := client.GetLTP("NSE", symbolToToken[sym])
		exitLong(sym, ltp, pos.Qty, "EOD Square-off")
	}

	for sym, pos := range shortPositions {
		ltp, _ := client.GetLTP("NSE", symbolToToken[sym])
		exitShort(sym, ltp, pos.Qty, "EOD Square-off")
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

	if len(saved.Map) != len(stocks.Tickers) {
		return false
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
