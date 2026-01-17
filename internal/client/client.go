// internal/client/client.go
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

	"github.com/may-bach/Axiom/internal/auth"
	"github.com/may-bach/Axiom/internal/config"
	"github.com/may-bach/Axiom/internal/session"
)

const (
	BaseURL = "https://piconnect.flattrade.in/PiConnectTP"
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

func MakeRequest(endpoint string, payload map[string]string) ([]byte, error) {
	token := session.Get()
	if token == "" {
		return nil, fmt.Errorf("no session token - authenticate first")
	}

	// Load UID from .env (required!)
	uid := os.Getenv("FLAT_USER_ID")
	if uid == "" {
		return nil, fmt.Errorf("FLAT_USER_ID missing in .env")
	}

	// Inject common fields
	payload["uid"] = uid
	payload["actid"] = uid
	payload["source"] = "API"

	jsonBody, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	finalBody := "jData=" + string(jsonBody) + "&jKey=" + token

	url := BaseURL + endpoint

	// Create request with timeout
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

	if strings.Contains(raw, "Session Expired") ||
		strings.Contains(raw, "Invalid Session") ||
		strings.Contains(raw, "Invalid User Id") ||
		strings.Contains(raw, "Not_Ok") {

		// Re-authenticate
		newToken, authErr := auth.GetSessionToken(config.C.APIKey, config.C.RequestCode, config.C.SecretKey)
		if authErr != nil {
			return nil, fmt.Errorf("re-auth failed: %v", authErr)
		}

		session.Set(newToken)

		// Retry with new token
		payload["jKey"] = newToken // update payload (though not strictly needed)
		jsonBody, _ = json.Marshal(payload)
		finalBody = "jData=" + string(jsonBody) + "&jKey=" + newToken

		req, _ = http.NewRequest("POST", url, bytes.NewBuffer([]byte(finalBody)))
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

		resp, err = client.Do(req)
		if err != nil {
			return nil, fmt.Errorf("retry request failed: %v", err)
		}
		defer resp.Body.Close()

		body, err = io.ReadAll(resp.Body)
		if err != nil {
			return nil, err
		}

		raw = string(body)
	}

	return body, nil
}

func SearchScrip(exch, searchText string) ([]byte, error) {
	payload := map[string]string{
		"exch":  exch,
		"stext": searchText,
	}
	respBytes, err := MakeRequest("/SearchScrip", payload)
	if err != nil {
		return nil, err
	}
	return respBytes, nil
}

type TouchlineResponse struct {
	Stat string `json:"stat"`
	Lp   string `json:"lp"`  // Last Price
	Ltp  string `json:"ltp"` // fallback
	Emsg string `json:"emsg"`
}

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

type OrderResponse struct {
	Stat       string `json:"stat"`
	Emsg       string `json:"emsg"`
	NorenOrdNo string `json:"norenordno"`
}

func PlaceOrder(sym, token, buySell, orderType string, qty int) error {
	payload := map[string]string{
		"exch":     "NSE",
		"tsym":     sym + "-EQ",
		"qty":      fmt.Sprint(qty),
		"prc":      "0", // market order
		"prd":      "C", // CNC
		"trgprc":   "0",
		"prctyp":   orderType, // "MKT"
		"ret":      "DAY",
		"trantype": buySell, // "B" or "S"
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
