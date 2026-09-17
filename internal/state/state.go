package state

import (
	"sync"

	"github.com/may-bach/Axiom/internal/models"
)

// Store provides thread-safe access to bot state
type Store struct {
	mu             sync.RWMutex
	LongPositions  map[string]models.Position
	ShortPositions map[string]models.Position
	TradeHistory   []models.TradeRecord
	DailyPnL       float64
}

// NewStore initializes a new state Store
func NewStore() *Store {
	return &Store{
		LongPositions:  make(map[string]models.Position),
		ShortPositions: make(map[string]models.Position),
		TradeHistory:   make([]models.TradeRecord, 0),
	}
}
