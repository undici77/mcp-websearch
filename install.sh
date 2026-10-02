#!/usr/bin/env bash
# Install websearch MCP into $HOME and register it with qwen. Never fatal to qwen itself.
# bash 3.2 compatible (macOS /usr/bin/env bash).
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${WEBSEARCH_HOME:-$HOME/.qwen/websearch-mcp}"
KEY_FILE="$HOME/.qwen/websearch-keys.json"

warn() { printf '  ! %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  warn "no python3 >= 3.9 found; qwen works, search does not. Install Python and re-run."
  exit 0
fi
step "python: $($PY -V 2>&1)"

step "sync $SRC -> $DEST"
mkdir -p "$DEST" || { warn "cannot create $DEST"; exit 0; }
if command -v rsync >/dev/null 2>&1; then
  # Keep .align/manifest.json (the contract) and upstream-stub (offline fidelity);
  # drop only the big regenerable caches.
  rsync -a --delete \
    --exclude '.venv' --exclude '.git' --exclude '__pycache__' \
    --exclude '.pytest_cache' --exclude '*.egg-info' \
    --exclude '.align/upstream' --exclude '.align/refcache' \
    "$SRC/" "$DEST/" || { warn "rsync failed"; exit 0; }
else
  cp -rp "$SRC/." "$DEST/" || { warn "copy failed"; exit 0; }
  rm -rf "$DEST/.venv" "$DEST/.align/upstream" "$DEST/.align/refcache" "$DEST/.pytest_cache" 2>/dev/null || true
fi

step "venv + hash-pinned deps"
VP="$DEST/.venv/bin/python"
if [ ! -x "$VP" ]; then
  "$PY" -m venv "$DEST/.venv" || { warn "venv failed (need python3-venv on Debian)"; exit 0; }
fi
# -m pip, never .venv/bin/pip: a venv is not relocatable and its script shebangs break on a move.
if [ -f "$DEST/requirements.lock" ]; then
  "$VP" -m pip install --quiet --require-hashes --no-deps -r "$DEST/requirements.lock" \
    || { warn "hash-pinned install failed — a lock hash no longer matches PyPI. Read the diff; do not re-hash blind."; exit 0; }
  "$VP" -m pip install --quiet --no-deps -e "$DEST" \
    || { warn "editable install failed"; exit 0; }
else
  warn "no requirements.lock — installing UNPINNED. Regenerate the lock before trusting this install."
  "$VP" -m pip install --quiet -e "$DEST[dev]" || { warn "pip install failed"; exit 0; }
fi

step "generate verbatim surface + verify"
if [ -f "$DEST/.align/upstream-stub/studio/backend/core/inference/tools.py" ] \
   || [ -n "${UNSLOTH_UPSTREAM:-}" ]; then
  "$DEST/.venv/bin/python" "$DEST/scripts/build_port.py" --check \
    || "$DEST/.venv/bin/python" "$DEST/scripts/build_port.py"
else
  warn "no upstream source; fidelity check skipped (align.py will shallow-clone on first run)"
fi
"$DEST/.venv/bin/python" -m pytest -q "$DEST/tests" || warn "tests failed — search may misbehave"

step "key file"
if [ ! -f "$KEY_FILE" ]; then
  umask 077
  printf '{\n  "serpapi": ""\n}\n' > "$KEY_FILE"
fi
chmod 600 "$KEY_FILE" 2>/dev/null || true
[ -s "$KEY_FILE" ] || warn "$KEY_FILE has no serpapi key — tier 3 stays disabled (free tiers only)"

step "register with qwen"
if command -v qwen >/dev/null 2>&1; then
  qwen mcp remove websearch >/dev/null 2>&1 || true
  qwen mcp add websearch "$DEST/.venv/bin/python" "$DEST/server.py" \
    -t stdio -s user --description "Keyless web search, ported from Unsloth Studio" \
    && echo "  registered: mcp__websearch__web_search" \
    || warn "qwen mcp add failed — add it by hand from qwen-mcp-snippet.json"
else
  warn "qwen not on PATH; merge qwen-mcp-snippet.json into ~/.qwen/settings.json by hand"
fi

cat <<'DONE'

  Done. Restart the session, then /mcp should list `websearch`.
  Built-in WebSearch stays off unless tools.webSearch has a key and enabled is not false.
DONE
