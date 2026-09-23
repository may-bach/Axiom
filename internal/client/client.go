package client

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/may-bach/Axiom/internal/models"
	"github.com/may-bach/Axiom/internal/session"
)

const (
	BaseURL = "https://piconnect.flattrade.in/PiConnectAPI"
)

type APIResponse struct {
	Stat string `json:"stat"`
	Emsg string `json:"emsg"`
}

type SearchResult struct {
	Stat   string `json:"stat"`
	Values []struct {
		Tsym  string `json:"tsym"`
		Token string `json:"token"`
	} `json:"values"`
}

type TouchlineResponse struct {
	Stat string `json:"stat"`
	Lp   string `json:"lp"`  // Last Price
	Ltp  string `json:"ltp"` // fallback
	Ap   string `json:"ap"`  // Average Price (NSE Intraday VWAP)
	O    string `json:"o"`   // Open
	H    string `json:"h"`   // High
	L    string `json:"l"`   // Low
	C    string `json:"c"`   // Close
	V    string `json:"v"`   // Traded Volume
	Emsg string `json:"emsg"`
}

type OrderResponse struct {
	Stat       string `json:"stat"`
	Emsg       string `json:"emsg"`
	NorenOrdNo string `json:"norenordno"`
}

// MakeRequest is the core function for all API calls
func MakeRequest(endpoint string, payload map[string]string) ([]byte, error) {
	token := session.Get()
	if token == "" {
		return nil, fmt.Errorf("no session token - authenticate first")
	}

	uid := os.Getenv("FLAT_USER_ID")
	if uid == "" {
		return nil, fmt.Errorf("FLAT_USER_ID missing in .env")
	}

	payload["uid"] = uid
	payload["actid"] = uid
	payload["source"] = "API"

	jsonBody, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	finalBody := "jData=" + string(jsonBody) + "&jKey=" + token

	url := BaseURL + endpoint

	client := &http.Client{Timeout: 10 * time.Second}
	req, err := http.NewRequest("POST", url, bytes.NewBuffer([]byte(finalBody)))
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %v", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	raw := string(body)

	// Check for session/token errors
	if strings.Contains(raw, "Session Expired") ||
		strings.Contains(raw, "Invalid Session") ||
		strings.Contains(raw, "Invalid User Id") ||
		strings.Contains(raw, "Not_Ok") {

		// DO NOT re-auth automatically here — return error so caller can handle
		// (re-auth should only happen at startup or manual restart with fresh request_code)
		return nil, fmt.Errorf("api call failed - possible session issue: %s - raw: %s", raw, raw)
	}

	return body, nil
}

// SearchScrip
func SearchScrip(exch, searchText string) ([]byte, error) {
	payload := map[string]string{
		"exch":  exch,
		"stext": searchText,
	}
	return MakeRequest("/SearchScrip", payload)
}

// GetLTP - no re-auth inside
func GetLTP(exch, token string) (float64, error) {
	payload := map[string]string{
		"exch":  exch,
		"token": token,
	}

	respBytes, err := MakeRequest("/GetQuotes", payload)
	if err != nil {
		return 0, err
	}

	raw := string(respBytes)

	var qr TouchlineResponse
	if err := json.Unmarshal(respBytes, &qr); err != nil {
		return 0, fmt.Errorf("JSON unmarshal failed: %v - raw: %s", err, raw)
	}

	if qr.Stat != "Ok" {
		return 0, fmt.Errorf("GetQuotes failed: stat=%s emsg=%s - raw: %s", qr.Stat, qr.Emsg, raw)
	}

	priceStr := qr.Lp
	if priceStr == "" {
		priceStr = qr.Ltp
	}
	if priceStr == "" {
		return 0, fmt.Errorf("no price field found - raw: %s", raw)
	}

	ltp, err := strconv.ParseFloat(priceStr, 64)
	if err != nil {
		return 0, fmt.Errorf("price parse error: %v - value: %s", err, priceStr)
	}

	return ltp, nil
}

