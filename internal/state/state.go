package state

import (
	"sync"
	"time"

	"github.com/may-bach/Axiom/internal/models"
)

// Store provides encapsulated, thread-safe access to all in-memory bot state.
type Store struct {
	mu              sync.RWMutex
	highLow         map[string]models.HighLow
	baseRanges      map[string]models.BaseRange
	baseEstablished bool
	rsLeaders       map[string]bool
	rsLaggards      map[string]bool
	tradedToday     map[string]bool
	vwapMap         map[string]float64
	ltpHistory      map[string][]float64
	latestLTP       map[string]float64
	longPositions   map[string]models.Position
	shortPositions  map[string]models.Position
	tradeHistory    []models.TradeRecord
	dailyPnL        float64
	lastDailyReset  time.Time
	regime          models.MarketRegime
	account         models.AccountState
}

// NewStore initializes a new state Store.
func NewStore() *Store {
	return &Store{
		highLow:         make(map[string]models.HighLow),
		baseRanges:      make(map[string]models.BaseRange),
		rsLeaders:       make(map[string]bool),
		rsLaggards:      make(map[string]bool),
		tradedToday:     make(map[string]bool),
		vwapMap:         make(map[string]float64),
		ltpHistory:      make(map[string][]float64),
		latestLTP:       make(map[string]float64),
		longPositions:   make(map[string]models.Position),
		shortPositions:  make(map[string]models.Position),
		tradeHistory:    make([]models.TradeRecord, 0),
		lastDailyReset:  time.Now().AddDate(0, 0, -1),
		regime: models.MarketRegime{
			Status:         "NORMAL",
			Color:          "GREEN",
			MaxPositions:   3,
			PositionBudget: 15000.0,
			DailyLossLimit: -750.0,
		},
		account: models.AccountState{
			InitialCapital:   10000.0,
			CurrentBalance:   10000.0,
			PeakBalance:      10000.0,
			TotalRealizedPnL: 0.0,
			LastUpdated:      time.Now().Format("2006-01-02 15:04:05 IST"),
		},
	}
}

// ----------------------------------------------------------------------
// High / Low
// ----------------------------------------------------------------------

func (s *Store) UpdateHighLow(sym string, ltp float64) {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.latestLTP[sym] = ltp

	hl, exists := s.highLow[sym]
	if !exists {
		hl = models.HighLow{High: ltp, Low: ltp}
	} else {
		if hl.High == 0 || ltp > hl.High {
			hl.High = ltp
		}
		if hl.Low == 0 || ltp < hl.Low {
			hl.Low = ltp
		}
	}
	s.highLow[sym] = hl
}

func (s *Store) UpdateLTP(sym string, ltp float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.latestLTP[sym] = ltp
}

func (s *Store) GetLTP(sym string) float64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.latestLTP[sym]
}

func (s *Store) GetHighLow(sym string) (models.HighLow, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	hl, ok := s.highLow[sym]
	return hl, ok
}

// ----------------------------------------------------------------------
// Base Range & Relative Strength (RS)
// ----------------------------------------------------------------------

func (s *Store) SetBaseRange(sym string, open, high, low, retPct float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.baseRanges[sym] = models.BaseRange{
		OpenPrice: open,
		High:      high,
		Low:       low,
		ReturnPct: retPct,
	}
}

func (s *Store) GetBaseRange(sym string) (models.BaseRange, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	br, ok := s.baseRanges[sym]
	return br, ok
}

func (s *Store) GetAllBaseRanges() map[string]models.BaseRange {
	s.mu.RLock()
	defer s.mu.RUnlock()
	res := make(map[string]models.BaseRange, len(s.baseRanges))
	for k, v := range s.baseRanges {
		res[k] = v
	}
	return res
}

func (s *Store) SetBaseEstablished(established bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.baseEstablished = established
}

func (s *Store) IsBaseEstablished() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.baseEstablished
}

func (s *Store) SetRSRanks(leaders map[string]bool, laggards map[string]bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.rsLeaders = leaders
	s.rsLaggards = laggards
}

func (s *Store) IsRSLeader(sym string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.rsLeaders[sym]
}

func (s *Store) IsRSLaggard(sym string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.rsLaggards[sym]
}

