VERSION ?= 0.1.4
COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
DATE ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
LDFLAGS := -s -w -X github.com/plxlaaa/aurora/internal/version.Version=$(VERSION) -X github.com/plxlaaa/aurora/internal/version.Commit=$(COMMIT) -X github.com/plxlaaa/aurora/internal/version.Date=$(DATE)

.PHONY: build windows test pack fmt vet

build:
	CGO_ENABLED=0 go build -trimpath -ldflags "$(LDFLAGS)" -o bin/aurora ./cmd/aurora

windows:
	CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "$(LDFLAGS)" -o bin/aurora-windows-amd64.exe ./cmd/aurora
	CGO_ENABLED=0 GOOS=windows GOARCH=arm64 go build -trimpath -ldflags "$(LDFLAGS)" -o bin/aurora-windows-arm64.exe ./cmd/aurora

test:
	go test ./...

fmt:
	gofmt -w cmd internal

vet:
	go vet ./...

pack:
	VERSION=$(VERSION) COMMIT=$(COMMIT) DATE=$(DATE) bash scripts/pack.sh