// GetQuoteDetails retrieves full quote including LTP, VWAP (ap), Open, High, Low, and Volume
func GetQuoteDetails(exch, token string) (models.QuoteData, error) {
	payload := map[string]string{
		"exch":  exch,
		"token": token,
	}

	respBytes, err := MakeRequest("/GetQuotes", payload)
	if err != nil {
		return models.QuoteData{}, err
	}

	raw := string(respBytes)

	var qr TouchlineResponse
	if err := json.Unmarshal(respBytes, &qr); err != nil {
		return models.QuoteData{}, fmt.Errorf("JSON unmarshal failed: %v - raw: %s", err, raw)
	}

	if qr.Stat != "Ok" {
		return models.QuoteData{}, fmt.Errorf("GetQuotes failed: stat=%s emsg=%s - raw: %s", qr.Stat, qr.Emsg, raw)
	}

	priceStr := qr.Lp
	if priceStr == "" {
		priceStr = qr.Ltp
	}
	if priceStr == "" {
		return models.QuoteData{}, fmt.Errorf("no price field found - raw: %s", raw)
	}

	ltp, err := strconv.ParseFloat(priceStr, 64)
	if err != nil {
		return models.QuoteData{}, fmt.Errorf("price parse error: %v - value: %s", err, priceStr)
	}

	vwap, _ := strconv.ParseFloat(qr.Ap, 64)
	if vwap == 0 {
		vwap = ltp
	}

	open, _ := strconv.ParseFloat(qr.O, 64)
	if open == 0 {
		open = ltp
	}

	high, _ := strconv.ParseFloat(qr.H, 64)
	if high == 0 {
		high = ltp
	}

	low, _ := strconv.ParseFloat(qr.L, 64)
	if low == 0 {
		low = ltp
	}

	vol, _ := strconv.ParseInt(qr.V, 10, 64)

	return models.QuoteData{
		LTP:    ltp,
		VWAP:   vwap,
		Open:   open,
		High:   high,
		Low:    low,
		Volume: vol,
	}, nil
}

// PlaceOrder - no re-auth inside
func PlaceOrder(sym, token, buySell, orderType string, qty int) error {
	tranType := "B"
	upperSide := strings.ToUpper(strings.TrimSpace(buySell))
	if upperSide == "SELL" || upperSide == "S" {
		tranType = "S"
	}

	payload := map[string]string{
		"exch":     "NSE",
		"tsym":     sym + "-EQ",
		"qty":      fmt.Sprint(qty),
		"prc":      "0", // market
		"prd":      "M", // MIS for intraday trading
		"trgprc":   "0",
		"prctyp":   orderType,
		"ret":      "DAY",
		"trantype": tranType,
	}

	respBytes, err := MakeRequest("/PlaceOrder", payload)
	if err != nil {
		return err
	}

	raw := string(respBytes)

	var or OrderResponse
	if err := json.Unmarshal(respBytes, &or); err != nil {
		return fmt.Errorf("order unmarshal failed: %v - raw: %s", err, raw)
	}

	if or.Stat != "Ok" {
		return fmt.Errorf("place order failed: %s - raw: %s", or.Emsg, raw)
	}

	fmt.Printf("Order placed successfully for %s - Order ID: %s\n", sym, or.NorenOrdNo)
	return nil
}

type LimitsResponse struct {
	Stat       string `json:"stat"`
	Cash       string `json:"cash"`
	MarginUsed string `json:"marginused"`
	Payin      string `json:"payin"`
	Emsg       string `json:"emsg"`
}

// GetLimits queries Flattrade for current available cash margin.
func GetLimits() (float64, error) {
	uid := os.Getenv("FLAT_USER_ID")
	payload := map[string]string{
		"actid": uid,
	}

	respBytes, err := MakeRequest("/Limits", payload)
	if err != nil {
		return 0, err
	}

	var lr LimitsResponse
	if err := json.Unmarshal(respBytes, &lr); err != nil {
		return 0, fmt.Errorf("limits unmarshal failed: %v - raw: %s", err, string(respBytes))
	}

	if lr.Stat != "Ok" {
		return 0, fmt.Errorf("limits query failed: %s", lr.Emsg)
	}

	cashVal, err := strconv.ParseFloat(strings.TrimSpace(lr.Cash), 64)
	if err != nil {
		return 0, fmt.Errorf("invalid cash value '%s': %v", lr.Cash, err)
	}

	return cashVal, nil
}

// CheckAvailableMargin verifies available cash against required margin.
func CheckAvailableMargin(required float64) (bool, float64, error) {
	available, err := GetLimits()
	if err != nil {
		return false, 0, err
	}
	return available >= required, available, nil
}
