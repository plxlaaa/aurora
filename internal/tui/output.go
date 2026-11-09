package tui

import (
	"fmt"
	"io"
	"sync"
)

type printer struct {
	mu sync.Mutex
	w  io.Writer
}

func newPrinter(w io.Writer) *printer {
	return &printer{w: w}
}

func (p *printer) Print(s string) {
	p.mu.Lock()
	defer p.mu.Unlock()
	fmt.Fprint(p.w, s)
}

func (p *printer) Printf(format string, a ...any) {
	p.mu.Lock()
	defer p.mu.Unlock()
	fmt.Fprintf(p.w, format, a...)
}

func (p *printer) ClearLine() {
	p.Print("\r\033[2K")
}
