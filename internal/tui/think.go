package tui

import (
        "strconv"
        "strings"
        "time"
)

// ThinkFilter hides chain-of-thought blocks (<think>…</think>) that many
// OpenAI-compatible relays inline in assistant content (DeepSeek/QwQ style).
// Claude Code shows a "✻ Thinking…" shimmer and collapses the block; we print
// a dim notice with the elapsed time instead of dumping raw reasoning.
// Streamed deltas may split tags mid-way, so a small holdback buffer keeps
// partial "<th…" tails until they resolve.
type ThinkFilter struct {
        inThink bool
        started time.Time
        shown   bool
        pend    string
}

// Reset clears filter state between turns.
func (t *ThinkFilter) Reset() {
        *t = ThinkFilter{}
}

var openTags = []string{"<think>", "<thinking>"}
var closeTags = []string{"</think>", "</thinking>"}

// Feed consumes one streamed delta and returns what should be printed now.
func (t *ThinkFilter) Feed(delta string) string {
        if delta == "" && t.pend == "" {
                return ""
        }
        t.pend += delta
        var out strings.Builder
        for {
                if t.inThink {
                        end := indexAnyTag(t.pend, closeTags)
                        if end < 0 {
                                // still inside the block; keep buffering (nothing prints)
                                if len(t.pend) > 64*1024 {
                                        t.pend = "" // runaway reasoning: drop rather than grow forever
                                }
                                return out.String()
                        }
                        after := t.pend[end+closeTagLen(t.pend, end):]
                        t.inThink = false
                        if t.shown {
                                if d := time.Since(t.started).Round(time.Second).Seconds(); d >= 1 {
                                        out.WriteString(thinkNotice(d))
                                }
                        }
                        t.pend = after
                        continue
                }
                beg := indexAnyTag(t.pend, openTags)
                if beg < 0 {
                        keep := holdbackLen(t.pend)
                        emit := t.pend[:len(t.pend)-keep]
                        out.WriteString(emit)
                        t.pend = t.pend[len(t.pend)-keep:]
                        return out.String()
                }
                out.WriteString(t.pend[:beg])
                t.inThink = true
                t.started = time.Now()
                t.shown = true
                t.pend = t.pend[beg+openTagLen(t.pend, beg):]
        }
}

func indexAnyTag(s string, tags []string) int {
        best := -1
        for _, tag := range tags {
                if i := strings.Index(s, tag); i >= 0 && (best < 0 || i < best) {
                        best = i
                }
        }
        return best
}

func openTagLen(s string, beg int) int {
        if strings.HasPrefix(s[beg:], "<thinking>") {
                return len("<thinking>")
        }
        return len("<think>")
}

func closeTagLen(s string, end int) int {
        if strings.HasPrefix(s[end:], "</thinking>") {
                return len("</thinking>")
        }
        return len("</think>")
}

// holdbackLen returns how many trailing bytes may be the start of an open
// tag and must not be printed yet. At most len("<thinking>")-1 bytes.
func holdbackLen(s string) int {
        max := len("<thinking>") - 1
        if len(s) < max {
                max = len(s)
        }
        for k := max; k > 0; k-- {
                tail := s[len(s)-k:]
                for _, tag := range openTags {
                        if strings.HasPrefix(tag, tail) {
                                return k
                        }
                }
        }
        return 0
}

// thinkNotice is the one-line collapsed summary after a reasoning block ends.
func thinkNotice(seconds float64) string {
        return cDim + "  " + glyphThink() + " thought for " + fmtSeconds(seconds) + cReset + "\n"
}

func fmtSeconds(s float64) string {
        switch {
        case s < 1:
                return "under a second"
        case s < 60:
                return trimZero(s) + "s"
        default:
                return trimZero(s/60) + "m"
        }
}

func trimZero(f float64) string {
        if f == float64(int64(f)) {
                return strconv.Itoa(int(f))
        }
        return strconv.FormatFloat(f, 'f', 1, 64)
}
