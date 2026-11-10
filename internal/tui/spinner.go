package tui

import (
	"math/rand"
	"sync"
	"time"
)

var spinnerFrames = []string{"·", "✢", "✳", "∗", "✻", "✽"}

var spinnerVerbs = []string{
	"Thinking", "Analyzing", "Reasoning", "Planning", "Pondering",
	"Brewing", "Cooking", "Crafting", "Sculpting", "Refactoring",
	"Optimizing", "Scanning", "Parsing", "Assembling", "Weaving",
	"Inspecting", "Mapping", "Sketching", "Tracing", "Debugging",
	"Exploring", "Searching", "Digging", "Unfolding", "Composing",
	"Orchestrating", "Calibrating", "Forging", "Sharpening", "Polishing",
	"Growing", "Concocting", "Synthesizing", "Distilling", "Structuring",
	"Aligning", "Reorganizing", "Translating", "Evaluating", "Noodling",
}

const (
	spinnerTick     = 120 * time.Millisecond
	spinnerRerollAt = 64
)

type spinner struct {
	p      *printer
	mu     sync.Mutex
	stop   chan struct{}
	done   chan struct{}
	active bool
}

func newSpinner(p *printer) *spinner {
	return &spinner{p: p}
}

func (s *spinner) Start() {
	if !IsTTY() {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.active {
		return
	}
	s.active = true
	s.stop = make(chan struct{})
	s.done = make(chan struct{})
	go s.loop()
}

func (s *spinner) loop() {
	defer close(s.done)
	start := time.Now()
	pos := 0
	dir := 1
	ticks := 0
	verb := spinnerVerbs[rand.Intn(len(spinnerVerbs))]
	ticker := time.NewTicker(spinnerTick)
	defer ticker.Stop()
	for {
		select {
		case <-s.stop:
			return
		case <-ticker.C:
			ticks++
			next := pos + dir
			if next >= len(spinnerFrames) || next < 0 {
				dir = -dir
				next = pos + dir
			}
			pos = next
			frame := spinnerFrames[pos]
			if ticks%spinnerRerollAt == 0 {
				verb = spinnerVerbs[rand.Intn(len(spinnerVerbs))]
			}
			elapsed := int(time.Since(start).Seconds())
			s.p.Printf("\r\033[2K%s%s%s %s%s%s... %ds %s· Ctrl+C interrupt%s",
				cCyan, frame, cReset,
				cBold, verb, cReset,
				elapsed,
				cDim, cReset)
		}
	}
}

func (s *spinner) Stop() {
	s.mu.Lock()
	if !s.active {
		s.mu.Unlock()
		return
	}
	s.active = false
	stop := s.stop
	done := s.done
	s.mu.Unlock()
	close(stop)
	select {
	case <-done:
	case <-time.After(500 * time.Millisecond):
	}
	s.p.ClearLine()
}
