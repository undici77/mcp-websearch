# mcp-websearch

`web_search` as an **external MCP tool** for Qwen Code, ported from Unsloth Studio's server-side tool
loop. Same engine tiers, same SSRF and domain-policy gates, same result format. **No API key needed.**
If `~/.qwen/websearch-keys.json` holds a SerpApi key, a sweep that fails on all free tiers falls back
to it; an honest empty sweep never does.

```
Qwen Code ─MCP/stdio→ server.py → ddgs → tier 1: wikipedia brave duckduckgo mojeek startpage
                                    └────→ tier 2: grokipedia google yahoo   (only if tier 1 is empty)
                                    └────→ tier 3: serpapi.com               (only if both tiers fail, key set)
```

Engines outside those tiers — **Yandex, Bing, the mullvad mirrors** — are never contacted. Tier 3 is
this fork's own paid fallback, not upstream, and is inert without a key.

## Web archives (`wayback` tool, fork-owned)

A second tool reads archived copies of pages that are gone, changed, or unreachable. It is **not
upstream** and never runs on its own: when `web_search` url mode fails on an HTTP error, a dead host
or a timeout, the result only *suggests* a `wayback` call, which the client confirms like any other.

```
wayback {url}                         → Wayback Machine latest capture, else Arquivo.pt
wayback {url, timestamp: "2015"}      → capture closest to that date (YYYY / YYYYMMDD / YYYYMMDDhhmmss)
wayback {url, list_snapshots: true}   → readable captures from both archives, newest first
wayback {url, archive: "arquivo"}     → one archive only
```

- **Hosts:** `archive.org` and `arquivo.pt`, nothing else. Every request carries a policy admitting
  only the archive that serves it, so a redirect cannot leave it.
- **Guards:** every byte goes through upstream's unchanged fetcher — SSRF refusal, DNS pinning,
  redirect re-checks, body cap, html→md.
- **Policy:** `allowed_domains` / `blocked_domains` judge the **original** URL, not the archive.
  Literal private/loopback addresses are refused even though the bytes would come from the archive.
- **Known limit:** a Wayback capture that was itself a redirect replays as a hop to another
  capture inside archive.org; that target's original domain is not re-judged by your policy.

## Install

```bash
cd mcp-websearch
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --no-deps -r requirements.lock
.venv/bin/python scripts/align.py --sync   # clone, safety gate, generate, test, egress gates, anchor
```

`-m pip` and `-m pytest`, never `.venv/bin/pip`: a venv is not relocatable, so its script
shebangs break the moment the directory moves.

## Supply chain

`requirements.lock` is the boundary. 45 exact pins, 778 sha256 digests, installed with
`--require-hashes --no-deps` — so a compromised PyPI release cannot be adopted silently, and a new
or changed dependency only enters by a diff you commit.

- `pyproject.toml` pins `mcp==2.2.0`, not a range. An open bound means every install silently
  takes the newest SDK.
- `tests/test_supply_chain.py` fails if any pin is unhashed, if any installed dist is not named by
  the lock, or if a `*exporter*` telemetry package appears anywhere in the set.
- `opentelemetry-api` is unavoidable (`mcp` requires `opentelemetry-api>=1.28.0`). API only, no
  exporter installed, so nothing exports — recorded here so its presence is a decision, not a
  discovery. An exporter appearing is a hard finding.

## Egress gate

`scripts/check_egress_mcp.py` is the MCP-side mirror of the fork's §18. A CONNECT-only proxy on
loopback records which hosts the tiers actually reach — the tunnel is never decrypted, so no CA is
installed and nothing is a man in the middle. **Test-time only:** normal operation has no proxy.

```bash
.venv/bin/python scripts/check_egress_mcp.py
```

Exit `0` = every destination inside the tiers · `1` = a host outside them · `2` = the detector is
blind (self-test canary missed, or primp bypassed the proxy). The allowlist is derived from
`_SEARCH_ENGINE_TIERS` through `ENGINE_HOSTS`, so adding an engine without mapping its host fails
the build rather than widening egress silently.

```bash
.venv/bin/python scripts/check_egress_mcp.py --wayback   # archive hosts only: archive.org, arquivo.pt
```

The archive run sets `UNSLOTH_STUDIO_DISABLE_DNS_PINNING=1` in its own process: a pinned CONNECT
names an IP, and the gate judges hostnames. Search mode must never reach an archive host.

Run it: every release · every `requirements.lock` bump · after realignment · after any change to
`fetch.py` or the tier list. It issues one live query.

