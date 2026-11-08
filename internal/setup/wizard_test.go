package setup

import (
	"bufio"
	"bytes"
	"strings"
	"testing"

	"github.com/plxlaaa/aurora/internal/config"
)

func runWizard(t *testing.T, input string, cfg config.Config) (Result, string, error) {
	t.Helper()
	var out bytes.Buffer
	res, err := Run(Options{In: bufio.NewReader(strings.NewReader(input)), Out: &out, Cfg: cfg})
	return res, out.String(), err
}

func TestWizardHappyPath(t *testing.T) {
	input := "2\n\n\nsk-test1234\nn\ny\n"
	res, out, err := runWizard(t, input, config.Default())
	if err != nil {
		t.Fatal(err)
	}
	if res.Config.LLM.Provider != "deepseek" {
		t.Fatalf("provider = %q", res.Config.LLM.Provider)
	}
	if res.Config.LLM.BaseURL != "https://api.deepseek.com/v1" {
		t.Fatalf("base url = %q", res.Config.LLM.BaseURL)
	}
	if res.Config.LLM.Model != "deepseek-chat" {
		t.Fatalf("model = %q", res.Config.LLM.Model)
	}
	if res.KeyName != "AURORA_API_KEY" || res.KeyValue != "sk-test1234" {
		t.Fatalf("key = %q/%q", res.KeyName, res.KeyValue)
	}
	if !strings.Contains(out, "DeepSeek") {
		t.Fatalf("provider list missing DeepSeek: %s", out)
	}
}

func TestWizardCustomEndpointTrimsSlash(t *testing.T) {
	input := "7\nhttp://localhost:9999/v1/\nmy-model\n\ny\n"
	res, _, err := runWizard(t, input, config.Default())
	if err != nil {
		t.Fatal(err)
	}
	if res.Config.LLM.Provider != "custom" {
		t.Fatalf("provider = %q", res.Config.LLM.Provider)
	}
	if res.Config.LLM.BaseURL != "http://localhost:9999/v1" {
		t.Fatalf("base url = %q", res.Config.LLM.BaseURL)
	}
	if res.Config.LLM.Model != "my-model" {
		t.Fatalf("model = %q", res.Config.LLM.Model)
	}
	if res.KeyValue != "" {
		t.Fatalf("expected empty key, got %q", res.KeyValue)
	}
}

func TestWizardPrefillKeepsProvider(t *testing.T) {
	cfg := config.Default()
	cfg.LLM.Provider = "moonshot"
	cfg.LLM.Model = "custom-model"
	input := "\n\ncustom-model\nsk-k\nn\ny\n"
	res, _, err := runWizard(t, input, cfg)
	if err != nil {
		t.Fatal(err)
	}
	if res.Config.LLM.Provider != "moonshot" {
		t.Fatalf("provider = %q", res.Config.LLM.Provider)
	}
	if res.Config.LLM.BaseURL != "https://api.moonshot.cn/v1" {
		t.Fatalf("base url = %q", res.Config.LLM.BaseURL)
	}
	if res.Config.LLM.Model != "custom-model" {
		t.Fatalf("model = %q", res.Config.LLM.Model)
	}
}

func TestWizardAbortOnEOF(t *testing.T) {
	if _, _, err := runWizard(t, "", config.Default()); err == nil {
		t.Fatal("expected abort error on EOF")
	}
}

func TestWizardCancelSavesNothing(t *testing.T) {
	input := "1\n\n\nsk-x\nn\nn\n"
	if _, _, err := runWizard(t, input, config.Default()); err == nil {
		t.Fatal("expected cancel error")
	}
}

func TestWizardInvalidSelectionReprompts(t *testing.T) {
	input := "99\n2\n\n\nsk-x\nn\ny\n"
	res, _, err := runWizard(t, input, config.Default())
	if err != nil {
		t.Fatal(err)
	}
	if res.Config.LLM.Provider != "deepseek" {
		t.Fatalf("provider = %q", res.Config.LLM.Provider)
	}
}

func TestPrintSummaryMasksKey(t *testing.T) {
	cfg := config.Default()
	cfg.LLM.Provider = "deepseek"
	var out bytes.Buffer
	res := Result{Config: cfg, KeyName: "AURORA_API_KEY", KeyValue: "sk-secret-value"}
	PrintSummary(&out, res, "/tmp/config.json", "/tmp/.env")
	got := out.String()
	if strings.Contains(got, "sk-secret-value") {
		t.Fatalf("summary leaked key: %s", got)
	}
	if !strings.Contains(got, "sk-s****") {
		t.Fatalf("summary missing masked key: %s", got)
	}
}
