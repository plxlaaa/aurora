package tools

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadWriteEditAndGrep(t *testing.T) {
	root := t.TempDir()
	r := &Registry{Root: root}
	res := r.writeFile("src/hello.txt", "alpha\nbeta\n")
	if !res.OK {
		t.Fatal(res.Content)
	}
	res = r.readFile("src/hello.txt", 1, 10)
	if !strings.Contains(res.Content, "1: alpha") {
		t.Fatalf("read: %s", res.Content)
	}
	res = r.editFile("src/hello.txt", "beta", "gamma", false)
	if !res.OK {
		t.Fatal(res.Content)
	}
	res = r.grep("gamma", ".", "*.txt")
	if !strings.Contains(res.Content, "gamma") {
		t.Fatalf("grep: %s", res.Content)
	}
	res = r.listDir("src")
	if !strings.Contains(res.Content, "hello.txt") {
		t.Fatalf("list: %s", res.Content)
	}
	abs, _ := filepath.Abs(root)
	_ = os.WriteFile(filepath.Join(abs, "src", "other.go"), []byte("package other\n"), 0o644)
	res = r.glob("**/*.go")
	if !strings.Contains(res.Content, "other.go") {
		t.Fatalf("glob: %s", res.Content)
	}
}

func TestPathEscapeRejected(t *testing.T) {
	root := t.TempDir()
	r := &Registry{Root: root}
	paths := []string{"../secret", "foo/../../secret"}
	if os.PathSeparator == '\\' {
		paths = append(paths, `..\secret`)
	}
	for _, p := range paths {
		res := r.readFile(p, 0, 0)
		if res.OK {
			t.Fatalf("expected path escape to fail for %s", p)
		}
	}
}

func TestGlobDoesNotOverMatch(t *testing.T) {
	root := t.TempDir()
	r := &Registry{Root: root}
	_ = os.MkdirAll(filepath.Join(root, "src", "pkg"), 0o755)
	_ = os.WriteFile(filepath.Join(root, "root.go"), []byte("package root\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "src", "pkg", "nested.go"), []byte("package nested\n"), 0o644)
	_ = os.WriteFile(filepath.Join(root, "readme.txt"), []byte("x\n"), 0o644)

	res := r.glob("*.go")
	if strings.Contains(res.Content, "nested.go") {
		t.Fatalf("*.go should stay in root: %s", res.Content)
	}
	if !strings.Contains(res.Content, "root.go") {
		t.Fatalf("*.go missed root.go: %s", res.Content)
	}
	res = r.glob("**/*.go")
	if !strings.Contains(res.Content, "nested.go") {
		t.Fatalf("**/*.go missed nested.go: %s", res.Content)
	}
}

func TestShellConfirmCancel(t *testing.T) {
	root := t.TempDir()
	r := &Registry{
		Root:         root,
		ConfirmShell: true,
		Confirm:      func(string) bool { return false },
	}
	res := r.runShell(context.Background(), "echo hi", "")
	if res.OK {
		t.Fatal("expected cancel")
	}
}
