package main

import (
	"fmt"
	"log"

	"github.com/may-bach/Axiom/internal/auth"
	"github.com/may-bach/Axiom/internal/config"
)

func main() {
	config.Load()

	fmt.Println("🚀 Axiom Protocol Initializing...")

	token, err := auth.GetSessionToken(config.APIKey, config.RequestCode, config.SecretKey)
	if err != nil {
		log.Fatalf("Auth failed: %v", err)
	}

	fmt.Printf("✅ Success! Session Token (jKey): %s\n", token)
	fmt.Println("Client:", config.UserID)
	fmt.Println("\nNext steps: Add API client wrapper & strategy")
}
