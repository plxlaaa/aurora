package updater

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/plxlaaa/aurora/internal/config"
	"github.com/plxlaaa/aurora/internal/version"
)

type Manifest struct {
	Version string           `json:"version"`
	Notes   string           `json:"notes,omitempty"`
	Assets  map[string]Asset `json:"assets"`
}

type Asset struct {
	Name   string `json:"name"`
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size,omitempty"`
}

type Result struct {
	Updated bool
	From    string
	To      string
	Channel string
	Asset   string
	Elapsed time.Duration
	Message string
}

type ProgressFn func(event string, detail string)

func Check(ctx context.Context, cfg config.Config, current string, progress ProgressFn) (Result, error) {
	start := time.Now()
	timeout := time.Duration(cfg.Update.TimeoutMS) * time.Millisecond
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	man, src, err := fetchManifest(ctx, cfg, timeout, progress)
	if err != nil {
		return Result{}, err
	}
	if progress != nil {
		progress("manifest", "loaded from "+src+" version "+man.Version)
	}
	msg := "already up to date"
	available := Newer(man.Version, current)
	if available {
		msg = "update available: " + current + " -> " + man.Version
	}
	return Result{
		Updated: false,
		From:    current,
		To:      man.Version,
		Message: msg,
		Elapsed: time.Since(start),
	}, nil
}

func CheckAndApply(ctx context.Context, cfg config.Config, current string, force bool, progress ProgressFn) (Result, error) {
	start := time.Now()
	emit := func(event, detail string) {
		if progress != nil {
			progress(event, detail)
		}
	}
	timeout := time.Duration(cfg.Update.TimeoutMS) * time.Millisecond
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	ctx, cancel := context.WithTimeout(ctx, timeout+30*time.Second)
	defer cancel()

	man, src, err := fetchManifest(ctx, cfg, timeout, emit)
	if err != nil {
		return Result{}, err
	}
	emit("manifest", "loaded from "+src+" version "+man.Version)
	if !force && !Newer(man.Version, current) {
		return Result{
			Updated: false,
			From:    current,
			To:      man.Version,
			Message: "already up to date",
			Elapsed: time.Since(start),
		}, nil
	}
	plat := runtime.GOOS + "-" + runtime.GOARCH
	asset, ok := man.Assets[plat]
	if !ok {
		return Result{}, fmt.Errorf("no release asset for platform %s", plat)
	}
	urls := assetURLs(cfg, man.Version, asset)
	emit("download", fmt.Sprintf("racing %d channels for %s", len(urls), asset.Name))
	tmp, channel, err := raceDownload(ctx, urls, timeout, emit)
	if err != nil {
		hint := ""
		if p, perr := config.Path(); perr == nil {
			hint = " or edit " + p + " update.mirrors/update.timeout_ms"
		}
		return Result{}, fmt.Errorf(
			"%w; download manually from https://github.com/%s/releases/latest%s",
			err, cfg.Update.Repo, hint,
		)
	}
	defer os.Remove(tmp)
	if asset.SHA256 != "" {
		sum, err := fileSHA256(tmp)
		if err != nil {
			return Result{}, err
		}
		if !strings.EqualFold(sum, asset.SHA256) {
			return Result{}, fmt.Errorf("checksum mismatch: got %s want %s", sum, asset.SHA256)
		}
		emit("verify", "sha256 ok")
	}
	exe, err := os.Executable()
	if err != nil {
		return Result{}, err
	}
	exe, err = filepath.EvalSymlinks(exe)
	if err != nil {
		exe, _ = os.Executable()
	}
	if err := replaceSelf(tmp, exe, asset.Name); err != nil {
		var pending *PendingError
		if errors.As(err, &pending) {
			emit("replace", pending.Path+" (pending)")
			return Result{
				Updated: true,
				From:    current,
				To:      man.Version,
				Channel: channel,
				Asset:   asset.Name,
				Elapsed: time.Since(start),
				Message: "downloaded " + man.Version + " via " + channel + "; restart aurora to finish replacing the locked binary",
			}, nil
		}
		return Result{}, err
	}
	emit("replace", exe)
	return Result{
		Updated: true,
		From:    current,
		To:      man.Version,
		Channel: channel,
		Asset:   asset.Name,
		Elapsed: time.Since(start),
		Message: "updated " + current + " -> " + man.Version + " via " + channel,
	}, nil
}

func fetchManifest(ctx context.Context, cfg config.Config, timeout time.Duration, emit ProgressFn) (Manifest, string, error) {
	urls := append([]string{}, cfg.Update.ManifestURLs...)
	urls = append(urls, GitHubLatestAPI(cfg.Update.Repo)...)
	seen := map[string]bool{}
	uniq := make([]string, 0, len(urls))
	for _, u := range urls {
		if u == "" || seen[u] {
			continue
		}
		seen[u] = true
		uniq = append(uniq, u)
	}
	type hit struct {
		man Manifest
		src string
		err error
	}
	ch := make(chan hit, len(uniq))
	child, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	for _, u := range uniq {
		u := u
		go func() {
			man, err := getManifest(child, u)
			ch <- hit{man: man, src: u, err: err}
		}()
	}
	var last error
	for i := 0; i < len(uniq); i++ {
		h := <-ch
		if h.err == nil && h.man.Version != "" {
			cancel()
			return h.man, h.src, nil
		}
		last = h.err
		if emit != nil && h.err != nil {
			emit("channel_fail", h.src+": "+h.err.Error())
		}
	}
	if last == nil {
		last = errors.New("no manifest channel succeeded")
	}
	return Manifest{}, "", last
}

