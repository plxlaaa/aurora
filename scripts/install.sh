#!/usr/bin/env bash
set -euo pipefail

REPO="${AURORA_REPO:-plxlaaa/aurora}"
VERSION="${AURORA_VERSION:-latest}"
INSTALL_DIR="${AURORA_INSTALL_DIR:-$HOME/.local/bin}"

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) arch="amd64" ;;
  aarch64|arm64) arch="arm64" ;;
  *) echo "unsupported arch: $arch" >&2; exit 1 ;;
esac

asset="aurora-${os}-${arch}.zip"
if [ "$VERSION" = "latest" ]; then
  raw="https://github.com/${REPO}/releases/latest/download/${asset}"
else
  raw="https://github.com/${REPO}/releases/download/v${VERSION#v}/${asset}"
fi

mirrors=(
  "$raw"
  "https://ghproxy.net/$raw"
  "https://mirror.ghproxy.com/$raw"
  "https://gitdl.cn/$raw"
  "https://gh.ddlc.top/$raw"
)

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
ok=0
for url in "${mirrors[@]}"; do
  echo "try $url"
  if curl -fsSL --max-time 20 "$url" -o "$tmp/$asset"; then
    ok=1
    break
  fi
done
if [ "$ok" != 1 ]; then
  echo "all download channels failed" >&2
  exit 1
fi

unzip -o "$tmp/$asset" -d "$tmp/out" >/dev/null
mkdir -p "$INSTALL_DIR"
install -m 0755 "$tmp/out/aurora" "$INSTALL_DIR/aurora"
echo "installed $INSTALL_DIR/aurora"
