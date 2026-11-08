package config

import "strings"

type Provider struct {
	ID           string
	Name         string
	BaseURL      string
	DefaultModel string
}

func Providers() []Provider {
	return []Provider{
		{ID: "openai", Name: "OpenAI", BaseURL: "https://api.openai.com/v1", DefaultModel: "gpt-4o-mini"},
		{ID: "deepseek", Name: "DeepSeek", BaseURL: "https://api.deepseek.com/v1", DefaultModel: "deepseek-chat"},
		{ID: "zhipu", Name: "Zhipu GLM", BaseURL: "https://open.bigmodel.cn/api/paas/v4", DefaultModel: "glm-4-flash"},
		{ID: "moonshot", Name: "Moonshot Kimi", BaseURL: "https://api.moonshot.cn/v1", DefaultModel: "moonshot-v1-8k"},
		{ID: "minimax", Name: "MiniMax", BaseURL: "https://api.minimaxi.com/v1", DefaultModel: "MiniMax-M2"},
		{ID: "ollama", Name: "Ollama (local)", BaseURL: "http://localhost:11434/v1", DefaultModel: "qwen3:4b"},
		{ID: "custom", Name: "Custom endpoint"},
	}
}

func FindProvider(id string) (Provider, bool) {
	id = strings.TrimSpace(strings.ToLower(id))
	for _, p := range Providers() {
		if p.ID == id {
			return p, true
		}
	}
	return Provider{}, false
}
