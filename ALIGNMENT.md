# ALIGNMENT — keeping this MCP on top of unsloth/upstream

**The command:**

```bash
scripts/align.py            # fetch origin/main, report, exit non-zero if work is owed
scripts/align.py --diff     # print the upstream body for anything flagged
```

It reads **named symbols only**, never the 22k-line `tools.py` whole. Typical run: ~1s.

## When align is required

Not optional, not "when convenient". Run it at each of these:

| trigger | do |
|---|---|
| about to edit anything in `unsloth_search/` | `scripts/align.py` **first** — a stale anchor makes the edit a divergence you will have to unwind |
| touched a `vendored` file | `align.py --diff`. A vendored file is byte-exact or it is wrong; there is no third state |
| the `ddgs` pin changed | `align.py` **and** `approved_engines()` — the live engine registry is an output of the pin |
| `anchor` ≠ `origin/main` in any output | full ritual. A stale anchor **is** owed work, not a status line |
| any `REVIEW` row | read that churn with `--diff`, re-justify or port, then `--bump` |
| shipping, tagging, handing off, or opening a PR on this port | full ritual, steps 1–9, ending in `ALIGNED.` |
| you last aligned more than a week ago | `align.py`. `main` moved; assume it moved in the watched surface |
| someone says `align current MCP with upstream/main` | full ritual, this turn |

The trigger is the **anchor**, not memory. `align.py` costs ~1s; not running it costs a port that is
quietly behind upstream on the one surface carrying the SSRF gates and the tier allowlist.

## `align` means DO it, not report it

`align.py` is a **gate**, not a deliverable. Read the **printed line**, not the exit code — exit `0`
covers two different states, because one `print` emits both (`align.py:291`), and a lone `REVIEW`
exits `0` since `owed` counts only DRIFT + BROKEN (`align.py:279`).

| printed | exit | state | you must |
|---|---|---|---|
| `ALIGNED.` | `0` | anchor == `origin/main`, nothing owed | report the anchor + counts. **Stop.** |
| `Aligned, but anchored to an older commit.` | `0` | content matches, **anchor is stale** | run steps 2–7, `--bump`, re-run until the line says `ALIGNED.` |
| `REVIEW adapted …` | `0` | upstream churned an adapted symbol | `--diff`, read it, re-justify in the doc **or** port it, then `--bump` |
| `DRIFTED` / `BROKEN` / `NEW?` | `1` | work owed | **finish the ritual below, in this same turn.** Then show `ALIGNED.` |

A green exit is not an alignment, and "here is what you should do next" is not an alignment. Port it,
`cp` it, bump it, prove it green, re-run `align.py` until it prints `ALIGNED.` — then report. Asking
permission to apply a `cp` that the doc already prescribes is the failure mode this section exists to
kill.

Two exceptions, because they are genuinely yours to make and not mine: a **new** upstream capability
(POST, binary mode, per-hop headers) that expands scope, and anything that would weaken an invariant
below. Everything else is mechanical — do it.

---

## Why drift is structurally hard here, not just unlikely

The verbatim surface is **generated**, not copied by hand.

```
studio/backend/core/inference/tools.py
        │  scripts/build_port.py  (AST extraction, 71 symbols, upstream line order)
        ▼
unsloth_search/upstream_verbatim.py      ← GENERATED, DO NOT EDIT
        │  imports, unchanged
        ▼
unsloth_search/fetch.py · search.py      ← the hand-written adapted functions
```

A transcription error is no longer possible in 71 of the 77 ported symbols: they are emitted by a
script from upstream's AST, and the same upstream commit always produces the same sha256.

## The four surfaces

| surface | count | test | when it shouts |
|---|---|---|---|
| `generated` | 71 | re-extract from upstream, compare each symbol's AST | upstream changed → `scripts/build_port.py` fixes it |
| `vendored` | 2 | sha256 both ways (`web_access_policy.py`, `_html_to_md.py`) | upstream changed → `cp` |
| `adapted` | 6 | upstream fingerprint vs the one recorded at sync | upstream touched it → read and re-justify |
| `out-of-scope` / `annotated` | 9 | presence + churn | upstream grew the feature, or deleted it |

`adapted` never claims equality — local code differs on purpose. It only flags upstream churn, which is
what keeps the report from crying wolf.

`.align/manifest.json` is **committed**: it is the contract (anchor commit + adapted fingerprints +
vendored hashes).

## The ritual

```bash
scripts/align.py --diff          # 1. what moved?
git -C .align/upstream checkout -q origin/main   # 2. move the checkout — build_port reads the TREE, not the ref
scripts/build_port.py            # 3. regenerate the verbatim surface
# 4. cp every flagged vendored file off the checkout; read the adapted REVIEWs by hand
.venv/bin/python -m pytest -q tests          # 5. green
.venv/bin/python scripts/check_egress_mcp.py # 6. live egress gate — one real query, hosts vs tiers
scripts/align.py --bump          # 7. re-anchor  (refused while anything is owed)
# 8. refresh .align/upstream-stub off the checkout, then re-run pytest
scripts/align.py                 # 9. must print ALIGNED. — that, not step 7, is the deliverable
```

