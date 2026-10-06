# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP server that lets an LLM search **list.am** (Armenia's largest classifieds site) and read listing details. list.am has no public API, so the server works entirely by fetching and parsing the site's public HTML.

## Commands

```bash
pip install -e ".[dev]"   # install with dev deps (pytest, ruff)
pytest                    # run all tests
pytest tests/test_parsing.py::test_parse_price   # run a single test
ruff check .              # lint (also runs in CI)
```

Debug commands hit the *live* site and print JSON — useful for checking whether `parsing.py` still matches the real page layout:

```bash
listam-mcp search "iphone 15"
listam-mcp search --category 56
listam-mcp get 24201365
listam-mcp categories
```

Interactive tool exploration:

```bash
npx @modelcontextprotocol/inspector listam-mcp
```

CI (`.github/workflows/ci.yml`) runs `ruff check .` and `pytest -q` on Python 3.10/3.12/3.13 for every push/PR to `main`.

## Architecture

Everything lives in `src/listam_mcp/`, one module per concern, in dependency order:

```
config.py   → settings only, no dependencies
models.py   → plain dataclasses, no dependencies
parsing.py  → HTML (str) -> models  (depends on config, models)
client.py   → HTTP fetching + URL building (depends on config, models, parsing)
storage.py  → SQLite for saved searches (no dependency on the others)
server.py   → MCP tools + CLI, wires client + storage together
```

**`parsing.py` is the only module that should need updating when list.am changes its layout.** Item-page and category parsing favor stable signals — `/item/<id>` and `/category/<id>` hrefs, `<h1>`, `og:` meta tags, and visible text — over CSS class names. Search-card parsing (`_parse_card`) is the one exception: list.am reuses short legacy classes ("l", "at", "p") for different fields depending on which card template rendered (keyword search vs. category browse), so a flat-text/length heuristic silently mismatches title vs. category breadcrumb vs. price. `_parse_card` instead targets each template's BEM-ish class *suffixes* (`__amount`, `__location`, `.pt`, plain `div.l`, etc. — see `_by_class_suffix`), which are consistent across templates even though the short classes aren't. Because heuristics can still miss a field, `ListingSummary` always carries the card's raw joined text (`text`) and `ListingDetail` carries the unclassified lines between the price and the description as `attributes_text`. When list.am changes layout again, refresh `tests/fixtures/search.html`/`filters.html` from a real fetched page (see `README.md`'s debug commands) rather than hand-tweaking the old fixture — the fixture is meant to mirror real markup, not an abstraction of it.

**`client.py`** enforces the security/politeness boundary: `ensure_listam_url()` rejects any URL whose host isn't `list.am`/`*.list.am`, and is called on every fetch — this is what stops the tools from being used to reach arbitrary hosts. A single `asyncio.Lock` serializes requests end-to-end, including retries: list.am sits behind Cloudflare, which can 403 a request as a short-lived burst-triggered block rather than a real ban, so `_fetch()` retries (growing backoff, `max_retries`) *while still holding the lock*, so every other queued call waits out the same cool-down instead of piling on. A simple in-memory `{url: (timestamp, html)}` dict caches responses for `cache_ttl` seconds. `search_pages()` follows `next_url` from `parse_search_page()` up to `max_pages`.

**`server.py`** exposes the MCP tools (`search_listings`, `search_by_url`, `get_filters`, `get_listing`, `get_listings`, `list_categories`, `save_search`, `check_saved_search`, `list_saved_searches`, `delete_saved_search`) and the `listam-mcp` CLI entry point (`main()`), which also handles the `search`/`get`/`categories` debug subcommands and the `stdio`/`streamable-http` transport flags. `client()`/`storage()` are lazily-constructed module-level singletons. Price filtering (`_filter`) and multi-page dedup (`_run_search`) happen here, in Python, after fetching — not via list.am URL params — because prices can be in AMD/USD/EUR/RUB and need cross-currency conversion via `settings.rates`. Search tools return `ListingSummary.to_dict(compact=True)` rows by default (drops `text`/`badges`/`image`/`is_agency`) since a full-size page of results can blow past a tool-output limit; pass `full=True` for everything, or `get_listing`/`get_listings` for one ad's complete detail. `get_filters` scrapes a category page's filter sidebar (`parse_filters`) for its `_aNN`-style param names and option values, since list.am has far more filters (rooms, mileage, transmission...) than this server models as first-class arguments — `extra_params` is the escape hatch that consumes them, though it can only carry one value per key, so a `multi: true` filter's multiple values still require `search_by_url` with a URL copied from the browser.

**`storage.py`** is a thin SQLite wrapper (`saved_search`, `seen_listing` tables) used only for the saved-search-diffing feature: `check_saved_search` re-runs the saved URL and reports listing IDs not previously recorded via `mark_seen`.

All configuration is environment-variable driven through the frozen `Settings` dataclass in `config.py` (`LISTAM_LANG`, `LISTAM_MIN_INTERVAL`, `LISTAM_CACHE_TTL`, `LISTAM_TIMEOUT`, `LISTAM_RATES`, `LISTAM_DATA_DIR`, `LISTAM_USER_AGENT`) and a module-level `settings` instance built at import time.
