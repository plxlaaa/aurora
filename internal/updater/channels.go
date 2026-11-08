package updater

import (
	"fmt"
	"net/url"
	"strings"
)

type Channel struct {
	Name string
	Wrap func(rawURL string) string
}

func DefaultChannels() []Channel {
	return []Channel{
		{Name: "github", Wrap: identity},
		{Name: "ghproxy_net", Wrap: prefix("https://ghproxy.net/")},
		{Name: "ghproxy", Wrap: prefix("https://mirror.ghproxy.com/")},
		{Name: "gitdl", Wrap: prefix("https://gitdl.cn/")},
		{Name: "gh_ddlc", Wrap: prefix("https://gh.ddlc.top/")},
		{Name: "ghproxy_cc", Wrap: prefix("https://ghproxy.cc/")},
	}
}

func Select(names []string) []Channel {
	all := DefaultChannels()
	if len(names) == 0 {
		return all
	}
	wanted := map[string]bool{}
	for _, n := range names {
		wanted[strings.ToLower(strings.TrimSpace(n))] = true
	}
	out := make([]Channel, 0, len(names))
	for _, ch := range all {
		if wanted[ch.Name] {
			out = append(out, ch)
		}
	}
	if len(out) == 0 {
		return all
	}
	return out
}

func identity(rawURL string) string { return rawURL }

func prefix(p string) func(string) string {
	return func(rawURL string) string {
		if strings.HasPrefix(rawURL, p) {
			return rawURL
		}
		return p + rawURL
	}
}

func GitHubAssetURL(repo, tag, asset string) string {
	tag = strings.TrimPrefix(tag, "v")
	return fmt.Sprintf("https://github.com/%s/releases/download/v%s/%s", repo, tag, asset)
}

func GitHubLatestAPI(repo string) []string {
	api := "https://api.github.com/repos/" + repo + "/releases/latest"
	return []string{
		api,
		"https://ghproxy.net/" + api,
		"https://mirror.ghproxy.com/" + api,
	}
}

func Encode(raw string) string {
	return url.QueryEscape(raw)
}
