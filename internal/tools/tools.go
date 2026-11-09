package tools

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"

	"github.com/plxlaaa/aurora/internal/llm"
)

type Result struct {
	OK      bool   `json:"ok"`
	Content string `json:"content"`
	Diff    string `json:"-"`
}

type Registry struct {
	Root         string
	ConfirmShell bool
	Confirm      func(string) bool
}

func Specs() []llm.ToolSpec {
	return []llm.ToolSpec{
		spec("read_file", "Read a UTF-8 text file. Limit large files with offset/limit.", map[string]any{
			"type": "object",
			"properties": map[string]any{
				"path":   map[string]any{"type": "string"},
				"offset": map[string]any{"type": "integer", "description": "1-based start line"},
				"limit":  map[string]any{"type": "integer", "description": "max lines to return"},
			},
			"required": []string{"path"},
		}),
		spec("write_file", "Create or overwrite a UTF-8 text file.", map[string]any{
			"type": "object",
			"properties": map[string]any{
				"path":    map[string]any{"type": "string"},
				"content": map[string]any{"type": "string"},
			},
			"required": []string{"path", "content"},
		}),
		spec("edit_file", "Replace exact text in a file.", map[string]any{
			"type": "object",
			"properties": map[string]any{
				"path":        map[string]any{"type": "string"},
				"old_string":  map[string]any{"type": "string"},
				"new_string":  map[string]any{"type": "string"},
				"replace_all": map[string]any{"type": "boolean"},
			},
			"required": []string{"path", "old_string", "new_string"},
		}),
		spec("list_dir", "List files in a directory.", map[string]any{
			"type": "object",
			"properties": map[string]any{
				"path": map[string]any{"type": "string"},
			},
			"required": []string{"path"},
		}),
		spec("glob", "Find files by glob pattern relative to workspace.", map[string]any{
			"type": "object",
			"properties": map[string]any{
				"pattern": map[string]any{"type": "string"},
			},
			"required": []string{"pattern"},
		}),
		spec("grep", "Search file contents with a regular expression.", map[string]any{
			"type": "object",
			"properties": map[string]any{
				"pattern": map[string]any{"type": "string"},
				"path":    map[string]any{"type": "string"},
				"include": map[string]any{"type": "string", "description": "glob filter such as *.go"},
			},
			"required": []string{"pattern"},
		}),
		spec("run_shell", "Run a shell command in the workspace. Prefer PowerShell on Windows.", map[string]any{
			"type": "object",
			"properties": map[string]any{
				"command": map[string]any{"type": "string"},
				"cwd":     map[string]any{"type": "string"},
			},
			"required": []string{"command"},
		}),
	}
}

func spec(name, desc string, schema map[string]any) llm.ToolSpec {
	raw, _ := json.Marshal(schema)
	return llm.ToolSpec{
		Type: "function",
		Function: llm.ToolFunction{
			Name:        name,
			Description: desc,
			Parameters:  raw,
		},
	}
}

func (r *Registry) Call(ctx context.Context, name, argsJSON string) Result {
	args := map[string]any{}
	if strings.TrimSpace(argsJSON) != "" {
		if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
			return fail("invalid tool arguments: " + err.Error())
		}
	}
	switch name {
	case "read_file":
		return r.readFile(str(args, "path"), intArg(args, "offset"), intArg(args, "limit"))
	case "write_file":
		return r.writeFile(str(args, "path"), str(args, "content"))
	case "edit_file":
		return r.editFile(str(args, "path"), str(args, "old_string"), str(args, "new_string"), boolArg(args, "replace_all"))
	case "list_dir":
		return r.listDir(str(args, "path"))
	case "glob":
		return r.glob(str(args, "pattern"))
	case "grep":
		return r.grep(str(args, "pattern"), str(args, "path"), str(args, "include"))
	case "run_shell":
		return r.runShell(ctx, str(args, "command"), str(args, "cwd"))
	default:
		return fail("unknown tool: " + name)
	}
}

func (r *Registry) resolve(p string) (string, error) {
	if p == "" {
		p = "."
	}
	root, err := filepath.Abs(r.Root)
	if err != nil {
		return "", err
	}
	root = filepath.Clean(root)
	if !filepath.IsAbs(p) {
		p = filepath.Join(root, p)
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return "", err
	}
	abs = filepath.Clean(abs)
	rel, err := filepath.Rel(root, abs)
	if err != nil {
		return "", fmt.Errorf("path is outside workspace")
	}
	rel = filepath.Clean(rel)
	if !inWorkspace(rel) {
		return "", fmt.Errorf("path is outside workspace")
	}
	return abs, nil
}

