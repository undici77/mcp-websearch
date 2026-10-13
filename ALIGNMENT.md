# ALIGNMENT — keeping this MCP on top of unsloth/upstream

**The command:**

```bash
scripts/align.py            # fetch origin/main, report, exit non-zero if work is owed
scripts/align.py --diff     # print the upstream body (and safety findings) for anything flagged
scripts/align.py --sync     # DO it: the whole ritual against one frozen sha, refusing at any mismatch
```

It reads **named symbols only**, never the 22k-line `tools.py` whole. A report takes about 1s. A
`--sync` takes about a minute, most of it the live egress gates.

## When align is required

Not optional, not "when convenient". Run it at each of these:

| trigger | do |
|---|---|
| about to edit anything in `unsloth_search/` | `scripts/align.py` **first** — a stale anchor makes the edit a divergence you will have to unwind |
| touched a `vendored` file | `align.py --diff`. A vendored file is byte-exact or it is wrong; there is no third state |
| the `ddgs` pin changed | `align.py` **and** `approved_engines()` — the live engine registry is an output of the pin |
| `anchor` ≠ `origin/main` in any output | full ritual. A stale anchor **is** owed work, not a status line |
| any `REVIEW` row | read that churn with `--diff`, re-justify or port, then `--bump` |
| shipping, tagging, handing off, or opening a PR on this port | `align.py --sync`, ending in `ALIGNED.` |
| you last aligned more than a week ago | `align.py`. `main` moved; assume it moved in the watched surface |
| someone says `align current MCP with upstream/main` | `align.py --sync`, this turn |

The trigger is the **anchor**, not memory. `align.py` costs ~1s; not running it costs a port that is
quietly behind upstream on the one surface carrying the SSRF gates and the tier allowlist.

## `align` means DO it, not report it

`align.py` is a **gate**, not a deliverable. Read the **printed line**, not the exit code. Exit `0`
covers two different states (the stale-anchor line also exits `0`), and a lone `REVIEW` exits `0`
because only DRIFTED, BROKEN and unacked SAFETY count as owed.

| printed | exit | state | you must |
|---|---|---|---|
| `ALIGNED.` | `0` | anchor == `origin/main`, nothing owed | report the anchor + counts. **Stop.** |
| `Aligned, but anchored to an older commit.` | `0` | content matches, **anchor is stale** | `align.py --sync` |
| `REVIEW adapted …` | `0` | upstream churned an adapted symbol | `--diff`, read it, re-justify in the doc **or** port it into `search.py`/`fetch.py`, then `--sync --reviewed` |
| `SAFETY … not yet acknowledged` | `1` | upstream brought a risky feature the port lacks | read it (`--diff`), then `--sync --ack`, write a reason for each entry, `--sync` again. **Never ack unread.** |
| `DRIFTED` / `BROKEN` / `NEW?` | `1` | work owed | `align.py --sync` **in this same turn**. Then show `ALIGNED.` |

A green exit is not an alignment, and "here is what you should do next" is not an alignment. Run
`--sync` until it prints `ALIGNED.`, then report. Asking permission to apply a copy that the doc
already prescribes is the failure mode this section exists to kill.

Three exceptions, because they are genuinely yours to make and not mine:

- a **new** upstream capability (POST, binary mode, per-hop headers) that expands scope;
- anything that would weaken an invariant below;
- **every SAFETY finding.** An ack is a human statement that the code was read. An agent may prepare
  the entries with `--ack` and show the diff, but the reason is the maintainer's to write.

Everything else is mechanical: do it.

---

## Why drift is structurally hard here, not just unlikely

The verbatim surface is **generated**, not copied by hand.

```
studio/backend/core/inference/tools.py
        │  scripts/build_port.py  (AST extraction, 73 symbols, upstream line order)
        ▼
unsloth_search/upstream_verbatim.py      ← GENERATED, DO NOT EDIT
        │  imports, unchanged
        ▼
unsloth_search/fetch.py · search.py      ← the hand-written adapted functions
```

A transcription error is no longer possible in 73 of the 79 ported symbols: they are emitted by a
script from upstream's AST, and the same upstream commit always produces the same sha256.

