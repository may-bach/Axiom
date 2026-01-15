package config

import (
	"fmt"
	"log"
	"os"

	"github.com/joho/godotenv"
)

type Config struct {
	APIKey      string
	RequestCode string
	SecretKey   string
}

var C Config

func Load() {
	err := godotenv.Load()
	if err != nil {
		log.Printf("Warning: Error loading .env file: %v", err)
	}

	C.APIKey = os.Getenv("FLAT_API_KEY")
	C.RequestCode = os.Getenv("FLAT_REQUEST_CODE")
	C.SecretKey = os.Getenv("FLAT_SECRET_KEY")

	if C.APIKey == "" || C.RequestCode == "" || C.SecretKey == "" {
		log.Fatal("Missing core credentials in .env (FLAT_API_KEY, FLAT_REQUEST_CODE, FLAT_SECRET_KEY)")
	}

	fmt.Println("Configuration loaded successfully")
}
