package tui

import (
	"fmt"
	"path/filepath"
	"strings"

	"github.com/plxlaaa/aurora/internal/llm"
)

// StatusLine is the dim one-liner Claude Code prints under a turn:
// model · workspace · token totals · context headroom.
func StatusLine(model, workspace string, last llm.Usage, ctxWindow int64) string {
	parts := []string{model}
	if base := filepath.Base(workspace); base != "" && base != "." && base != "/" {
		parts = append(parts, base)
	}
	if !last.IsZero() {
		parts = append(parts, fmt.Sprintf("↑%s ↓%s", fmtTokens(last.Prompt), fmtTokens(last.Completion)))
	}
	if seg := contextSegment(last.Prompt, ctxWindow); seg != "" {
		// strip the embedded color codes when composing the dim line
		seg = strings.ReplaceAll(seg, "\033[31m", "")
		seg = strings.ReplaceAll(seg, "\033[33m", "")
		parts = append(parts, strings.TrimSpace(seg))
	}
	if cw := ctxWindow; cw > 0 {
		parts = append(parts, fmt.Sprintf("ctx %s", fmtTokens(cw)))
	}
	return cDim + strings.Join(parts, " · ") + cReset
}
