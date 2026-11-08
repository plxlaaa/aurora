package setup

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/plxlaaa/aurora/internal/config"
)

type Options struct {
	In  *bufio.Reader
	Out io.Writer
	Cfg config.Config
}

type Result struct {
	Config   config.Config
	KeyName  string
	KeyValue string
}

func Run(o Options) (Result, error) {
	if o.In == nil {
		return Result{}, fmt.Errorf("no input reader")
	}
	if o.Out == nil {
		o.Out = io.Discard
	}
	cfg := o.Cfg

	prov, err := selectProvider(o, cfg)
	if err != nil {
		return Result{}, err
	}
	cfg.LLM.Provider = prov.ID

	baseDefault := prov.BaseURL
	if baseDefault == "" {
		baseDefault = cfg.LLM.BaseURL
	}
	base, err := prompt(o, "API base URL", baseDefault)
	if err != nil {
		return Result{}, err
	}
	base = strings.TrimRight(strings.TrimSpace(base), "/")
	if base == "" {
		return Result{}, fmt.Errorf("base URL is required")
	}
	cfg.LLM.BaseURL = base

	modelDefault := prov.DefaultModel
	if modelDefault == "" {
		modelDefault = cfg.LLM.Model
	}
	model, err := prompt(o, "Model", modelDefault)
	if err != nil {
		return Result{}, err
	}
	if model == "" {
		return Result{}, fmt.Errorf("model is required")
	}
	cfg.LLM.Model = model

	keyName := cfg.LLM.APIKeyEnv
	if keyName == "" {
		keyName = "AURORA_API_KEY"
	}
	cfg.LLM.APIKeyEnv = keyName

	key, err := prompt(o, "API key (leave empty to keep using environment variables)", "")
	if err != nil {
		return Result{}, err
	}
	key = strings.TrimSpace(key)

	if key != "" {
		test, err := promptYesNo(o, "Test connection now", true)
		if err != nil {
			return Result{}, err
		}
		if test {
			res := probe(context.Background(), base, key)
			fmt.Fprintf(o.Out, "  %s\n", res.Message)
			if !res.OK {
				fmt.Fprintf(o.Out, "  fix the values above or run aurora setup again to retry\n")
			}
		}
	}

	save, err := promptYesNo(o, "Save this configuration", true)
	if err != nil {
		return Result{}, err
	}
	if !save {
		return Result{}, fmt.Errorf("setup cancelled")
	}
	return Result{Config: cfg, KeyName: keyName, KeyValue: key}, nil
}

func PrintSummary(out io.Writer, r Result, cfgPath, keyPath string) {
	cfg := r.Config
	fmt.Fprintln(out, "setup complete")
	fmt.Fprintln(out, "  config   ", cfgPath)
	fmt.Fprintf(out, "  provider  %s\n", cfg.LLM.Provider)
	fmt.Fprintf(out, "  base_url  %s\n", cfg.LLM.BaseURL)
	fmt.Fprintf(out, "  model     %s\n", cfg.LLM.Model)
	if r.KeyValue != "" {
		fmt.Fprintf(out, "  api_key   %s stored in %s as %s\n", maskKey(r.KeyValue), keyPath, r.KeyName)
		return
	}
	fmt.Fprintf(out, "  api_key   not stored; set %s or re-run aurora setup\n", r.KeyName)
}

func maskKey(s string) string {
	if len(s) <= 4 {
		return "****"
	}
	return s[:4] + "****"
}

func selectProvider(o Options, cfg config.Config) (config.Provider, error) {
	providers := config.Providers()
	def := 0
	fmt.Fprintln(o.Out, "select provider:")
	for i, p := range providers {
		if p.ID == cfg.LLM.Provider {
			def = i
		}
		hint := p.BaseURL
		if hint == "" {
			hint = "enter URL manually"
		}
		if p.DefaultModel != "" {
			hint += "  (default model " + p.DefaultModel + ")"
		}
		fmt.Fprintf(o.Out, "  %d) %-16s %s\n", i+1, p.Name, hint)
	}
	for {
		raw, err := prompt(o, "provider", strconv.Itoa(def+1))
		if err != nil {
			return config.Provider{}, err
		}
		n, err := strconv.Atoi(strings.TrimSpace(raw))
		if err != nil || n < 1 || n > len(providers) {
			fmt.Fprintf(o.Out, "  enter a number between 1 and %d\n", len(providers))
			continue
		}
		return providers[n-1], nil
	}
}

func prompt(o Options, label, def string) (string, error) {
	if def != "" {
		fmt.Fprintf(o.Out, "%s [%s]: ", label, def)
	} else {
		fmt.Fprintf(o.Out, "%s: ", label)
	}
	line, eof, err := readLine(o)
	if err != nil {
		return "", err
	}
	if line == "" && eof {
		return "", fmt.Errorf("setup aborted")
	}
	if line == "" {
		return def, nil
	}
	return line, nil
}

func promptYesNo(o Options, label string, def bool) (bool, error) {
	hint := "[Y/n]"
	if !def {
		hint = "[y/N]"
	}
	fmt.Fprintf(o.Out, "%s %s: ", label, hint)
	line, eof, err := readLine(o)
	if err != nil {
		return false, err
	}
	if line == "" && eof {
		return false, fmt.Errorf("setup aborted")
	}
	switch strings.ToLower(line) {
	case "":
		return def, nil
	case "y", "yes":
		return true, nil
	case "n", "no":
		return false, nil
	}
	fmt.Fprintln(o.Out, "  please answer y or n")
	return promptYesNo(o, label, def)
}

func readLine(o Options) (string, bool, error) {
	line, err := o.In.ReadString('\n')
	eof := false
	if err == io.EOF {
		eof = true
	} else if err != nil {
		return "", false, err
	}
	return strings.TrimSpace(line), eof, nil
}

type probeResult struct {
	OK      bool
	Message string
}

func probe(parent context.Context, baseURL, apiKey string) probeResult {
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(baseURL, "/")+"/models", nil)
	if err != nil {
		return probeResult{Message: "probe error: " + err.Error()}
	}
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return probeResult{Message: "probe failed: " + err.Error()}
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	switch {
	case resp.StatusCode < 300:
		return probeResult{OK: true, Message: "connection ok, key accepted"}
	case resp.StatusCode == 401 || resp.StatusCode == 403:
		return probeResult{Message: fmt.Sprintf("endpoint rejected the API key (HTTP %d)", resp.StatusCode)}
	default:
		return probeResult{OK: true, Message: fmt.Sprintf("endpoint reachable (HTTP %d), key not verified", resp.StatusCode)}
	}
}
