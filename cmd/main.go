package main

import (
	"encoding/json"
	"fmt"
	"log"
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

	// Day high/low tracking
	highLow = make(map[string]struct{ High, Low float64 })

	// Recent LTP history (for bounce/quick drop) - last few values
	ltpHistory = make(map[string][]float64)

	// Long positions
	longPositions = make(map[string]struct {
		EntryPrice   float64
		HighestPrice float64 // trailing SL
		Qty          int
	})

	// Short positions
	shortPositions = make(map[string]struct {
		EntryPrice  float64
		LowestPrice float64 // trailing SL for shorts
		Qty         int
	})

	// Configurable parameters
	budgetPerTrade         = 100000.0 // ₹ per trade
	maxConcurrentPositions = 8        // total long + short open at once
	bufferPercent          = 0.002    // 0.2% buffer for breakouts
	bounceReboundPercent   = 0.008    // 0.8% up move → bounce buy
	quickDropPercent       = 0.012    // 1.2% drop in one poll → quick short
	fixedSLPercent         = 1.0
	targetPercent          = 2.0
	trailingSLPercent      = 1.0
	historyWindow          = 3 // how many recent LTPs to keep
)

func main() {
	config.Load()
	fmt.Println("Axiom Protocol Initializing...")

	// Authenticate
	token, err := auth.GetSessionToken(config.APIKey, config.RequestCode, config.SecretKey)
	if err != nil {
		log.Fatalf("Auth failed: %v", err)
	}
	session.Set(token)
	fmt.Println("Session token set globally")

	// Load watchlist
	if err := stocks.Load(""); err != nil {
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
		for _, sym := range stocks.Tickers {
			respBytes, err := client.SearchScrip("NSE", sym)
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
					if v.Tsym == sym+"-EQ" {
						symbolToToken[sym] = v.Token
						fmt.Printf("Mapped %s → %s\n", sym, v.Token)
						found = true
						break
					}
				}
				if !found {
					fmt.Printf("No -EQ found for %s\n", sym)
				}
			} else {
				fmt.Printf("Search failed for %s: %s\n", sym, sr.Emsg)
			}

			time.Sleep(300 * time.Millisecond)
		}
		saveTokenMap()
	}

	fmt.Printf("Mapped %d/%d symbols successfully\n", len(symbolToToken), len(stocks.Tickers))

	// Quick LTP test after auth
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

	fmt.Println("Bot running. Press Ctrl+C to exit.")

	// Main polling loop
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	for range ticker.C {
		now := time.Now().In(time.FixedZone("IST", 5*60*60+30*60))
		if now.Hour() >= 15 && now.Minute() >= 10 {
			squareOffAllPositions(now)
			break
		}

		fmt.Printf("Polling LTP at %s\n", now.Format("15:04:05"))

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

// ──────────────────────────────────────────────────────────────────────────────
// Core helper functions
// ──────────────────────────────────────────────────────────────────────────────

func updateHighLow(sym string, ltp float64) {
	mu.Lock()
	defer mu.Unlock()
	hl := highLow[sym]
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
	hist := ltpHistory[sym]
	hist = append(hist, ltp)
	if len(hist) > historyWindow {
		hist = hist[1:]
	}
	ltpHistory[sym] = hist
}

func getTotalOpenPositions() int {
	return len(longPositions) + len(shortPositions)
}

// ──────────────────────────────────────────────────────────────────────────────
// Entry logic (all four signals)
// ──────────────────────────────────────────────────────────────────────────────

func checkAllEntries(sym string, ltp float64) {
	mu.Lock()
	totalOpen := getTotalOpenPositions()
	mu.Unlock()

	if totalOpen >= maxConcurrentPositions {
		fmt.Printf("Max positions (%d/%d) reached - skipping %s\n", totalOpen, maxConcurrentPositions, sym)
		return
	}

	checkBreakoutLong(sym, ltp)
	checkBounceBackBuy(sym, ltp)
	checkBreakdownShort(sym, ltp)
	checkQuickDropShort(sym, ltp)
}

func checkBreakoutLong(sym string, ltp float64) {
	mu.Lock()
	hl := highLow[sym]
	pos := longPositions[sym]
	mu.Unlock()

	if pos.EntryPrice > 0 {
		return
	}

	if hl.High > 0 && ltp > hl.High*(1+bufferPercent) {
		fmt.Printf("[LONG] Breakout BUY %s @ %.2f (high: %.2f)\n", sym, ltp, hl.High)
		enterLong(sym, ltp)
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
	if prev <= hl.Low*1.005 && // near low
		ltp >= prev*(1+bounceReboundPercent) { // strong bounce
		fmt.Printf("[LONG] Bounce BUY %s @ %.2f (prev: %.2f, low: %.2f)\n", sym, ltp, prev, hl.Low)
		enterLong(sym, ltp)
	}
}

func checkBreakdownShort(sym string, ltp float64) {
	mu.Lock()
	hl := highLow[sym]
	pos := shortPositions[sym]
	mu.Unlock()

	if pos.EntryPrice > 0 {
		return
	}

	if hl.Low > 0 && ltp < hl.Low*(1-bufferPercent) {
		fmt.Printf("[SHORT] Breakdown SELL %s @ %.2f (low: %.2f)\n", sym, ltp, hl.Low)
		enterShort(sym, ltp)
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
	if drop >= quickDropPercent {
		fmt.Printf("[SHORT] Quick Drop SELL %s @ %.2f (drop: %.2f%% from %.2f)\n", sym, ltp, drop*100, prev)
		enterShort(sym, ltp)
	}
}

// ──────────────────────────────────────────────────────────────────────────────
// Entry & Exit helpers (unchanged)
// ──────────────────────────────────────────────────────────────────────────────

func enterLong(sym string, ltp float64) {
	qty := int(budgetPerTrade / ltp)
	if qty < 1 {
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

	fmt.Printf("Entered LONG %s: Qty %d @ %.2f\n", sym, qty, ltp)
}

func enterShort(sym string, ltp float64) {
	qty := int(budgetPerTrade / ltp)
	if qty < 1 {
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

	fmt.Printf("Entered SHORT %s: Qty %d @ %.2f\n", sym, qty, ltp)
}

// (The rest of the code — checkLongExit, checkShortExit, exitLong, exitShort, squareOffAllPositions, loadSavedTokenMap, saveTokenMap, max/min helpers — remains exactly the same as in the previous full version)

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

// ... (add loadSavedTokenMap and saveTokenMap functions here as before)