func (s *Store) MarkSymbolTradedToday(sym string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tradedToday[sym] = true
}

func (s *Store) HasSymbolTradedToday(sym string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.tradedToday[sym]
}

func (s *Store) SetVWAP(sym string, vwap float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.vwapMap[sym] = vwap
}

func (s *Store) GetVWAP(sym string) float64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.vwapMap[sym]
}

// GetMarketBreadth calculates real-time advance/decline metrics across the active universe.
// Returns advancePct (0.0 to 100.0), avgReturn (percentage), and totalCount.
func (s *Store) GetMarketBreadth() (advancePct, avgReturn float64, totalCount int) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if len(s.baseRanges) == 0 {
		return 50.0, 0.0, 0
	}

	advancing := 0
	var sumReturn float64

	for sym, br := range s.baseRanges {
		if br.OpenPrice <= 0 {
			continue
		}
		ltp, ok := s.latestLTP[sym]
		if !ok || ltp <= 0 {
			ltp = br.OpenPrice
		}
		ret := ((ltp - br.OpenPrice) / br.OpenPrice) * 100.0
		sumReturn += ret
		if ret >= 0 {
			advancing++
		}
		totalCount++
	}

	if totalCount == 0 {
		return 50.0, 0.0, 0
	}

	advancePct = (float64(advancing) / float64(totalCount)) * 100.0
	avgReturn = sumReturn / float64(totalCount)
	return advancePct, avgReturn, totalCount
}

// ----------------------------------------------------------------------
// Tick History
// ----------------------------------------------------------------------

func (s *Store) AppendHistory(sym string, ltp float64, window int) {
	s.mu.Lock()
	defer s.mu.Unlock()

	hist := s.ltpHistory[sym]
	hist = append(hist, ltp)
	if len(hist) > window {
		hist = hist[1:]
	}
	s.ltpHistory[sym] = hist
}

func (s *Store) GetHistory(sym string) []float64 {
	s.mu.RLock()
	defer s.mu.RUnlock()

	hist := s.ltpHistory[sym]
	res := make([]float64, len(hist))
	copy(res, hist)
	return res
}

// ----------------------------------------------------------------------
// Positions
// ----------------------------------------------------------------------

func (s *Store) GetOpenCount() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.longPositions) + len(s.shortPositions)
}

func (s *Store) HasPosition(sym string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	_, isLong := s.longPositions[sym]
	_, isShort := s.shortPositions[sym]
	return isLong || isShort
}

func (s *Store) GetLongPosition(sym string) (models.Position, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	pos, ok := s.longPositions[sym]
	return pos, ok
}

func (s *Store) GetShortPosition(sym string) (models.Position, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	pos, ok := s.shortPositions[sym]
	return pos, ok
}

func (s *Store) OpenLong(sym string, ltp float64, qty int, t time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.longPositions[sym] = models.Position{
		Symbol:       sym,
		Direction:    "LONG",
		EntryPrice:   ltp,
		HighestPrice: ltp,
		LowestPrice:  ltp,
		Qty:          qty,
		EntryTime:    t,
	}
}

func (s *Store) OpenShort(sym string, ltp float64, qty int, t time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.shortPositions[sym] = models.Position{
		Symbol:       sym,
		Direction:    "SHORT",
		EntryPrice:   ltp,
		HighestPrice: ltp,
		LowestPrice:  ltp,
		Qty:          qty,
		EntryTime:    t,
	}
}

func (s *Store) UpdateLongHighest(sym string, ltp float64) (models.Position, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	pos, ok := s.longPositions[sym]
	if !ok {
		return pos, false
	}
	if ltp > pos.HighestPrice {
		pos.HighestPrice = ltp
		s.longPositions[sym] = pos
	}
	return pos, true
}

func (s *Store) UpdateShortLowest(sym string, ltp float64) (models.Position, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	pos, ok := s.shortPositions[sym]
	if !ok {
		return pos, false
	}
	if ltp < pos.LowestPrice {
		pos.LowestPrice = ltp
		s.shortPositions[sym] = pos
	}
	return pos, true
}

// CloseLong atomically removes and returns the long position.
func (s *Store) CloseLong(sym string) (models.Position, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	pos, ok := s.longPositions[sym]
	if ok {
		delete(s.longPositions, sym)
	}
	return pos, ok
}

