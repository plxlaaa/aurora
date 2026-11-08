package updater

import (
	"os"
	"path/filepath"
	"testing"
)

func TestApplyPendingSwapsBinary(t *testing.T) {
	dir := t.TempDir()
	exe := filepath.Join(dir, "aurora")
	if err := os.WriteFile(exe, []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(NewPath(exe), []byte("new"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := ApplyPending(exe); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(exe)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != "new" {
		t.Fatalf("got %q", got)
	}
	bak, err := os.ReadFile(OldPath(exe))
	if err != nil {
		t.Fatal(err)
	}
	if string(bak) != "old" {
		t.Fatalf("bak %q", bak)
	}
	if _, err := os.Stat(NewPath(exe)); err == nil {
		t.Fatal("new binary should have been consumed")
	}
}

func TestApplyPendingNoop(t *testing.T) {
	dir := t.TempDir()
	exe := filepath.Join(dir, "aurora")
	if err := os.WriteFile(exe, []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := ApplyPending(exe); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(exe)
	if string(got) != "old" {
		t.Fatalf("got %q", got)
	}
}
