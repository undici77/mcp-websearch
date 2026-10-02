# ALIGNMENT — keeping this MCP on top of unsloth/upstream

**The command:**

```bash
scripts/align.py            # fetch origin/main, report, exit non-zero if work is owed
scripts/align.py --diff     # print the upstream body for anything flagged
```

Say `align current MCP with upstream/main` → run the above. It reads **named symbols only**, never the
22k-line `tools.py` whole. Typical run: ~1s.

---

## Why drift is structurally hard here, not just unlikely

The verbatim surface is **generated**, not copied by hand.

```
studio/backend/core/inference/tools.py
        │  scripts/build_port.py  (AST extraction, 58 symbols, upstream line order)
        ▼
unsloth_search/upstream_verbatim.py      ← GENERATED, DO NOT EDIT
        │  imports, unchanged
        ▼
unsloth_search/fetch.py · search.py      ← the hand-written 5 adapted functions
```

A transcription error is no longer possible in 58 of the 63 ported symbols: they are emitted by a
script from upstream's AST. Rebuilding is deterministic — the same upstream commit always produces the
same sha256.

## The four surfaces

| surface | count | test | when it shouts |
|---|---|---|---|
| `generated` | 58 | re-extract from upstream, compare each symbol's AST | upstream changed → `scripts/build_port.py` fixes it |
| `vendored` | 2 | sha256 both ways (`web_access_policy.py`, `_html_to_md.py`) | upstream changed → `cp` |
| `adapted` | 5 | upstream fingerprint vs the one recorded at sync | upstream touched it → read and re-justify |
| `out-of-scope` / `annotated` | 9 | presence + churn | upstream grew the feature, or deleted it |

`adapted` never claims equality — local code differs on purpose. It only flags upstream churn, which is
what keeps the report from crying wolf.

`.align/manifest.json` is **committed**: it is the contract (anchor commit + adapted fingerprints +
vendored hashes).

## The ritual

```bash
scripts/align.py --diff          # 1. what moved?
scripts/build_port.py            # 2. regenerate the verbatim surface
# 3. apply the vendored cp and read the adapted REVIEWs by hand
.venv/bin/python -m pytest -q tests          # 4. green
.venv/bin/python scripts/check_egress_mcp.py # 5. live egress gate — one real query, hosts vs tiers
scripts/align.py --bump          # 6. re-anchor  (refused while anything is owed)
```

`--bump` refuses while anything is DRIFTED or BROKEN, so the anchor can never silently move past
unapplied work.

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

- **A throttled tier-2 engine masks an honest empty sweep.** `if not results and last_error: raise
  last_error` turns "nothing matched" into `Search failed: RequestError(...)`. Observed live here on
  2026-10-02 (`grokipedia … tls handshake eof`). Upstream behaves identically. Copy it — "fixing" it
  makes every future diff dirty forever.
- `EMPTY_SEARCH_RESULTS` is two distinct strings for two distinct exits. Both must survive.
- Tier 1 may *raise* rather than return `[]` on an empty sweep. That is how ddgs signals empty, and the
  tier fall-through depends on it.

## Deliberately out of scope

Studio-UI-coupled, no MCP equivalent: image search and its opaque-id thumbnail registry
(`_image_search`, `_web_search_images_suffix`, `web_search_tool_with_images`, `_clean_image_queries`,
`search_images.py`), PDF text extraction (`_extract_pdf_text` — PDFs are refused by magic instead),
`<meta refresh>` following (`_meta_refresh_target` — fails closed), WHATWG `<meta charset>` prescan,
`_dense_char_limit`, and context-window page sizing (`_page_char_budget` → `UNSLOTH_PAGE_MAX_CHARS`;
an MCP server has no resident model to probe).

If upstream grows a new web/search symbol, `align.py` prints it under `NEW?`. Port it, or add it to
`OUT_OF_SCOPE`/`ANNOTATED` with a reason. Silence is a decision, not an accident.

## Adding a symbol

1. Add the name to `SYMBOLS` in `scripts/build_port.py` (verbatim) **or** to `ADAPTED` in
   `scripts/align.py` (needs local judgement).
2. `scripts/build_port.py` → `pytest -q tests` → `scripts/align.py --bump`.

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
