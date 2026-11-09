package tools

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDiffModify(t *testing.T) {
	d := Diff("alpha\nbeta\ngamma", "alpha\nBETA\ngamma")
	if !strings.Contains(d, "-beta") || !strings.Contains(d, "+BETA") {
		t.Fatalf("diff=%q", d)
	}
}

func TestDiffInsertAndDelete(t *testing.T) {
	d := Diff("one\ntwo\nthree", "one\ntwo\nthree\nfour")
	if !strings.Contains(d, "+four") || strings.Contains(d, "-one") {
		t.Fatalf("diff=%q", d)
	}
	d = Diff("one\ntwo", "one")
	if !strings.Contains(d, "-two") || strings.Contains(d, "+") {
		t.Fatalf("diff=%q", d)
	}
}

func TestDiffIdentical(t *testing.T) {
	if d := Diff("same\ntext", "same\ntext"); d != "" {
		t.Fatalf("expected empty, got %q", d)
	}
}

func TestDiffEmptyToContent(t *testing.T) {
	d := Diff("", "hello\nworld")
	if !strings.Contains(d, "+hello") || !strings.Contains(d, "+world") {
		t.Fatalf("diff=%q", d)
	}
}

func TestDiffCapTrailer(t *testing.T) {
	var oldLines, newLines []string
	for i := 0; i < 100; i++ {
		oldLines = append(oldLines, "old")
		newLines = append(newLines, "new")
	}
	d := Diff(strings.Join(oldLines, "\n"), strings.Join(newLines, "\n"))
	if !strings.Contains(d, "more") {
		t.Fatalf("expected trailer, got %q", d)
	}
	if got := strings.Count(d, "\n"); got > 70 {
		t.Fatalf("diff too long: %d lines", got)
	}
}

func TestWriteFileDiffOnCreateAndOverwrite(t *testing.T) {
	r := &Registry{Root: t.TempDir()}
	res := r.writeFile("out.txt", "hello\nworld\n")
	if !res.OK || !strings.Contains(res.Diff, "+hello") {
		t.Fatalf("create diff=%q", res.Diff)
	}
	res = r.writeFile("out.txt", "hello\nWORLD\n")
	if !strings.Contains(res.Diff, "-world") || !strings.Contains(res.Diff, "+WORLD") {
		t.Fatalf("overwrite diff=%q", res.Diff)
	}
}

func TestEditFileDiff(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "a.txt")
	if err := os.WriteFile(p, []byte("one\ntwo\nthree\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	r := &Registry{Root: dir}
	res := r.editFile("a.txt", "two", "TWO", false)
	if !res.OK {
		t.Fatalf("edit failed: %s", res.Content)
	}
	if !strings.Contains(res.Diff, "-two") || !strings.Contains(res.Diff, "+TWO") {
		t.Fatalf("diff=%q", res.Diff)
	}
}

func TestResultJSONExcludesDiff(t *testing.T) {
	raw, err := json.Marshal(Result{OK: true, Content: "x", Diff: "+secret"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "Diff") || strings.Contains(string(raw), "secret") {
		t.Fatalf("diff leaked into llm payload: %s", raw)
	}
}