## The four surfaces

| surface | count | test | when it shouts |
|---|---|---|---|
| `generated` | 73 | re-extract from upstream, compare each symbol's AST | upstream changed → `scripts/build_port.py` fixes it |
| `vendored` | 2 | sha256 both ways (`web_access_policy.py`, `_html_to_md.py`) | upstream changed → `cp` |
| `adapted` | 6 | upstream fingerprint vs the one recorded at sync | upstream touched it → read and re-justify |
| `out-of-scope` / `annotated` | 9 | presence + churn | upstream grew the feature, or deleted it |
| `safety` | all of the above | risky features new vs the **committed** port (`scripts/safety_diff.py`) | upstream added exec/process/fs/socket/env/host/blob → a human acks with a reason |

`adapted` never claims equality — local code differs on purpose. It only flags upstream churn, which is
what keeps the report from crying wolf.

`.align/manifest.json` is **committed**: it is the contract (anchor commit + adapted fingerprints +
vendored hashes + `stub_sha256`). `.align/safety-ack.json` is committed too: it records every risky
feature a human let in, and why.

## The ritual is one command

```bash
scripts/align.py --sync          # then review `git diff` and commit the result as one commit
```

What it does, in order. It stops at the first failure, and nothing is anchored unless every step passed:

| # | step | refuses when |
|---|---|---|
| 1 | fetch, then **freeze** `origin/main` to a sha; every later step reads that sha, never the ref | the clone's `origin` is not `github.com/unslothai/unsloth` |
| 2 | **safety gate**, before a byte is written (see below) | a new risky feature has no written reason |
| 3 | check out the frozen sha (the manual steps and `build_port.py` read the tree) | HEAD ≠ sha afterwards |
| 4 | regenerate `upstream_verbatim.py` from the sha, plus the symbol-closure check | fewer symbols than `SYMBOLS`, or any unbound name |
| 5 | copy both vendored files and refresh `.align/upstream-stub/` from the sha | — |
| 6 | report against the sha | DRIFTED/BROKEN remain, `NEW?` symbols, or `REVIEW` without `--reviewed` |
| 7 | pytest, then `check_egress_mcp.py` and `check_egress_mcp.py --wayback` | any failure (`--no-egress` skips the gates and says so in the final line) |
| 8 | write the manifest (anchor, fingerprints, vendored and stub hashes), then pytest again, including the stub pin | any failure |
| 9 | print checkout / ref / manifest | the three shas disagree |

`main` moving mid-run no longer matters: step 1 freezes it. The old traps are now refusals:

- **A stale checkout** (`git fetch` checks nothing out) is caught by step 3.
- **A stale stub** is caught by `test_stub_is_pinned_to_the_anchor`, which compares it to `manifest.stub_sha256`.
- **A missing helper** (a ported symbol calling an unlisted one) is caught by step 4 and by `test_generated_file_binds_every_name_it_uses`.

**`--bump` still exists**, for a manual run, but it refuses unless the checkout HEAD is `origin/main`.
Prefer `--sync`.

A vendored mismatch is always fixed by copying the ref's file, and `--sync` does that. The label only
says which side moved: `upstream changed` means upstream's hash differs from the recorded one, and
`local edited` means it doesn't.

## The safety gate

`align` proves the port **equals** upstream. It cannot tell you upstream is **safe**: a compromised
commit would regenerate and go green. `scripts/safety_diff.py` closes that gap mechanically.

- **Accepted** is the generated and vendored files as **committed** here (`git show HEAD:`). Running
  `build_port.py` by hand cannot launder a change into the baseline.
