package core

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"
	"unicode"

	"golang.org/x/text/unicode/norm"
)

type Token struct {
	Value	string
	Position int
}

func Tokenize(body string, stopwords map[string]bool) ([]Token, int, int, string) {
	
	s := norm.NFKC.String(body)

	s = strings.ToLower(s)

	sNFD := norm.NFD.String(s)
	var sb strings.Builder
	for _, r := range sNFD {
		if !unicode.Is(unicode.Mn, r) {
			sb.WriteRune(r)
		}
	}
	s = norm.NFC.String(sb.String())

	runes := []rune(s)
	var rawTokens []Token
	var currentToken strings.Builder
	position := 0

	isWordChar := func(r rune) bool {
		return unicode.In(r, unicode.Lu, unicode.Ll, unicode.Lt, unicode.Lm, unicode.Lo, unicode.Nd)
	}
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		
		isInternalJoiner := false
		if r == '\'' || r == '\u2019' {
			if i > 0 && i < len(runes)-1 {
				if isWordChar(runes[i-1]) && isWordChar(runes[i+1]) {
					isInternalJoiner = true
				}
			}
		}
		if isWordChar(r) || isInternalJoiner {
			currentToken.WriteRune(r)
		} else {

			if currentToken.Len() > 0 {
				rawTokens = append(rawTokens, Token{
					Value:    currentToken.String(),
					Position: position,
				})
				position++
				currentToken.Reset()
			}
		}
	}
	
	if currentToken.Len() > 0 {
		rawTokens = append(rawTokens, Token{
			Value:    currentToken.String(),
			Position: position,
		})
	}
	nTokensRaw := len(rawTokens)

	var keptTokens []Token
	var keptTokensStrings []string
	isAllDigits := func(t string) bool {
		for _, r := range t {
			if !unicode.Is(unicode.Nd, r) {
				return false
			}
		}
		return true
	}
	for _, t := range rawTokens {
		tokenRunes := []rune(t.Value)
		
		if len(tokenRunes) < 2 {
			continue
		}

		if len(tokenRunes) > 40 {
			continue
		}

		if isAllDigits(t.Value) {
			continue
		}

		if stopwords[t.Value] {
			continue
		}
		keptTokens = append(keptTokens, t)
		keptTokensStrings = append(keptTokensStrings, t.Value)
	}
	nTokensKept := len(keptTokens)

	joined := strings.Join(keptTokensStrings, "\n")
	hash := sha256.Sum256([]byte(joined))
	sha256Tokens := hex.EncodeToString(hash[:])
	return keptTokens, nTokensRaw, nTokensKept, sha256Tokens
}
