package config

import (
	"log"
	"os"

	"github.com/joho/godotenv"
)

var (
	UserID      string
	APIKey      string
	SecretKey   string
	RedirectURL string
	RequestCode string
	MaxTrades   int
	Budget      float64
)

func Load() {
	_ = godotenv.Load()

	UserID = os.Getenv("FLAT_USER_ID")
	APIKey = os.Getenv("FLAT_API_KEY")
	SecretKey = os.Getenv("FLAT_SECRET_KEY")
	RedirectURL = os.Getenv("FLAT_REDIRECT_URL")
	RequestCode = os.Getenv("FLAT_REQUEST_CODE")

	if UserID == "" || APIKey == "" || SecretKey == "" {
		log.Fatal("Missing core credentials in .env")
	}
}