- **Candidate** is the same surface as upstream has it at the sha under review, through the same extraction.
- **Features**, per symbol and per vendored file:

  | category | what counts |
  |---|---|
  | code execution | `eval`, `exec`, `compile`, `__import__`, `importlib`, `runpy` |
  | processes and signals | `subprocess`, `pty`, `os.system`/`popen`/`exec*`/`spawn*`/`fork`/`kill`, `multiprocessing`, `signal`, `atexit` |
  | filesystem | `os.remove`/`unlink`/`rename`/`chmod`/…, `shutil`, `tempfile`, `open(..., 'w'/'a'/'x'/'+')`, `.write_text`/`.write_bytes`/`.unlink`/`.mkdir`/… |
  | network | `socket`, `http.server`, `socketserver`, `smtplib`, `ftplib`, `telnetlib`, `webbrowser` |
  | deserialisation | `pickle`, `marshal`, `shelve`, `dill` |
  | native code | `ctypes`, `cffi` |
  | decoding | `base64`, `binascii`, `codecs.decode` |
  | runtime patching | `sys.modules`, `sys.path`, `builtins`, `globals`, `setattr`, `delattr`, `sys.settrace`/`setprofile` |
  | data the code carries | env var names read, URL hosts in literals, encoded blobs (≥200 unbroken base64/hex chars), every import inside a body |

- **A finding** is a feature the candidate has and the accepted port lacks. An unchanged surface
  yields zero, so a quiet sync stays quiet.
- **To pass**, the finding needs an entry in `.align/safety-ack.json` with a non-empty `reason`.
  `--sync --ack` appends the entries with blank reasons, and blank never passes. The commit diff of
  that file is the audit trail.
- **A one-off full read**: `scripts/safety_diff.py --inventory` lists every risky feature in the
  accepted port (37 today: 31 imports, 3 calls, 2 refs, 1 host). The sockets are only in DNS
  pinning, and the only literal host is `api.github.com` (the README API).
- **Plain `align.py`** counts unacked findings as owed, so `ALIGNED.` is impossible while one is pending.
- **The proof** is `tests/test_safety_gate.py`. It plants a `subprocess` exfiltration of the SerpApi key
  in a throwaway "upstream" and requires the gate to stop it until a reason is written. It also
  requires that a clone with any other `origin`, or a bare `tools.py` from `UNSLOTH_UPSTREAM`, is never
  taken as upstream.

What it does **not** cover, by decision: obfuscation that builds names at runtime
(`getattr(mod, "sys"+"tem")`), and changes inside **dependencies** (ddgs, primp, mcp). Dependencies
are gated by `requirements.lock` hashes plus `tests/test_supply_chain.py`, and a version bump there
is a reviewed diff, not an automatic one.

## The watched surface, if you insist on grepping

```
studio/backend/core/inference/tools.py
  _SEARCH_ENGINE_TIERS          the allowlist. THE thing. tier2 only after tier1 is empty.
  _resolve_engine_tiers         drops engines the installed ddgs lacks, else auto→Yandex
  _web_search                   tier loop, one deadline, policy filter, snippet nudge
  _search_failure_message       model-facing copy, verbatim
  _fetch_page_text/_fetch_url_raw   SSRF, pinning, budget, redirects
  _validate_and_resolve_host    non-public-IP refusal
  WEB_SEARCH_TOOL               the schema the model sees
studio/backend/core/inference/web_access_policy.py   whole file, vendored
studio/backend/core/inference/_html_to_md.py         whole file, vendored
studio/backend/tests/test_web_search_tiers.py        the regression we mirror
studio/backend/requirements/studio.txt               the ddgs pin
```

## Load-bearing invariants — never "improve" these

1. **Never `backend="auto"`.** ddgs auto fans out to **Yandex**; the tier strings exist to stop that.
2. **Drop unknown engine names, never name them.** ddgs 9.8.0 raises `KeyError` on an unknown name and
   then *silently re-runs as `auto`*. This is the entire reason `_resolve_engine_tiers` exists.
3. **One timeout budget across both tiers.** A fresh `DDGS(timeout=…)` per tier doubles it.
4. **Pin the validated IP, keep SNI/cert on the hostname.** Splitting these is the DNS-rebinding fix.
5. **`url` mode is egress to a host the model named.** Upstream prompts (`_web_search_fetches_url`);
   here that is the client's per-call confirmation, expressed as `readOnlyHint/openWorldHint`. Never
   register this server with `--trust`.
6. **ddgs pins `9.14.4` ≥3.10 / `9.8.0` <3.10.** A bump changes the live engine registry, so check
   `approved_engines()` after any bump.