`--bump` refuses while anything is DRIFTED or BROKEN, so the anchor can never silently move past
unapplied work.

**Step 2 is not optional.** `git fetch` moves `origin/main` and checks out nothing, and both
`build_port.py`'s `locate()` and `tests/test_fidelity.py` hash a **working-tree path**. Skip the
checkout and step 3 regenerates anchor-content that step 7 then stamps with a newer anchor — a
false green that no later run can catch.

**Step 4: `cp` is the fix for any vendored flag.** A vendored file is byte-exact or it is wrong, so
pull the ref copy regardless of whether the note blames local or upstream — the note's attribution is
inverted (`align.py:226` treats *equality* with the recorded hash as "upstream changed"). Trust the
hashes, not the sentence.

**Step 8 or the gate fights you.** `.align/upstream-stub/` is an untracked offline stand-in for the
anchored ref. Leave it at the old anchor and `test_vendored_files_are_byte_exact` fails *the correct
fix*; delete it (fresh clone) and the test silently skips. Refresh it whenever the anchor moves.

**`main` moves mid-run — `ALIGNED.` alone is not the proof.** `align.py` compares against
`git show origin/main:…`, so every fetch can advance the ref out from under you: step 7 stamps an
anchor newer than the content step 3 copied, and the next `build_port.py` (which reads the TREE)
silently regenerates the stale content. Before calling it done, prove all three agree:

```bash
git -C .align/upstream rev-parse HEAD                       # checkout
git -C .align/upstream rev-parse origin/main               # ref
.venv/bin/python -c "import json;print(json.load(open('.align/manifest.json'))['upstream_commit'])"
```

Same sha three times, or loop back to step 2: move the checkout, rebuild, `cp` the vendored files,
refresh the stub, re-test, re-align.

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

`_DISABLE_DNS_PINNING_ENV` is upstream's name for the literal kept inline at `fetch.py:159`; same
variable, same default, so the diff stays honest without a rename.

**Port these — do not stop at a REVIEW:**

| upstream | why |
|---|---|
| `_HTMLBodyLocator`, `_MAX_HTML_FETCH_BYTES`, `body_window`/`charset` args | a flat `_MAX_FETCH_BYTES` cap returns `<head>`, not content. Wider read, same text out, no new egress or schema field |
| `_install_yahoo_layout_parser`, `_install_ddgs_http1_retry`, `_wrap_ddgs_client`, `_ddgs_http1_replay`, `_is_connection_reset`, `_DDGS_RESET_MARKERS`, `_DDGS_HTTP1_RETRY_LOCK`, `_class_token`, `_YAHOO_SECTION_*` | ddgs-side gaps, not tier gaps: Yahoo is tier 2 and `section.algo` parses empty; the replay answers resets and `tls handshake eof`. Idempotent, renames no engine |
| `_usable_search_results` | the tier gate above |
| upstream's `or href` title fallback | absorb it; keep `clip.py` — with no spill, one uncapped hit fills the channel |

If upstream grows a new web/search symbol, `align.py` prints it under `NEW?`. Port it, or add it to
`OUT_OF_SCOPE`/`ANNOTATED` with a reason. Silence is a decision, not an accident.

**Never trust the `0 new`.** `NEW?` is keyword-matched (`web_search`, `engine_tier`, `fetch_page`,
`fetch_url`, `search_engine` at `align.py:263`), so the overwhelming majority of upstream's symbols
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

## Adding a symbol

1. Add the name to `SYMBOLS` in `scripts/build_port.py` (verbatim) **or** to `ADAPTED` in
   `scripts/align.py` (needs local judgement).
2. `scripts/build_port.py` → `pytest -q tests` → `scripts/align.py --bump`.

**A new symbol drags its dependencies in.** `build_port.py` extracts the named symbol only, so a symbol
that calls an unlisted helper — usually one reached through a new parameter — emits a file that imports
clean and raises `NameError` at the call. `align.py` compares ASTs symbol by symbol and cannot see it.
After every `build_port.py`, check the closure with `symtable`, never by importing:

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

## Install: a stale `.align/upstream` in DEST outranks the anchor

`build_port.py`'s `locate()` ranks `.align/upstream` **above** `.align/upstream-stub`, and rsync's
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

Prove the installed tree is the anchored tree, from `$DEST`:

```bash
.venv/bin/python scripts/align.py --offline --upstream .align/upstream-stub   # 88 tracked · 88 synced
.venv/bin/python -c "import asyncio,server;print([t.name for t in asyncio.run(server.app.list_tools())])"
```

## When this becomes its own repo

It currently lives at `mcp-websearch/` **inside** another checkout, excluded via
`.git/info/exclude` so `git status` there stays clean. To move it out:

```bash
mv mcp-websearch ~/projects/
cd ~/projects/mcp-websearch
git init -b main && git add -A && git commit -m "Web search MCP ported from Unsloth Studio"
```

`align.py` then finds no sibling clone and shallow-clones upstream into `.align/upstream/` on first run.
Override with `--upstream <path>` or `UNSLOTH_UPSTREAM=<path>`.
