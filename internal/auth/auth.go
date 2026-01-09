package auth

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
)

type TokenResponse struct {
	Token  string `json:"token"`
	Client string `json:"client"`
	Status string `json:"status"`
	Emsg   string `json:"emsg"`
}

func GetSessionToken(apiKey, requestCode, apiSecret string) (string, error) {
	if requestCode == "" {
		return "", fmt.Errorf("request_code required - get fresh one from browser daily")
	}

	// IMPORTANT: hash = SHA256(api_key + request_code + api_secret)
	input := apiKey + requestCode + apiSecret
	hash := sha256.Sum256([]byte(input))
	securityKey := hex.EncodeToString(hash[:])

	payload := map[string]string{
		"api_key":      apiKey,
		"request_code": requestCode,
		"api_secret":   securityKey,
	}

	bodyBytes, _ := json.Marshal(payload)

	req, _ := http.NewRequest("POST", "https://authapi.flattrade.in/trade/apitoken", bytes.NewBuffer(bodyBytes))
	req.Header.Set("Content-Type", "application/json")

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	var tr TokenResponse
	if err := json.Unmarshal(body, &tr); err != nil {
		return "", fmt.Errorf("invalid JSON: %v - raw: %s", err, string(body))
	}

	if tr.Status == "Ok" {
		return tr.Token, nil
	}

	return "", fmt.Errorf("failed: status=%s emsg=%s raw=%s", tr.Status, tr.Emsg, string(body))
}
