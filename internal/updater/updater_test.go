package updater

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/plxlaaa/aurora/internal/config"
)

func TestNewer(t *testing.T) {
	if !Newer("0.2.0", "0.1.0") {
		t.Fatal("expected 0.2.0 > 0.1.0")
	}
	if Newer("0.1.0", "0.1.0") {
		t.Fatal("equal versions should not be newer")
	}
	if Newer("0.1.0", "0.2.0") {
		t.Fatal("0.1.0 should not be newer than 0.2.0")
	}
}

func TestSelectChannels(t *testing.T) {
	chs := Select([]string{"github", "ghproxy"})
	if len(chs) != 2 {
		t.Fatalf("got %d channels", len(chs))
	}
}

func TestParseVer(t *testing.T) {
	v := parseVer("v1.2.3-beta")
	if v != [3]int{1, 2, 3} {
		t.Fatalf("got %#v", v)
	}
}

func TestRaceDownloadPrefersFastChannel(t *testing.T) {
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(400 * time.Millisecond)
		w.Write([]byte("slow-body"))
	}))
	defer slow.Close()
	fast := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("fast-body"))
	}))
	defer fast.Close()

	ctx := context.Background()
	path, name, err := raceDownload(ctx, []namedURL{
		{Name: "slow", URL: slow.URL},
		{Name: "fast", URL: fast.URL},
	}, 2*time.Second, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(path)
	if name != "fast" {
		t.Fatalf("winner = %s, want fast", name)
	}
	raw, _ := os.ReadFile(path)
	if string(raw) != "fast-body" {
		t.Fatalf("body = %q", raw)
	}
}

func TestGetManifestFromGitHubAPI(t *testing.T) {
	payload := githubRelease{
		TagName: "v0.9.0",
		Body:    "notes",
		Assets: []struct {
			Name               string `json:"name"`
			BrowserDownloadURL string `json:"browser_download_url"`
			Size               int64  `json:"size"`
		}{
			{Name: "aurora-windows-amd64.zip", BrowserDownloadURL: "http://example/a.zip", Size: 10},
		},
	}
	raw, _ := json.Marshal(payload)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(raw)
	}))
	defer srv.Close()
	man, err := getManifest(context.Background(), srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	if man.Version != "0.9.0" {
		t.Fatalf("version=%s", man.Version)
	}
	if _, ok := man.Assets["windows-amd64"]; !ok {
		t.Fatal("missing windows-amd64 asset")
	}
}

func TestFileSHA256AndZipExtract(t *testing.T) {
	dir := t.TempDir()
	zipPath := filepath.Join(dir, "a.zip")
	zf, err := os.Create(zipPath)
	if err != nil {
		t.Fatal(err)
	}
	zw := zip.NewWriter(zf)
	name := "aurora"
	if runtime.GOOS == "windows" {
		name = "aurora.exe"
	}
	w, err := zw.Create(name)
	if err != nil {
		t.Fatal(err)
	}
	io.WriteString(w, "binary-bytes")
	zw.Close()
	zf.Close()

	bin, err := extractBinary(zipPath, filepath.Base(zipPath))
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(bin)
	raw, _ := os.ReadFile(bin)
	if string(raw) != "binary-bytes" {
		t.Fatalf("extracted %q", raw)
	}
	sum, err := fileSHA256(zipPath)
	if err != nil {
		t.Fatal(err)
	}
	h := sha256.New()
	b, _ := os.ReadFile(zipPath)
	h.Write(b)
	if sum != hex.EncodeToString(h.Sum(nil)) {
		t.Fatal("sha mismatch")
	}
}

func TestManifestJSONShape(t *testing.T) {
	cfg := config.Default()
	if len(cfg.Update.ManifestURLs) == 0 {
		t.Fatal("expected default manifest urls")
	}
	if len(Select(cfg.Update.Mirrors)) < 3 {
		t.Fatal("expected multiple mirrors")
	}
}
