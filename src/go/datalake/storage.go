package datalake

import (
	"errors"
	"time"
)

var ErrNotFound = errors.New("Book not found in datalake")

type Storage interface {
	Write(bookID int, header, body string) (string, string, error)
	Lookup(bookID int) (string, string, error)
	ListNew(since time.Time) ([]int, error)
}
