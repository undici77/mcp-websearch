#!/usr/bin/env bash
# Install websearch MCP into $HOME and register it with qwen. Never fatal to qwen itself: a failed
# port unregisters rather than registering a dead server, and exits 1 so the caller can see it.
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
  # cp honours no excludes, so the source venv came along; a venv is not relocatable.
  rm -rf "$DEST/.venv" 2>/dev/null || true
fi
# An rsync --exclude also shields the matching DEST path from --delete, so a .align/upstream left by
# an earlier install survives every later one — and locate() prefers it over the anchored stub.
# Neither cache is the anchor; drop both so locate() resolves to the stub that just synced.
rm -rf "$DEST/.align/upstream" "$DEST/.align/refcache" "$DEST/.pytest_cache" 2>/dev/null || true

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
BROKEN=0
GEN="$DEST/scripts/build_port.py"
if [ -f "$DEST/.align/upstream-stub/studio/backend/core/inference/tools.py" ] \
   || [ -n "${UNSLOTH_UPSTREAM:-}" ]; then
  # Echo what build_port resolved to: the located source is the thing that goes stale.
  OUT="$("$VP" "$GEN" --check 2>&1)" || OUT="$("$VP" "$GEN" 2>&1)"
  printf '%s\n' "$OUT" | sed 's/^/  /'
  # The manifest records how many symbols the anchor yields. Fewer means build_port read a source
  # older than the anchor — the exact stale-clone failure above — so the port is incomplete, not fresh.
  WANT="$("$VP" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("generated_symbols",0))' "$DEST/.align/manifest.json" 2>/dev/null)"
  HAVE="$(printf '%s\n' "$OUT" | sed -n 's/.*[^0-9]\([0-9][0-9]*\) symbols.*/\1/p' | head -1)"
  if [ -z "$WANT" ] || [ "$HAVE" != "$WANT" ]; then
    warn "generated ${HAVE:-?} symbols, the anchor yields ${WANT:-?} — the upstream source build_port found is stale, not the anchor"
    BROKEN=1
  fi
else
  warn "no upstream source; fidelity check skipped (align.py will shallow-clone on first run)"
fi
"$DEST/.venv/bin/python" -m pytest -q "$DEST/tests" || { warn "tests failed"; BROKEN=1; }

step "key file"
if [ ! -f "$KEY_FILE" ]; then
  umask 077
  printf '{\n  "serpapi": ""\n}\n' > "$KEY_FILE"
fi
chmod 600 "$KEY_FILE" 2>/dev/null || true
[ -s "$KEY_FILE" ] || warn "$KEY_FILE has no serpapi key — tier 3 stays disabled (free tiers only)"

step "register with qwen"
if [ "$BROKEN" -ne 0 ]; then
  # A registered server that fails at import shows up in /mcp as a dead tool the model may still
  # reach for. Unregister it and leave qwen on its built-in path until the port is fixed.
  qwen mcp remove websearch >/dev/null 2>&1 || true
  warn "NOT registered — the build above is incomplete. Fix it and re-run install.sh."
elif command -v qwen >/dev/null 2>&1; then
  qwen mcp remove websearch >/dev/null 2>&1 || true
  qwen mcp add websearch "$DEST/.venv/bin/python" "$DEST/server.py" \
    -t stdio -s user --description "Keyless web search, ported from Unsloth Studio" \
    && echo "  registered: mcp__websearch__web_search" \
    || warn "qwen mcp add failed — add it by hand from qwen-mcp-snippet.json"
else
  warn "qwen not on PATH; merge qwen-mcp-snippet.json into ~/.qwen/settings.json by hand"
fi

if [ "$BROKEN" -ne 0 ]; then
  printf '\n  NOT installed. The port did not verify — qwen is untouched and still on its built-in path.\n'
  printf '  Re-run install.sh after fixing the reported symbol gap.\n'
  exit 1
fi

cat <<'DONE'

  Done. Restart the session, then /mcp should list `websearch`.
  Built-in WebSearch stays off unless tools.webSearch has a key and enabled is not false.
DONE