## Upstream behaviour that is NOT a bug to fix here

- **A tier's exception outranks an empty sweep.** `if not results and last_error: raise last_error`
  turns "nothing matched" into `Search failed: RequestError(...)`. Keep it — an engine that threw is a
  different fact from one that found nothing, and `_search_failure_message` exists to say which.
- **A policy-blocked sweep is not an exception.** `_usable_search_results` filters each tier's
  candidates through `check_url_access` before the loop decides, and the re-raise is gated on
  `not rejected_results`. Hits that existed but the website policy refused report
  `EMPTY_SEARCH_RESULTS[1]`, never `Search failed: <exception>`. Never widen it back to
  `if not results and last_error:`.
- `EMPTY_SEARCH_RESULTS` is two distinct strings for two distinct exits. Both must survive.
- Tier 1 may *raise* rather than return `[]` on an empty sweep. That is how ddgs signals empty, and the
  tier fall-through depends on it.

**Tier 3 spends money, so its gate is narrower than "empty".** SerpApi fires only on a sweep that
actually ran and came up empty. Keep it shut when the sweep was policy-blocked (`rejected_results`) and
when no approved engine resolved (`client is None` — a broken install, not an empty search: SerpApi
would re-find the same URLs and the same policy would drop them again). Prove either gate with the key
**armed** and the recorded host list asserted empty; a keyless test proves nothing about the gate.

## Deliberately out of scope

One test decides every case: does porting this give the MCP server a **new capability**, or does it
only read the same result better? New capability → the maintainer's call, refuse it here and record
why. Reads better → port it.

**Never port:**

| upstream | why it stays out |
|---|---|
| `_image_search`, `_web_search_images_suffix`, `web_search_tool_with_images`, `_clean_image_queries`, `search_images.py` | opaque-id thumbnail registry; no proxy to register ids against |
| `_extract_pdf_text`, `_MAX_PDF_FETCH_BYTES`, `_has_pdf_magic` | PDFs are refused by magic, not half-read |
| `_meta_refresh_target` | a page's own refresh hop is not followed; fails closed |
| `_sniff_meta_charset`, `_WHATWG_CHARSET_LABELS`, `_whatwg_codec` | declared/BOM/utf-8 only |
| `_dense_char_limit` | density re-scan bound to Studio's token accounting |
| `_page_char_budget`, `_result_char_budget`, `_request_context_tokens`, `_UNMEASURED_ROOM_MARGIN` | probes the loaded model's window via a Studio ContextVar; `UNSLOTH_PAGE_MAX_CHARS` is the override here |
| `cap_tool_text`, `MAX_TOOL_TEXT_CHARS`, `_TOOL_TEXT_READERS`, `_tool_text_notice_head`, `_tool_text_search_hint` | 256K spill to disk; the MCP client owns its truncation |
| `raw_bytes_max` | binary mode; a JSON text channel has no content part for `bytes` |
| `post_data` | read-only tool; POST contradicts `readOnlyHint` and the per-call `url` confirmation |
| `host_headers` | nothing to shed — the schema never carries caller secrets |
| `meta_out`, `_is_bot_check` | no resident UI; the error string is the whole channel |
| `_has_single_byte_text_evidence` | cp1252 rescue; `_looks_binary` failing closed is the contract |
| `_wikipedia_search` | second route to `en.wikipedia.org/w/api.php` outside ddgs, via `raw_bytes_max` |
| `DEEP_RESEARCH_TOOL`, `RENDER_HTML_TOOL`, `SEARCH_CONVERSATION_TOOL`, `SEARCH_KNOWLEDGE_BASE_TOOL` | separate tools; this MCP mirrors `web_search` alone, each needs its own schema + egress review |

`_DISABLE_DNS_PINNING_ENV` is upstream's name for the literal kept inline at `fetch.py:164`; same
variable, same default, so the diff stays honest without a rename. It only matters when a proxy
applies, and `check_egress_mcp.py --wayback` sets it test-time only to keep the hostname in CONNECT.

**Port these — do not stop at a REVIEW:**

