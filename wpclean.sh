#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if command -v uv >/dev/null 2>&1; then
  UV_EXE="$(command -v uv)"
elif [[ -x "${HOME}/.local/bin/uv" ]]; then
  UV_EXE="${HOME}/.local/bin/uv"
else
  printf 'uv runtime was not found.\n' >&2
  printf 'Run ./install.sh first.\n' >&2
  exit 1
fi

exec "$UV_EXE" run --no-sync wpclean "$@"
