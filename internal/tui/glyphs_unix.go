//go:build !windows

package tui

// detectUnicode: non-Windows terminals are assumed UTF-8 capable
// (macOS/Linux terminals have been for a decade). Piped output falls
// back to ASCII so scripts can grep our lines.
func detectUnicode() bool {
	return true
}

func init() { SetUnicode(detectUnicode()) }