| upstream | why |
|---|---|
| `_HTMLBodyLocator`, `_MAX_HTML_FETCH_BYTES`, `body_window`/`charset` args | a flat `_MAX_FETCH_BYTES` cap returns `<head>`, not content. Wider read, same text out, no new egress or schema field |
| `_install_yahoo_layout_parser`, `_install_ddgs_http1_retry`, `_wrap_ddgs_client`, `_ddgs_http1_replay`, `_is_connection_reset`, `_DDGS_RESET_MARKERS`, `_DDGS_HTTP1_RETRY_LOCK`, `_class_token`, `_YAHOO_SECTION_*` | ddgs-side gaps, not tier gaps: Yahoo is tier 2 and `section.algo` parses empty; the replay answers resets and `tls handshake eof`. Idempotent, renames no engine |
| `_usable_search_results` | the tier gate above |
| `_ContentDecoder`, `_DECODED_CODINGS`, `Accept-Encoding: gzip, deflate` | servers send gzip unasked, even against urllib's default `identity`; undecoded the body reaches the model as binary garbage. Same bytes, same host, no new schema field. Brotli/zstd stay refused (#13532) |
| upstream's `or href` title fallback | absorb it; keep `clip.py` — with no spill, one uncapped hit fills the channel |

If upstream grows a new web/search symbol, `align.py` prints it under `NEW?`. Port it, or add it to
`OUT_OF_SCOPE`/`ANNOTATED` with a reason. Silence is a decision, not an accident.

**Never trust the `0 new`.** `NEW?` is keyword-matched (`web_search`, `engine_tier`, `fetch_page`,
`fetch_url`, `search_engine` in `report()`), so the overwhelming majority of upstream's symbols
are invisible to it. When the churn looks suspicious, list what is unmapped:

```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "scripts")
import build_port, align
p = align.upstream_file(align.find_upstream(None), build_port.UPSTREAM_REL, "origin/main")
known = set(build_port.SYMBOLS) | set(align.ADAPTED) | set(align.OUT_OF_SCOPE) | set(align.ANNOTATED)
un = sorted(set(align.symbols(p)) - known)
print(len(un), "unmapped of", len(align.symbols(p)))
print([n for n in un if any(k in n.lower() for k in ("fetch", "search", "html", "http", "text"))])
PY
```

## Fork-owned, outside the watched surface

New capabilities the maintainer approved. They **call** upstream symbols and never edit or shadow
them, so `align.py` cannot report them and no anchor bump is ever owed for them. If an align run
regenerates something they call, `pytest` is what catches the break, not `align.py`.

| file | what | egress | gate |
|---|---|---|---|
| `serpapi.py` | tier 3, paid, key-gated | `serpapi.com` | `check_egress_mcp.py` |
| `wayback.py` + `wayback` tool in `server.py` | archived copies: Wayback Machine, then Arquivo.pt | `archive.org`, `arquivo.pt` | `check_egress_mcp.py --wayback` |
| `server.py` url-mode hint | text only: suggests `wayback` after an HTTP error, a dead host or a timeout | none | `tests/test_wayback.py` |

Rules for anything added here:

- Fetch **only** through `fetch_url_raw` / `fetch_page_text`. A second HTTP stack would bypass every
  SSRF and pinning gate this port exists to keep.
- Judge the caller's website policy against the URL the model named. Pin each fixed host with its
  own `allowedDomains` policy so a redirect can never leave it.
- Never edit `search.py` / `fetch.py` to wire a feature in. Wrap it in `server.py`, as the hint does.
  Those are ADAPTED, and every local line there is a line to re-justify at the next `REVIEW`.
- Add the hosts to the egress gate as a **separate** set. `approved_hosts()` stays tiers + serpapi
  only (`test_gate_covers_exactly_the_tiers_plus_serpapi`), because search reaching an archive is a bug.

## Adding a symbol

1. Add the name to `SYMBOLS` in `scripts/build_port.py` (verbatim) **or** to `ADAPTED` in
   `scripts/align.py` (needs local judgement).
2. `scripts/align.py --sync`. A newly ported symbol's risky features are new versus the committed
   port, so the safety gate shows them for review.