func inWorkspace(rel string) bool {
	if rel == "." {
		return true
	}
	if rel == ".." {
		return false
	}
	sep := string(os.PathSeparator)
	if strings.HasPrefix(rel, ".."+sep) || strings.HasPrefix(rel, "../") {
		return false
	}
	for _, part := range strings.Split(rel, sep) {
		if part == ".." {
			return false
		}
	}
	return true
}

func (r *Registry) readFile(path string, offset, limit int) Result {
	abs, err := r.resolve(path)
	if err != nil {
		return fail(err.Error())
	}
	st, err := os.Stat(abs)
	if err != nil {
		return fail(err.Error())
	}
	if st.Size() > 2<<20 && limit <= 0 {
		return fail("file larger than 2MB; pass offset and limit")
	}
	raw, err := os.ReadFile(abs)
	if err != nil {
		return fail(err.Error())
	}
	if bytes.IndexByte(raw, 0) >= 0 {
		return fail("binary file")
	}
	text := string(raw)
	lines := strings.Split(text, "\n")
	start := 0
	if offset > 0 {
		start = offset - 1
	}
	if start > len(lines) {
		start = len(lines)
	}
	end := len(lines)
	if limit > 0 && start+limit < end {
		end = start + limit
	}
	var b strings.Builder
	for i := start; i < end; i++ {
		fmt.Fprintf(&b, "%d: %s\n", i+1, lines[i])
	}
	return ok(b.String())
}

func (r *Registry) writeFile(path, content string) Result {
	abs, err := r.resolve(path)
	if err != nil {
		return fail(err.Error())
	}
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return fail(err.Error())
	}
	prev := ""
	created := true
	if raw, err := os.ReadFile(abs); err == nil {
		prev = string(raw)
		created = false
	}
	if err := os.WriteFile(abs, []byte(content), 0o644); err != nil {
		return fail(err.Error())
	}
	res := ok("wrote " + abs)
	if created {
		res.Content = fmt.Sprintf("created %s (%d lines)", abs, len(splitLines(content)))
		res.Diff = Diff("", content)
	} else {
		res.Content = fmt.Sprintf("wrote %s (%d lines)", abs, len(splitLines(content)))
		res.Diff = Diff(prev, content)
	}
	return res
}

func (r *Registry) editFile(path, oldS, newS string, replaceAll bool) Result {
	abs, err := r.resolve(path)
	if err != nil {
		return fail(err.Error())
	}
	raw, err := os.ReadFile(abs)
	if err != nil {
		return fail(err.Error())
	}
	src := string(raw)
	if oldS == "" {
		return fail("old_string is empty")
	}
	count := strings.Count(src, oldS)
	if count == 0 {
		return fail("old_string not found")
	}
	if !replaceAll && count > 1 {
		return fail("old_string matched multiple times; set replace_all=true or add more context")
	}
	out := strings.Replace(src, oldS, newS, -1)
	if !replaceAll {
		out = strings.Replace(src, oldS, newS, 1)
	}
	if err := os.WriteFile(abs, []byte(out), 0o644); err != nil {
		return fail(err.Error())
	}
	replacements := map[bool]int{true: count, false: 1}[replaceAll]
	res := ok(fmt.Sprintf("updated %s (%d replacement)", abs, replacements))
	res.Diff = Diff(src, out)
	return res
}

func (r *Registry) listDir(path string) Result {
	abs, err := r.resolve(path)
	if err != nil {
		return fail(err.Error())
	}
	entries, err := os.ReadDir(abs)
	if err != nil {
		return fail(err.Error())
	}
	var b strings.Builder
	for _, e := range entries {
		kind := "file"
		if e.IsDir() {
			kind = "dir"
		}
		fmt.Fprintf(&b, "%s\t%s\n", kind, e.Name())
	}
	return ok(b.String())
}

func (r *Registry) glob(pattern string) Result {
	if pattern == "" {
		return fail("pattern is empty")
	}
	matches := []string{}
	_ = filepath.WalkDir(r.Root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		rel, relErr := filepath.Rel(r.Root, path)
		if relErr != nil {
			return nil
		}
		rel = filepath.ToSlash(rel)
		if globMatch(pattern, rel) && !d.IsDir() {
			matches = append(matches, rel)
		}
		if d.IsDir() && (d.Name() == ".git" || d.Name() == "node_modules" || d.Name() == "vendor") {
			return filepath.SkipDir
		}
		if len(matches) >= 200 {
			return io.EOF
		}
		return nil
	})
	sort.Strings(matches)
	return ok(strings.Join(matches, "\n"))
}

