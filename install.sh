#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

step() { printf '\n>> %s\n' "$1"; }
ok() { printf '[OK] %s\n' "$1"; }
warn() { printf '[!] %s\n' "$1" >&2; }
fail() { printf '[X] %s\n' "$1" >&2; }

ask_yes_no() {
  local prompt="$1"
  if [[ "${WPCLEAN_ASSUME_YES:-0}" == "1" ]]; then
    return 0
  fi
  local answer
  read -r -p "$prompt [Y/n] " answer || true
  answer="${answer:-y}"
  case "${answer,,}" in
    y|yes|c|co) return 0 ;;
    *) return 1 ;;
  esac
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

install_uv() {
  step "Installing uv from the official Astral installer"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    fail "curl or wget is required to install uv."
    fail "Install one of them first, for example: sudo apt install curl"
    return 2
  fi
}

printf '\nWP CLEAN REBUILD - UBUNTU SETUP\n'
printf '================================\n'

if [[ "$(uname -s)" != "Linux" ]]; then
  warn "This installer is intended for Ubuntu/Linux."
fi

step "STEP 1 - Check uv"
UV_EXE="$(find_uv || true)"
if [[ -z "$UV_EXE" ]]; then
  warn "uv is not installed."
  if ! ask_yes_no "Install uv automatically?"; then
    fail "Cannot continue without uv."
    exit 2
  fi
  install_uv
  export PATH="${HOME}/.local/bin:${PATH}"
  UV_EXE="$(find_uv || true)"
fi

if [[ -z "$UV_EXE" ]]; then
  fail "uv installer finished but uv was not found."
  exit 2
fi
ok "uv: $UV_EXE"

step "STEP 2 - Check Python 3.13"
if ! "$UV_EXE" python find 3.13 >/dev/null 2>&1; then
  warn "Python 3.13 managed by uv is not installed."
  if ! ask_yes_no "Install Python 3.13 automatically?"; then
    fail "Cannot continue without Python 3.13."
    exit 2
  fi
  "$UV_EXE" python install 3.13
fi

if ! "$UV_EXE" python find 3.13 >/dev/null 2>&1; then
  fail "Python 3.13 is still unavailable after installation."
  exit 2
fi
ok "Python 3.13 is ready."

step "STEP 3 - Sync project dependencies"
"$UV_EXE" sync

step "STEP 4 - Run environment self-check"
"$UV_EXE" run --no-sync wpclean doctor
ok "Runtime environment is ready."

printf '\nSetup completed successfully.\n'
printf 'Start the GUI with: ./START.sh\n'
printf 'Run the CLI with:  ./wpclean.sh --help\n'