// CloseShort atomically removes and returns the short position.
func (s *Store) CloseShort(sym string) (models.Position, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	pos, ok := s.shortPositions[sym]
	if ok {
		delete(s.shortPositions, sym)
	}
	return pos, ok
}

func (s *Store) GetAllOpenPositions() []models.Position {
	s.mu.RLock()
	defer s.mu.RUnlock()

	all := make([]models.Position, 0, len(s.longPositions)+len(s.shortPositions))
	for _, p := range s.longPositions {
		all = append(all, p)
	}
	for _, p := range s.shortPositions {
		all = append(all, p)
	}
	return all
}

// ----------------------------------------------------------------------
// Trade History & Daily P&L
// ----------------------------------------------------------------------

func (s *Store) RecordTrade(t models.TradeRecord) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tradeHistory = append(s.tradeHistory, t)
	s.dailyPnL += t.PnL
}

func (s *Store) GetDailyPnL() float64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.dailyPnL
}

func (s *Store) GetTradeHistory() []models.TradeRecord {
	s.mu.RLock()
	defer s.mu.RUnlock()
	res := make([]models.TradeRecord, len(s.tradeHistory))
	copy(res, s.tradeHistory)
	return res
}

func (s *Store) ResetDaily(resetTime time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lastDailyReset = resetTime
	s.tradeHistory = nil
	s.dailyPnL = 0
	s.highLow = make(map[string]models.HighLow)
	s.baseRanges = make(map[string]models.BaseRange)
	s.baseEstablished = false
	s.rsLeaders = make(map[string]bool)
	s.rsLaggards = make(map[string]bool)
	s.tradedToday = make(map[string]bool)
	s.vwapMap = make(map[string]float64)
	s.ltpHistory = make(map[string][]float64)
	s.latestLTP = make(map[string]float64)
}

func (s *Store) GetLastReset() time.Time {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.lastDailyReset
}

// ----------------------------------------------------------------------
// Market Regime
// ----------------------------------------------------------------------

func (s *Store) SetRegime(r models.MarketRegime) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.regime = r
}

func (s *Store) GetRegime() models.MarketRegime {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.regime
}

func (s *Store) GetStockDirective(sym string) string {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if s.regime.StockDirectives != nil {
		if directive, ok := s.regime.StockDirectives[sym]; ok && directive != "" {
			return directive
		}
	}
	return "NEUTRAL"
}

// ----------------------------------------------------------------------
// Account & Capital Compounding
// ----------------------------------------------------------------------

func (s *Store) SetAccount(acc models.AccountState) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.account = acc
}

func (s *Store) GetAccount() models.AccountState {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.account
}

func (s *Store) UpdateAccountDaily(dailyPnL float64, dateStr, updatedTime string) (models.AccountState, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if s.account.LastCompoundedDate == dateStr {
		return s.account, false
	}

	s.account.CurrentBalance += dailyPnL
	s.account.TotalRealizedPnL += dailyPnL
	if s.account.CurrentBalance > s.account.PeakBalance {
		s.account.PeakBalance = s.account.CurrentBalance
	}
	s.account.LastCompoundedDate = dateStr
	s.account.LastUpdated = updatedTime
	return s.account, true
}

func (s *Store) GetPositionBudget(leverage float64) float64 {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if s.regime.Status == "CRISIS" || s.regime.Status == "CALM_STAND_DOWN" || s.regime.MaxPositions == 0 {
		return 0.0
	}

	bal := s.account.CurrentBalance
	if bal <= 0 {
		bal = 10000.0
	}

	if s.regime.Status == "CAUTION" {
		// Caution regime: up to 2 positions allowed, 1.0x balance per position
		return bal * 1.0 * leverage
	}

	// Normal regime: 1.5x balance sizing per trade (for 2-3 concurrent trades under 5x margin)
	return bal * 1.5 * leverage
}

func (s *Store) GetDailyLossLimit() float64 {
	s.mu.RLock()
	defer s.mu.RUnlock()

	bal := s.account.CurrentBalance
	if bal <= 0 {
		bal = 10000.0
	}
	// Hard 7.5% daily stop-loss floor
	return -0.075 * bal
}
