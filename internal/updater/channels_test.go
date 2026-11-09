package updater

import (
	"strings"
	"testing"

	"github.com/plxlaaa/aurora/internal/config"
)

func TestSelectResolvesNewChannels(t *testing.T) {
	chs := Select([]string{"github", "gh_proxy_com", "ghfast_top"})
	if len(chs) != 3 {
		t.Fatalf("got %d channels", len(chs))
	}
	byName := map[string]bool{}
	for _, c := range chs {
		byName[c.Name] = true
	}
	for _, want := range []string{"github", "gh_proxy_com", "ghfast_top"} {
		if !byName[want] {
			t.Fatalf("channel %q not resolved from %v", want, chs)
		}
	}
}

func TestDefaultMirrorsAllResolve(t *testing.T) {
	names := config.DefaultMirrors()
	chs := Select(names)
	if len(chs) != len(names) {
		t.Fatalf("default mirrors %v resolved to %d channels", names, len(chs))
	}
	for _, c := range chs {
		if c.Name == "gitdl" || c.Name == "ghproxy" {
			t.Fatalf("default set contains retired channel %q", c.Name)
		}
	}
}

func TestGitHubLatestAPIExcludesDeadMirrors(t *testing.T) {
	urls := GitHubLatestAPI("plxlaaa/aurora")
	joined := strings.Join(urls, " ")
	for _, dead := range []string{"mirror.ghproxy.com", "gitdl.cn"} {
		if strings.Contains(joined, dead) {
			t.Fatalf("dead prefix %q in %v", dead, urls)
		}
	}
	if len(urls) < 2 {
		t.Fatalf("expected API fallbacks, got %v", urls)
	}
}

func TestChannelWrapsAssetURL(t *testing.T) {
	for _, ch := range DefaultChannels() {
		if ch.Name == "gh_proxy_com" {
			got := ch.Wrap("https://github.com/plxlaaa/aurora/releases/download/v0.1.4/aurora-windows-amd64.zip")
			want := "https://gh-proxy.com/https://github.com/plxlaaa/aurora/releases/download/v0.1.4/aurora-windows-amd64.zip"
			if got != want {
				t.Fatalf("wrap = %q, want %q", got, want)
			}
			return
		}
	}
	t.Fatal("gh_proxy_com channel missing")
}
