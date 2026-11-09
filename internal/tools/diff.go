package tools

import (
	"strconv"
	"strings"
)

const (
	maxDiffInputLines = 2000
	maxDiffOutLines   = 60
	diffContext       = 2
)

func Diff(oldText, newText string) string {
	oldLines := splitLines(oldText)
	newLines := splitLines(newText)
	if equalLines(oldLines, newLines) {
		return ""
	}
	if len(oldLines) > maxDiffInputLines {
		oldLines = oldLines[:maxDiffInputLines]
	}
	if len(newLines) > maxDiffInputLines {
		newLines = newLines[:maxDiffInputLines]
	}
	ops := diffOps(oldLines, newLines)
	var b strings.Builder
	shown := 0
	changedShown := 0
	lastChange := -diffContext - 1
	for i, op := range ops {
		if op.kind != opEqual {
			lastChange = i
		}
		show := op.kind != opEqual || i-lastChange <= diffContext
		if !show {
			for d := 1; d <= diffContext && i+d < len(ops); d++ {
				if ops[i+d].kind != opEqual {
					show = true
					break
				}
			}
		}
		if !show {
			continue
		}
		if shown >= maxDiffOutLines {
			break
		}
		switch op.kind {
		case opEqual:
			b.WriteString(" ")
			b.WriteString(op.text)
			b.WriteString("\n")
		case opDelete:
			b.WriteString("-")
			b.WriteString(op.text)
			b.WriteString("\n")
			changedShown++
		case opInsert:
			b.WriteString("+")
			b.WriteString(op.text)
			b.WriteString("\n")
			changedShown++
		}
		shown++
	}
	out := strings.TrimRight(b.String(), "\n")
	if changedShown < totalChanged(ops) {
		out += "\n... +" + strconv.Itoa(totalChanged(ops)-changedShown) + " more"
	}
	return out
}

type opKind int

const (
	opEqual opKind = iota
	opDelete
	opInsert
)

type diffOp struct {
	kind opKind
	text string
}

func splitLines(text string) []string {
	text = strings.TrimSuffix(text, "\n")
	if text == "" {
		return nil
	}
	return strings.Split(text, "\n")
}

func equalLines(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func totalChanged(ops []diffOp) int {
	n := 0
	for _, op := range ops {
		if op.kind != opEqual {
			n++
		}
	}
	return n
}

func diffOps(oldLines, newLines []string) []diffOp {
	n, m := len(oldLines), len(newLines)
	lcs := make([][]int, n+1)
	for i := range lcs {
		lcs[i] = make([]int, m+1)
	}
	for i := n - 1; i >= 0; i-- {
		for j := m - 1; j >= 0; j-- {
			if oldLines[i] == newLines[j] {
				lcs[i][j] = lcs[i+1][j+1] + 1
			} else if lcs[i+1][j] >= lcs[i][j+1] {
				lcs[i][j] = lcs[i+1][j]
			} else {
				lcs[i][j] = lcs[i][j+1]
			}
		}
	}
	ops := make([]diffOp, 0, n+m)
	i, j := 0, 0
	for i < n && j < m {
		if oldLines[i] == newLines[j] {
			ops = append(ops, diffOp{opEqual, oldLines[i]})
			i++
			j++
		} else if lcs[i+1][j] >= lcs[i][j+1] {
			ops = append(ops, diffOp{opDelete, oldLines[i]})
			i++
		} else {
			ops = append(ops, diffOp{opInsert, newLines[j]})
			j++
		}
	}
	for ; i < n; i++ {
		ops = append(ops, diffOp{opDelete, oldLines[i]})
	}
	for ; j < m; j++ {
		ops = append(ops, diffOp{opInsert, newLines[j]})
	}
	return ops
}
