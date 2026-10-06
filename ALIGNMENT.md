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

**`main` moves mid-run.** Observed 2026-10-05: the ref advanced twice inside one session
(`7b2f7d5` → `f57a7de`), and `--bump` anchored to the newer ref than the content just copied. Step 9
is what catches that; if it flags anything, loop back to step 2.

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
`search_images.py`), PDF text extraction (`_extract_pdf_text`, `_MAX_PDF_FETCH_BYTES`,
`_has_pdf_magic` — PDFs are refused by magic instead of half-read), `<meta refresh>` following
(`_meta_refresh_target` — fails closed), WHATWG `<meta charset>` prescan (`_sniff_meta_charset`,
`_WHATWG_CHARSET_LABELS`, `_whatwg_codec`), `_dense_char_limit`, and context-window page sizing
(`_page_char_budget` → `UNSLOTH_PAGE_MAX_CHARS`; an MCP server has no resident model to probe).

**Recorded at the `c17ca64` anchor, closing the `_fetch_url_raw` REVIEW.** Upstream grew four call
capabilities in that signature. Each expands what the tool can *do*, so each is the maintainer's call,
not a mechanical port:

| upstream | what it adds | why it stays out here |
|---|---|---|
| `raw_bytes_max` | binary mode — body returned as `bytes` | an MCP client gets text in a JSON string; raw bytes need a content part the schema has no field for |
| `post_data` | urlencoded POST, kept on 307/308 | `web_search` is read-only; POST contradicts `readOnlyHint` and the per-call `url` confirmation |
| `host_headers` | per-hop headers, shed when a redirect crosses sites | nothing to shed — the schema exposes `query`/`url` only, no caller-supplied secrets |
| `meta_out` | final `url`/`charset`/`filename`, plus `bot_check` on HTTP errors | no resident UI to render them; the error string is the whole channel |

Also unported from the same churn, both deliberate: `_is_bot_check` (only feeds `meta_out`) and
`_has_single_byte_text_evidence` (upstream's cp1252 rescue for mislabeled text — here a body that
`_looks_binary` cannot clear returns the binary placeholder, failing closed). `_DISABLE_DNS_PINNING_ENV`
is upstream's name for the literal kept inline at `fetch.py:159`; same variable, same default, so the
diff stays honest without a rename.

If upstream grows a new web/search symbol, `align.py` prints it under `NEW?`. Port it, or add it to
`OUT_OF_SCOPE`/`ANNOTATED` with a reason. Silence is a decision, not an accident.

`NEW?` is **keyword-matched** (`web_search`, `engine_tier`, `fetch_page`, `fetch_url`, `search_engine`
at `align.py:263`), so `0 new` does not mean nothing new. At `c17ca64` it prints **zero** while 927
upstream symbols sit unmapped — including fetch-adjacent ones that bear on this port:
`_has_pdf_magic`, `_MAX_PDF_FETCH_BYTES`, `_sniff_meta_charset`, `_has_single_byte_text_evidence`,
`_is_bot_check`, `_DISABLE_DNS_PINNING_ENV`, and upstream's 256K tool-result spill
(`cap_tool_text` / `MAX_TOOL_TEXT_CHARS` / `_TOOL_TEXT_READERS` / `_tool_text_notice_head` /
`_tool_text_search_hint`). Diff upstream's symbol list against the anchor when the churn looks
suspicious — don't trust the zero.

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