**A new symbol drags its dependencies in.** `build_port.py` extracts the named symbol only, so a symbol
that calls an unlisted helper — usually one reached through a new parameter — emits a file that imports
clean and raises `NameError` at the call. `align.py` compares ASTs symbol by symbol and cannot see it.
`build_port.py`, `--sync` and pytest all run `build_port.closure_gaps()` now. The check by hand,
never by importing:

```bash
.venv/bin/python - <<'PY'
import symtable, builtins
t = symtable.symtable(open("unsloth_search/upstream_verbatim.py").read(), "m", "exec")
d = {s.get_name() for s in t.get_symbols() if s.is_assigned() or s.is_imported()}
need = set()
def w(x):
    for s in x.get_symbols():
        if s.is_referenced() and s.is_global() and s.get_name() not in d and not hasattr(builtins, s.get_name()):
            need.add(s.get_name())
    for c in x.get_children(): w(c)
w(t); print(sorted(need) or "clean")
PY
```

`HEADER` binds `logger = logging.getLogger(__name__)` — the only non-upstream binding in the generated
file. Keep it: upstream's `get_logger(__name__)` lives in studio's logging module, outside the ported
surface, and `_install_ddgs_http1_retry` calls `logger.debug`.

`HEADER`'s import list is not free either: a ported symbol can drag a stdlib module it names in an
`except` clause, and the missing import surfaces as `NameError` *inside* the handler, masking the real
error. `_read_capped_body` catches `zlib.error` (#13532), so `HEADER` imports `zlib`. The `symtable`
closure check above is what catches this; importing the file does not.

## Install: a stale `.align/upstream` in DEST outranks the anchor

`build_port.py`'s `locate()` ranks `.align/upstream` **above** `.align/upstream-stub`
(any git candidate must also prove its `origin` is `unslothai/unsloth`), and rsync's
`--exclude` also shields a matching DEST path from `--delete`. A clone an earlier install left in DEST
therefore survives every later install and wins, regenerating the verbatim surface from a commit that
predates the anchor. Symptom: `build_port.py` warns `upstream has no: …`, the symbol count drops, and
the installed server dies at `import` on a missing symbol.

`install.sh` must:

1. `rm -rf "$DEST/.align/upstream" "$DEST/.align/refcache"` after the sync, in **both** branches —
   neither is the anchor; the stub is, and the stub is what should be left standing.
2. Compare the count `build_port` reports against `manifest.generated_symbols`. Fewer means the source
   it found predates the anchor.
3. Unregister and `exit 1` on any of these. A dead tool in `/mcp` is worse than no tool.
4. `exit 1` on **every** failure: no Python, sync, venv, a lock hash mismatch, or a missing lock.
   It never installs unpinned. There is no `pip install -e .` either: its build isolation would
   fetch setuptools from PyPI with no hash.

Prove the installed tree is the anchored tree, from `$DEST`:

```bash
.venv/bin/python scripts/align.py --offline --upstream .align/upstream-stub   # 90 tracked · 90 synced
.venv/bin/python -c "import asyncio,server;print([t.name for t in asyncio.run(server.app.list_tools())])"
```

In `$DEST` the stub is not a git repo, so `origin` resolves empty and the printed line is the
stale-anchor nudge, never `ALIGNED.`, however correct the content is. Read the counts, not the line.
`--bump` and `--sync` both refuse there: a snapshot has no commit to anchor to. The anchor is owned
by the source repo, where `--sync` prints `ALIGNED.` with checkout, ref and manifest agreeing.

## When this becomes its own repo

It currently lives at `mcp-websearch/` **inside** another checkout, excluded via
`.git/info/exclude` so `git status` there stays clean. To move it out:

```bash
mv mcp-websearch ~/projects/
cd ~/projects/mcp-websearch
git init -b main && git add -A && git commit -m "Web search MCP ported from Unsloth Studio"
```

`align.py` then finds no sibling clone and shallow-clones upstream into `.align/upstream/` on first run.
Override with `--upstream <path>` or `UNSLOTH_UPSTREAM=<path>`. Both must be a clone whose `origin`
is `github.com/unslothai/unsloth`; only an explicit `--upstream` may name a non-git snapshot.
