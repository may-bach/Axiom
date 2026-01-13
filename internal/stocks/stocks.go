package stocks

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// Tickers is globally accessible list of symbols
var Tickers []string

// Load reads and validates stocks.json
func Load(filePath string) error {
	// Default path if empty
	if filePath == "" {
		filePath = filepath.Join("data", "stocks.json")
	}

	// Check existence
	if _, err := os.Stat(filePath); os.IsNotExist(err) {
		return fmt.Errorf("stocks file not found at: %s", filePath)
	}

	data, err := os.ReadFile(filePath)
	if err != nil {
		return fmt.Errorf("cannot read stocks file: %v", err)
	}

	var config struct {
		Tickers []string `json:"tickers"`
	}

	if err := json.Unmarshal(data, &config); err != nil {
		return fmt.Errorf("invalid JSON format in stocks.json: %v", err)
	}

	if len(config.Tickers) == 0 {
		return fmt.Errorf("no tickers found in stocks.json")
	}

	Tickers = config.Tickers
	fmt.Printf("Loaded %d stocks to monitor\n", len(Tickers))

	return nil
}
