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
	})
	shortPositions = make(map[string]struct {
		EntryPrice, LowestPrice float64
		Qty                     int
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

func main() {

	fmt.Println("Axiom Protocol Initializing")
	config.Load()

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
	fmt.Println("Mapping symbols to tokens")

	if loadSavedTokenMap() {
		fmt.Println("Loaded existing token map from file")
	} else {
		// CRITICAL FIX: Re-authenticate just before mapping (token might have expired)
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

	// Initial load of brain config
	if err := loadBrainConfig(); err != nil {
		log.Printf("Warning: Could not load config.json - using defaults: %v", err)
	} else {
		fmt.Printf("Loaded Trading Strategies\n", len(stockStrategies))
	}

	if len(symbolToToken) > 0 {
		var firstSym, firstToken string
		for s, t := range symbolToToken {
			firstSym = s
			firstToken = t
			break
		}
		_, err := client.GetLTP("NSE", firstToken)
		if err != nil {
			log.Printf("Immediate LTP test for %s failed: %v", firstSym, err)
		}
	}

	fmt.Println("Axiom Protocol Online")

	// Main polling loop
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	lastBrainUpdate := time.Now()

	for range ticker.C {
		now := time.Now().In(time.FixedZone("IST", 5*60*60+30*60))

		// Auto square-off at 15:10 IST (but continue running)
		if now.Hour() == 15 && now.Minute() >= 10 {
			squareOffAllPositions(now)
		}

		// Run brain.py and reload config every 15 minutes
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
			successCount++

			updateHighLow(sym, ltp)
			updateLTPHistory(sym, ltp)
			checkAllEntries(sym, ltp)
			checkLongExit(sym, ltp)
			checkShortExit(sym, ltp)

			time.Sleep(200 * time.Millisecond)
		}

		fmt.Printf("Successfully fetched LTP\n", successCount, len(symbolToToken))
		fmt.Println("---")
	}
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

// Load config.json from data/config.json
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

// Get strategy with fallback
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

// Update day high/low
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

// Update LTP history for bounce/quick drop
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

// ──────────────────────────────────────────────────────────────────────────────
// Entry logic using per-stock thresholds from config
// ──────────────────────────────────────────────────────────────────────────────

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

// ──────────────────────────────────────────────────────────────────────────────
// Entry / Exit helpers
// ──────────────────────────────────────────────────────────────────────────────

func enterLong(sym string, ltp float64, leverage float64) {
	effectiveBudget := defaultBudget * leverage
	qty := int(effectiveBudget / ltp)
	if qty < 1 {
		fmt.Printf("Budget too low for long %s (leverage %.1f)\n", sym, leverage)
		return
	}

	err := client.PlaceOrder(sym, symbolToToken[sym], "BUY", "MKT", qty)
	if err != nil {
		log.Printf("Long entry failed %s: %v", sym, err)
		return
	}

	mu.Lock()
	longPositions[sym] = struct {
		EntryPrice   float64
		HighestPrice float64
		Qty          int
	}{ltp, ltp, qty}
	mu.Unlock()

	fmt.Printf("Entered LONG %s: Qty %d @ %.2f (leverage %.1f)\n", sym, qty, ltp, leverage)
}

func enterShort(sym string, ltp float64, leverage float64) {
	effectiveBudget := defaultBudget * leverage
	qty := int(effectiveBudget / ltp)
	if qty < 1 {
		fmt.Printf("Budget too low for short %s (leverage %.1f)\n", sym, leverage)
		return
	}

	err := client.PlaceOrder(sym, symbolToToken[sym], "SELL", "MKT", qty)
	if err != nil {
		log.Printf("Short entry failed %s: %v", sym, err)
		return
	}

	mu.Lock()
	shortPositions[sym] = struct {
		EntryPrice  float64
		LowestPrice float64
		Qty         int
	}{ltp, ltp, qty}
	mu.Unlock()

	fmt.Printf("Entered SHORT %s: Qty %d @ %.2f (leverage %.1f)\n", sym, qty, ltp, leverage)
}

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
		fmt.Printf("LONG FIXED SL hit %s @ %.2f (SL %.3f)\n", sym, ltp, strat.SL)
		exitLong(sym, ltp, pos.Qty)
		return
	}

	target := pos.EntryPrice * (1 + strat.Target)
	if ltp >= target {
		fmt.Printf("LONG TARGET hit %s @ %.2f (target %.3f)\n", sym, ltp, strat.Target)
		exitLong(sym, ltp, pos.Qty)
		return
	}

	trailingSL := pos.HighestPrice * (1 - defaultTrailingPercent/100)
	if ltp <= trailingSL {
		fmt.Printf("LONG TRAILING SL %s @ %.2f\n", sym, ltp)
		exitLong(sym, ltp, pos.Qty)
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
		fmt.Printf("SHORT FIXED SL hit %s @ %.2f (SL %.3f)\n", sym, ltp, strat.SL)
		exitShort(sym, ltp, pos.Qty)
		return
	}

	target := pos.EntryPrice * (1 - strat.Target)
	if ltp <= target {
		fmt.Printf("SHORT TARGET hit %s @ %.2f (target %.3f)\n", sym, ltp, strat.Target)
		exitShort(sym, ltp, pos.Qty)
		return
	}

	trailingSL := pos.LowestPrice * (1 + defaultTrailingPercent/100)
	if ltp >= trailingSL {
		fmt.Printf("SHORT TRAILING SL %s @ %.2f\n", sym, ltp)
		exitShort(sym, ltp, pos.Qty)
	}
}

func exitLong(sym string, ltp float64, qty int) {
	err := client.PlaceOrder(sym, symbolToToken[sym], "SELL", "MKT", qty)
	if err != nil {
		log.Printf("Long exit failed %s: %v", sym, err)
		return
	}
	fmt.Printf("Exited LONG %s: Qty %d @ %.2f\n", sym, qty, ltp)

	mu.Lock()
	delete(longPositions, sym)
	mu.Unlock()
}

func exitShort(sym string, ltp float64, qty int) {
	err := client.PlaceOrder(sym, symbolToToken[sym], "BUY", "MKT", qty)
	if err != nil {
		log.Printf("Short exit failed %s: %v", sym, err)
		return
	}
	fmt.Printf("Exited SHORT %s: Qty %d @ %.2f\n", sym, qty, ltp)

	mu.Lock()
	delete(shortPositions, sym)
	mu.Unlock()
}

func squareOffAllPositions(now time.Time) {
	fmt.Printf("Square-off time (%s) - exiting all\n", now.Format("15:04"))

	mu.Lock()
	defer mu.Unlock()

	for sym, pos := range longPositions {
		ltp, _ := client.GetLTP("NSE", symbolToToken[sym])
		exitLong(sym, ltp, pos.Qty)
	}

	for sym, pos := range shortPositions {
		ltp, _ := client.GetLTP("NSE", symbolToToken[sym])
		exitShort(sym, ltp, pos.Qty)
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
