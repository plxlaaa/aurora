//go:build windows

package tui

import (
	"os"
	"strings"
	"syscall"
	"unicode/utf8"
)

// detectUnicode decides whether the console can render the Unicode glyph
// set. Windows classic conhost with a legacy codepage (cp936/cp437) cannot
// — those show ✻⏺⎿ as '?' — while Windows Terminal and VT-enabled hosts
// handle UTF-8 fine. Heuristics, in order:
//  1. WT_SESSION set            -> Windows Terminal, UTF-8 safe
//  2. output is not a TTY       -> piped/redirected: plain ASCII is safest
//     (scripts grep our output)
//  3. codepage 65001            -> UTF-8 console, safe
//  4. anything else (cp936 …)   -> fall back to ASCII glyphs
func detectUnicode() bool {
	if os.Getenv("WT_SESSION") != "" || os.Getenv("TERM_PROGRAM") != "" {
		return true
	}
	st, err := os.Stdout.Stat()
	if err != nil || st.Mode()&os.ModeCharDevice == 0 {
		return false
	}
	return consoleCP() == 65001
}

func consoleCP() uint32 {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	get := kernel32.NewProc("GetConsoleOutputCP")
	cp, _, _ := get.Call()
	return uint32(cp)
}

func init() { SetUnicode(detectUnicode()) }

var _ = utf8.RuneLen
var _ = strings.TrimSpace