func getManifest(ctx context.Context, rawURL string) (Manifest, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return Manifest{}, err
	}
	req.Header.Set("User-Agent", "aurora/"+version.Short())
	req.Header.Set("Accept", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return Manifest{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return Manifest{}, fmt.Errorf("http %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	if err != nil {
		return Manifest{}, err
	}
	var man Manifest
	if err := json.Unmarshal(body, &man); err == nil && man.Version != "" {
		return man, nil
	}
	var gh githubRelease
	if err := json.Unmarshal(body, &gh); err != nil {
		return Manifest{}, fmt.Errorf("unrecognized manifest")
	}
	return gh.ToManifest(), nil
}

type githubRelease struct {
	TagName string `json:"tag_name"`
	Body    string `json:"body"`
	Assets  []struct {
		Name               string `json:"name"`
		BrowserDownloadURL string `json:"browser_download_url"`
		Size               int64  `json:"size"`
	} `json:"assets"`
}

func (g githubRelease) ToManifest() Manifest {
	ver := strings.TrimPrefix(g.TagName, "v")
	assets := map[string]Asset{}
	for _, a := range g.Assets {
		plat := platformFromName(a.Name)
		if plat == "" {
			continue
		}
		assets[plat] = Asset{Name: a.Name, URL: a.BrowserDownloadURL, Size: a.Size}
	}
	return Manifest{Version: ver, Notes: g.Body, Assets: assets}
}

func platformFromName(name string) string {
	n := strings.ToLower(name)
	switch {
	case strings.Contains(n, "windows-amd64"), strings.Contains(n, "windows_amd64"):
		return "windows-amd64"
	case strings.Contains(n, "windows-arm64"), strings.Contains(n, "windows_arm64"):
		return "windows-arm64"
	case strings.Contains(n, "linux-amd64"), strings.Contains(n, "linux_amd64"):
		return "linux-amd64"
	case strings.Contains(n, "linux-arm64"), strings.Contains(n, "linux_arm64"):
		return "linux-arm64"
	case strings.Contains(n, "darwin-arm64"), strings.Contains(n, "darwin_arm64"):
		return "darwin-arm64"
	case strings.Contains(n, "darwin-amd64"), strings.Contains(n, "darwin_amd64"):
		return "darwin-amd64"
	default:
		return ""
	}
}

func assetURLs(cfg config.Config, ver string, asset Asset) []namedURL {
	channels := Select(cfg.Update.Mirrors)
	raw := asset.URL
	if raw == "" {
		raw = GitHubAssetURL(cfg.Update.Repo, ver, asset.Name)
	}
	out := make([]namedURL, 0, len(channels)+1)
	seen := map[string]bool{}
	add := func(name, u string) {
		if u == "" || seen[u] {
			return
		}
		seen[u] = true
		out = append(out, namedURL{Name: name, URL: u})
	}
	for _, ch := range channels {
		add(ch.Name, ch.Wrap(raw))
	}
	if asset.URL != "" && asset.URL != raw {
		add("asset", asset.URL)
	}
	return out
}

type namedURL struct {
	Name string
	URL  string
}

func raceDownload(ctx context.Context, urls []namedURL, timeout time.Duration, emit ProgressFn) (string, string, error) {
	if len(urls) == 0 {
		return "", "", errors.New("no download channels")
	}
	child, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	type hit struct {
		path string
		name string
		err  error
	}
	ch := make(chan hit, len(urls))
	var once sync.Once
	for _, u := range urls {
		u := u
		go func() {
			path, err := downloadToTemp(child, u.URL)
			if err == nil && !validZip(path) {
				os.Remove(path)
				err = errors.New("payload is not a zip (proxy returned html?)")
			}
			if err != nil {
				ch <- hit{name: u.Name, err: err}
				return
			}
			ch <- hit{path: path, name: u.Name}
		}()
	}
	var last error
	var winner string
	var winnerPath string
	pending := len(urls)
	for pending > 0 {
		h := <-ch
		pending--
		if h.err != nil {
			last = h.err
			if emit != nil {
				emit("channel_fail", h.name+": "+h.err.Error())
			}
			continue
		}
		once.Do(func() {
			winner = h.name
			winnerPath = h.path
			cancel()
		})
		if h.path != winnerPath {
			os.Remove(h.path)
		}
		if winnerPath != "" && pending == 0 {
			break
		}
	}
	if winnerPath == "" {
		if last == nil {
			last = errors.New("all download channels failed")
		}
		return "", "", last
	}
	return winnerPath, winner, nil
}

func validZip(path string) bool {
	r, err := zip.OpenReader(path)
	if err != nil {
		return false
	}
	_ = r.Close()
	return true
}

func downloadToTemp(ctx context.Context, rawURL string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "aurora/"+version.Short())
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return "", fmt.Errorf("http %d", resp.StatusCode)
	}
	f, err := os.CreateTemp("", "aurora-update-*")
	if err != nil {
		return "", err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		os.Remove(f.Name())
		return "", err
	}
	if err := f.Close(); err != nil {
		os.Remove(f.Name())
		return "", err
	}
	return f.Name(), nil
}