Verified on `primp 2.0.1` / `ddgs 9.14.4`: the tiers **do** honour `HTTPS_PROXY` — all eight
engine hosts arrive as CONNECT records and the search still succeeds. The proxy is a real control
point here, not a dead one.

## Add to Qwen Code

```bash
qwen mcp add websearch "$(pwd)/.venv/bin/python" "$(pwd)/server.py" \
  -t stdio -s user --description "Keyless web search, ported from Unsloth Studio"
```

`-s user` → `~/.qwen/settings.json`; `-s project` → `.qwen/settings.json`. Restart the session, then
`/mcp` should list `websearch` with one tool, `web_search`. The model sees it as
`mcp__websearch__web_search` — the `mcp__<server>__` prefix is forced by the client and cannot be
overridden. Hand-edits preferred:
[`qwen-mcp-snippet.json`](qwen-mcp-snippet.json).

**Do not pass `--trust`.** `url` mode fetches a page whose host the *model* chose; the client's
per-call confirmation is the gate that makes that safe, mirroring upstream's `_web_search_fetches_url`
prompt. Trusting the server removes it.

## Use

| call | what happens |
|---|---|
| `{"query": "..."}` | tiered search → `Title / URL / Snippet` blocks |
| `{"url": "..."}` | one page → Markdown (`<article>`/`<main>` scoping, boilerplate stripped) |
| `{"url": "github.com/o/r"}` | rewritten to the GitHub README API — you get the README, not the repo's UI chrome |
| `{"allowed_domains":[...]}` | pushed in as `site:` terms **and** enforced on every result URL |
| `{"blocked_domains":[...]}` | refused at policy time |

## Env

| var | default | |
|---|---|---|
| `UNSLOTH_PAGE_MAX_CHARS` | `16000` | fetched-page cap |
| `UNSLOTH_SEARCH_MAX_RESULTS` | `5` | results per search |
| `UNSLOTH_MCP_TRANSPORT` | `stdio` | `stdio` \| `streamable-http` \| `sse` |
| `UNSLOTH_STUDIO_DISABLE_DNS_PINNING` | unset | `1` keeps the hostname in proxied requests (enterprise TLS interception) |
| `UNSLOTH_UPSTREAM` | auto-detected | path to an unsloth clone, for `align.py` |

## How the port is built

58 of the 63 ported symbols are **machine-generated** from upstream's AST, so they cannot drift by
transcription:

```
tools.py ──build_port.py──▶ upstream_verbatim.py (GENERATED) ──import──▶ fetch.py · search.py
                                                                        └── 5 hand-adapted functions
```

| surface | | |
|---|---|---|
| `generated` | `upstream_verbatim.py` | AST-extracted from `tools.py`. **Never hand-edit.** |
| `vendored` | `web_access_policy.py`, `_html_to_md.py` | byte-exact `cp`, stdlib-only |
| `adapted` | `web_search`, `fetch_url_raw`, `fetch_page_text`, `_truncate_page_text`, `page_char_budget` | the only places local judgement was applied |

**Faithful:** engine tier allowlist + registry resolution, non-public-IP refusal, DNS pinning with
correct SNI, one wall-clock budget across tiers and redirect hops, capped chunked body read, binary
rejection by MIME then magic, HTML→Markdown, GitHub README routing, every model-facing string.

**Not here (by choice):** image search + thumbnail registry, PDF text extraction, `<meta refresh>`,
WHATWG charset prescan, context-window page sizing. Reasoning per item: [ALIGNMENT.md](ALIGNMENT.md).

## Stay aligned

```bash
scripts/align.py --diff
```

Reads only the pinned symbols, never all of `tools.py`. Reports `SYNCED / REVIEW / DRIFTED / BROKEN`
and prints the upstream body to apply. Full contract: [ALIGNMENT.md](ALIGNMENT.md).

## Layout

```
server.py                       MCP stdio entrypoint (mcp SDK v1 FastMCP and v2 MCPServer both work)
unsloth_search/
  upstream_verbatim.py   GENERATED — 58 symbols extracted from tools.py, do not edit
  web_access_policy.py   vendored byte-exact
  _html_to_md.py         vendored byte-exact, stdlib-only
  fetch.py               adapted fetch layer
  search.py              adapted tier loop + approved_engines()
  __init__.py            package edge
.align/manifest.json           the sync contract (committed)
scripts/build_port.py         generates upstream_verbatim.py
scripts/align.py              the drift checker
tests/                        tiers (mirrored upstream) + guards + fidelity
```

AGPL-3.0-only, matching upstream (`COPYING` / `studio/LICENSE.AGPL-3.0` in unslothai/unsloth).
