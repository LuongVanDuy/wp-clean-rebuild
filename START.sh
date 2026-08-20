#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

LOG_DIR="$ROOT_DIR/logs"
STARTUP_LOG="$LOG_DIR/gui-startup-linux.log"
mkdir -p "$LOG_DIR"

export PYTHONUTF8=1
export PYTHONIOENCODING="utf-8:replace"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >>"$STARTUP_LOG" 2>/dev/null || true
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  if [[ -x "${HOME}/.local/bin/uv" ]]; then
    printf '%s\n' "${HOME}/.local/bin/uv"
    return 0
  fi
  return 1
}

printf '\nWP CLEAN REBUILD - LOCAL GUI (UBUNTU)\n'
printf '=====================================\n'

UV_EXE="$(find_uv || true)"
if [[ -z "$UV_EXE" ]] || ! "$UV_EXE" run --no-sync wpclean doctor >/dev/null 2>&1; then
  printf '[!] Runtime is not ready. Running Ubuntu setup first.\n'
  if ! "$ROOT_DIR/install.sh"; then
    printf '[X] Setup failed. See terminal output above.\n' >&2
    exit 2
  fi
  export PATH="${HOME}/.local/bin:${PATH}"
  UV_EXE="$(find_uv || true)"
fi

if [[ -z "$UV_EXE" ]]; then
  printf '[X] uv was not found after setup.\n' >&2
  exit 2
fi

printf '[OK] Runtime environment is ready.\n'
printf 'The browser should open automatically. Keep this terminal open while using the GUI.\n'
log "START wpclean.linux_runtime_entry"

"$UV_EXE" run --no-sync python -m wpclean.linux_runtime_entry
EXIT_CODE=$?
log "EXIT wpclean.linux_runtime_entry code=$EXIT_CODE"

if [[ "$EXIT_CODE" -eq 0 ]]; then
  exit 0
fi

printf '[!] Multi-project GUI stopped with exit code %s.\n' "$EXIT_CODE" >&2
printf '[!] Trying the stable fallback GUI.\n' >&2
log "START wpclean.linux_runtime_entry --stable"

"$UV_EXE" run --no-sync python -m wpclean.linux_runtime_entry --stable
STABLE_EXIT=$?
log "EXIT wpclean.linux_runtime_entry --stable code=$STABLE_EXIT"

if [[ "$STABLE_EXIT" -ne 0 ]]; then
  printf '[X] Fallback GUI also failed. Startup log: %s\n' "$STARTUP_LOG" >&2
fi
exit "$STABLE_EXIT"