func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func NewPath(exe string) string { return exe + ".new" }
func OldPath(exe string) string { return exe + ".old" }

func ApplyPending(exe string) error {
	if exe == "" {
		return nil
	}
	newBin := NewPath(exe)
	st, err := os.Stat(newBin)
	if err != nil || st.IsDir() {
		pending := exe + ".pending"
		if st2, err2 := os.Stat(pending); err2 == nil && !st2.IsDir() {
			_ = os.Rename(pending, newBin)
			st, err = os.Stat(newBin)
			if err != nil || st.IsDir() {
				return nil
			}
		} else {
			return nil
		}
	}
	bak := OldPath(exe)
	_ = os.Remove(bak)
	if err := os.Rename(exe, bak); err != nil {
		if runtime.GOOS == "windows" {
			return relaunchFromNew(exe, newBin)
		}
		return err
	}
	if err := os.Rename(newBin, exe); err != nil {
		_ = os.Rename(bak, exe)
		return err
	}
	if runtime.GOOS != "windows" {
		_ = os.Chmod(exe, 0o755)
	}
	return nil
}

func replaceSelf(downloadPath, exe, assetName string) error {
	bin, err := extractBinary(downloadPath, assetName)
	if err != nil {
		return err
	}
	defer func() {
		if bin != downloadPath {
			os.Remove(bin)
		}
	}()
	tmp := NewPath(exe)
	in, err := os.Open(bin)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(tmp)
		return err
	}
	if err := out.Close(); err != nil {
		os.Remove(tmp)
		return err
	}
	bak := OldPath(exe)
	_ = os.Remove(bak)
	if err := os.Rename(exe, bak); err != nil {
		if runtime.GOOS == "windows" {
			return &PendingError{Path: tmp, Restart: true}
		}
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, exe); err != nil {
		_ = os.Rename(bak, exe)
		os.Remove(tmp)
		return err
	}
	if runtime.GOOS != "windows" {
		_ = os.Chmod(exe, 0o755)
	}
	return nil
}

type PendingError struct {
	Path    string
	Restart bool
}

func (e *PendingError) Error() string {
	return "binary is locked; next launch will finish the update from " + e.Path
}

func extractBinary(path, assetName string) (string, error) {
	if strings.HasSuffix(strings.ToLower(assetName), ".zip") || isZip(path) {
		r, err := zip.OpenReader(path)
		if err != nil {
			return "", err
		}
		defer r.Close()
		want := "aurora"
		if runtime.GOOS == "windows" {
			want = "aurora.exe"
		}
		for _, f := range r.File {
			base := filepath.Base(f.Name)
			if base != want && !strings.EqualFold(base, want) {
				continue
			}
			rc, err := f.Open()
			if err != nil {
				return "", err
			}
			tmp, err := os.CreateTemp("", "aurora-bin-*")
			if err != nil {
				rc.Close()
				return "", err
			}
			_, copyErr := io.Copy(tmp, rc)
			rc.Close()
			closeErr := tmp.Close()
			if copyErr != nil {
				os.Remove(tmp.Name())
				return "", copyErr
			}
			if closeErr != nil {
				os.Remove(tmp.Name())
				return "", closeErr
			}
			return tmp.Name(), nil
		}
		return "", fmt.Errorf("zip does not contain %s", want)
	}
	return path, nil
}

func isZip(path string) bool {
	f, err := os.Open(path)
	if err != nil {
		return false
	}
	defer f.Close()
	var hdr [4]byte
	n, _ := f.Read(hdr[:])
	return n >= 4 && hdr[0] == 'P' && hdr[1] == 'K'
}

func Newer(remote, current string) bool {
	r := parseVer(remote)
	c := parseVer(current)
	for i := 0; i < 3; i++ {
		if r[i] > c[i] {
			return true
		}
		if r[i] < c[i] {
			return false
		}
	}
	return false
}

func parseVer(s string) [3]int {
	s = strings.TrimPrefix(strings.ToLower(s), "v")
	var out [3]int
	parts := strings.Split(s, ".")
	for i := 0; i < 3 && i < len(parts); i++ {
		n := 0
		for _, ch := range parts[i] {
			if ch < '0' || ch > '9' {
				break
			}
			n = n*10 + int(ch-'0')
		}
		out[i] = n
	}
	return out
}