func globMatch(pattern, rel string) bool {
	pattern = filepath.ToSlash(strings.TrimSpace(pattern))
	rel = filepath.ToSlash(rel)
	if pattern == "" {
		return false
	}
	re, err := globToRegexp(pattern)
	if err != nil {
		ok, _ := path.Match(pattern, rel)
		if ok {
			return true
		}
		ok, _ = path.Match(pattern, path.Base(rel))
		return ok
	}
	return re.MatchString(rel)
}

func globToRegexp(pat string) (*regexp.Regexp, error) {
	var b strings.Builder
	b.WriteByte('^')
	runes := []rune(pat)
	for i := 0; i < len(runes); i++ {
		if runes[i] == '*' {
			if i+1 < len(runes) && runes[i+1] == '*' {
				b.WriteString(".*")
				i++
				if i+1 < len(runes) && runes[i+1] == '/' {
					i++
				}
				continue
			}
			b.WriteString("[^/]*")
			continue
		}
		if runes[i] == '?' {
			b.WriteString("[^/]")
			continue
		}
		b.WriteString(regexp.QuoteMeta(string(runes[i])))
	}
	b.WriteByte('$')
	return regexp.Compile(b.String())
}

func (r *Registry) grep(pattern, path, include string) Result {
	re, err := regexp.Compile(pattern)
	if err != nil {
		return fail(err.Error())
	}
	root := r.Root
	if path != "" {
		abs, err := r.resolve(path)
		if err != nil {
			return fail(err.Error())
		}
		root = abs
	}
	var b strings.Builder
	hits := 0
	_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			if d != nil && d.IsDir() && (d.Name() == ".git" || d.Name() == "node_modules" || d.Name() == "vendor") {
				return filepath.SkipDir
			}
			return nil
		}
		if include != "" {
			okMatch, _ := filepath.Match(include, d.Name())
			if !okMatch {
				return nil
			}
		}
		raw, err := os.ReadFile(p)
		if err != nil || bytes.IndexByte(raw, 0) >= 0 {
			return nil
		}
		rel, _ := filepath.Rel(r.Root, p)
		for i, line := range strings.Split(string(raw), "\n") {
			if re.FindStringIndex(line) != nil {
				fmt.Fprintf(&b, "%s:%d:%s\n", filepath.ToSlash(rel), i+1, line)
				hits++
				if hits >= 80 {
					return io.EOF
				}
			}
		}
		return nil
	})
	if hits == 0 {
		return ok("no matches")
	}
	return ok(b.String())
}

func (r *Registry) runShell(ctx context.Context, command, cwd string) Result {
	if strings.TrimSpace(command) == "" {
		return fail("command is empty")
	}
	if r.ConfirmShell && r.Confirm != nil && !r.Confirm(command) {
		return fail("shell command cancelled")
	}
	work := r.Root
	if cwd != "" {
		abs, err := r.resolve(cwd)
		if err != nil {
			return fail(err.Error())
		}
		work = abs
	}
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command)
	} else {
		cmd = exec.CommandContext(ctx, "bash", "-lc", command)
	}
	cmd.Dir = work
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	out := strings.TrimSpace(stdout.String() + "\n" + stderr.String())
	if out == "" {
		out = "(no output)"
	}
	if len(out) > 80_000 {
		out = out[:80_000] + "\n...[truncated]"
	}
	if err != nil {
		return fail(out + "\n" + err.Error())
	}
	return ok(out)
}

func str(m map[string]any, k string) string {
	v, _ := m[k].(string)
	return v
}

func intArg(m map[string]any, k string) int {
	switch v := m[k].(type) {
	case float64:
		return int(v)
	case int:
		return v
	case json.Number:
		n, _ := v.Int64()
		return int(n)
	case string:
		n := 0
		for _, ch := range v {
			if ch < '0' || ch > '9' {
				return 0
			}
			n = n*10 + int(ch-'0')
		}
		return n
	default:
		return 0
	}
}

func boolArg(m map[string]any, k string) bool {
	v, _ := m[k].(bool)
	return v
}

func ok(s string) Result   { return Result{OK: true, Content: s} }
func fail(s string) Result { return Result{OK: false, Content: s} }
