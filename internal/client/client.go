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

func MakeRequest(endpoint string, payload map[string]string) ([]byte, error) {
	token := session.Get()
	if token == "" {
		return nil, fmt.Errorf("no session token - authenticate first")
	}

	// Inject common auth fields into the JSON part
	payload["uid"] = os.Getenv("FLAT_USER_ID")
	payload["actid"] = payload["uid"]
	payload["source"] = "API"

	jsonBody, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	finalBody := "jData=" + string(jsonBody) + "&jKey=" + token

	url := BaseURL + endpoint

	req, err := http.NewRequest("POST", url, bytes.NewBuffer([]byte(finalBody)))
	if err != nil {
		return nil, err
	}

	// Flattrade expects this header for form data
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if strings.Contains(string(body), "Invalid Session Key") || strings.Contains(string(body), "Connection failed") {
		// Re-authenticate
		newToken, err := auth.GetSessionToken(config.APIKey, config.RequestCode, config.SecretKey)
		if err == nil {
			session.Set(newToken)
			payload["jKey"] = newToken
		}
	}

	return body, nil

}

type SearchResult struct {
	Stat   string `json:"stat"`
	Values []struct {
		Tsym  string `json:"tsym"`
		Token string `json:"token"`
	} `json:"values"`
}

func SearchScrip(exch, searchText string) ([]byte, error) {
	payload := map[string]string{
		"exch":  exch,
		"stext": searchText,
	}
	return MakeRequest("/SearchScrip", payload)
}

type TouchlineResponse struct {
	Stat string `json:"stat"`
	Lp   string `json:"lp"` // Last Price as string
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
	fmt.Printf("DEBUG GetQuotes raw for token %s: %s\n", token, raw)

	var qr struct {
		Stat string `json:"stat"`
		Lp   string `json:"lp"`
		Ltp  string `json:"ltp"` // fallback
		Emsg string `json:"emsg"`
	}

	if err := json.Unmarshal(respBytes, &qr); err != nil {
		return 0, fmt.Errorf("JSON unmarshal failed: %v - raw: %s", err, raw)
	}

	// Safe check - only access fields if unmarshal succeeded
	if qr.Stat == "" || qr.Stat != "Ok" {
		return 0, fmt.Errorf("GetQuotes failed or invalid response: stat=%s emsg=%s - raw: %s", qr.Stat, qr.Emsg, raw)
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
	NorenOrdNo string `json:"norenordno"` // order ID
}

func PlaceOrder(sym, token, buySell, orderType string, qty int) error {
	payload := map[string]string{
		"exch":     "NSE",
		"tsym":     sym + "-EQ",
		"qty":      fmt.Sprint(qty),
		"prc":      "0", // market order
		"prd":      "C", // CNC for equity
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
	fmt.Printf("DEBUG PlaceOrder raw for %s: %s\n", sym, raw)

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
