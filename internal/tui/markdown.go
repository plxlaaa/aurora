package tui

import (
        "regexp"
        "strings"
)

var (
        fenceRe     = regexp.MustCompile("^\\s*```+")
        headingRe   = regexp.MustCompile("^(#{1,6})\\s+(.*)$")
        bulletRe    = regexp.MustCompile("^[-*]\\s+(.*)$")
        codeSpanRe  = regexp.MustCompile("`([^`]+)`")
        boldRe      = regexp.MustCompile(`\*\*([^*]+)\*\*`)
        quoteRe     = regexp.MustCompile("^>\\s?(.*)$")
        hrRe        = regexp.MustCompile(`^\s*([-*_]\s*){3,}$`)
        linkRe      = regexp.MustCompile(`\[([^\]]+)\]\(([^)]+)\)`)
)

type mdState struct {
        inFence bool
}

func renderMarkdownLine(line string, st *mdState) string {
        if fenceRe.MatchString(line) {
                st.inFence = !st.inFence
                return cDim + strings.Repeat("─", 12) + cReset
        }
        if st.inFence {
                return "  " + cDim + line + cReset
        }
        if m := headingRe.FindStringSubmatch(line); m != nil {
                text := strings.TrimSpace(m[2])
                if len(m[1]) == 1 {
                        return cBold + cCyan + text + cReset
                }
                return cBold + text + cReset
        }
        if m := bulletRe.FindStringSubmatch(line); m != nil {
                return cDim + glyphBullet() + cReset + " " + inlineMarkdown(m[1])
        }
        if m := quoteRe.FindStringSubmatch(line); m != nil {
                return cDim + glyphQuote() + cReset + " " + cDim + inlineMarkdown(m[1]) + cReset
        }
        if hrRe.MatchString(line) {
                return cDim + strings.Repeat("─", 32) + cReset
        }
        return inlineMarkdown(line)
}

func inlineMarkdown(s string) string {
        parts := codeSpanRe.Split(s, -1)
        spans := codeSpanRe.FindAllStringSubmatch(s, -1)
        var b strings.Builder
        for i, p := range parts {
                b.WriteString(boldMarkdown(p))
                if i < len(spans) {
                        b.WriteString(cCyan + spans[i][1] + cReset)
                }
        }
        return b.String()
}

func boldMarkdown(s string) string {
        s = boldRe.ReplaceAllString(s, cBold+"$1"+cReset)
        return linkRe.ReplaceAllString(s, cCyan+"$1"+cReset+cDim+" ($2)"+cReset)
}

type lineBuffer struct {
        st   mdState
        pend strings.Builder
        rich bool
}

func (lb *lineBuffer) Feed(delta string, p *printer) {
        lb.pend.WriteString(delta)
        for {
                s := lb.pend.String()
                idx := strings.IndexByte(s, '\n')
                if idx < 0 {
                        return
                }
                line := s[:idx]
                lb.pend.Reset()
                lb.pend.WriteString(s[idx+1:])
                lb.emitLine(line, p, true)
        }
}

func (lb *lineBuffer) Flush(p *printer) {
        rest := lb.pend.String()
        if rest == "" {
                return
        }
        lb.pend.Reset()
        lb.emitLine(rest, p, false)
}

func (lb *lineBuffer) emitLine(line string, p *printer, newline bool) {
        if lb.rich {
                p.Print(renderMarkdownLine(line, &lb.st))
        } else {
                p.Print(line)
        }
        if newline {
                p.Print("\n")
        }
}

func (lb *lineBuffer) Reset() {
        lb.pend.Reset()
        lb.st = mdState{}
}
