#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/dist"
VERSION="${VERSION:-0.2.0}"
COMMIT="${COMMIT:-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)}"
DATE="${DATE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

mkdir -p "$DIST"

targets=(
  "windows/amd64"
  "windows/arm64"
  "linux/amd64"
  "linux/arm64"
  "darwin/amd64"
  "darwin/arm64"
)

ldflags="-s -w -X github.com/plxlaaa/aurora/internal/version.Version=${VERSION} -X github.com/plxlaaa/aurora/internal/version.Commit=${COMMIT} -X github.com/plxlaaa/aurora/internal/version.Date=${DATE}"

for t in "${targets[@]}"; do
  os="${t%/*}"
  arch="${t#*/}"
  name="aurora"
  if [ "$os" = "windows" ]; then
    name="aurora.exe"
  fi
  outdir="$DIST/aurora-${os}-${arch}"
  mkdir -p "$outdir"
  echo "build $os/$arch"
  CGO_ENABLED=0 GOOS="$os" GOARCH="$arch" go build -trimpath -ldflags "$ldflags" -o "$outdir/$name" "$ROOT/cmd/aurora"
  cp "$ROOT/scripts/install.ps1" "$DIST/install.ps1"
  cp "$ROOT/scripts/install.cmd" "$DIST/install.cmd"
  cp "$ROOT/scripts/install.sh" "$DIST/install.sh"
  python3 - "$outdir/$name" "$DIST/aurora-${os}-${arch}.zip" "$name" <<'PY'
import sys, zipfile
src, dest, arc = sys.argv[1], sys.argv[2], sys.argv[3]
with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(src, arc)
PY
done

export DIST="$DIST"
export VERSION
python3 - <<'PY'
import hashlib, json, os, pathlib
root = pathlib.Path(os.environ.get("DIST", "dist"))
if not root.exists():
    root = pathlib.Path("dist")
version = os.environ.get("VERSION", "0.1.0")
assets = {}
for z in sorted(root.glob("aurora-*.zip")):
    name = z.name
    plat = name[len("aurora-"):-len(".zip")]
    h = hashlib.sha256(z.read_bytes()).hexdigest()
    assets[plat] = {
        "name": name,
        "url": f"https://github.com/plxlaaa/aurora/releases/download/v{version}/{name}",
        "sha256": h,
        "size": z.stat().st_size,
    }
man = {"version": version, "notes": "Aurora terminal agent", "assets": assets}
(root / "manifest.json").write_text(json.dumps(man, indent=2) + "\n")
print("wrote", root / "manifest.json")
PY
