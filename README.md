# listam-mcp

An [MCP](https://modelcontextprotocol.io) server for **[list.am](https://www.list.am)**, Armenia's largest classifieds site. It lets Claude (or any MCP client) search listings in every category, read full ad details and track saved searches for new ads.

> Unofficial project, not affiliated with list.am. list.am has no public API, so this server reads the public website. Use it for personal, low-volume purposes and respect list.am's [Terms of Service](https://www.list.am/help/14).

## What you can ask

- "Find 2-room apartments for rent in Arabkir under $700 a month, owners only, and compare the best five."
- "Search for a used iPhone 15 under 350,000 AMD and tell me which ads look like the best deal."
- "Save this search as `kentron-rent` and tell me tomorrow what's new."
- "Open ad 24201365 and summarize the pros and cons."

## Tools

| Tool | Description |
|---|---|
| `search_listings` | Search any category by text, category, region and price (AMD/USD/EUR/RUB), optionally excluding agencies. Returns compact rows by default; pass `full=True` for every field. |
| `search_by_url` | Run a search URL copied from the browser, keeping every filter list.am supports. |
| `get_filters` | A category's own filters (condition, rooms, mileage, transmission...) and their values, for `search_listings`' `extra_params`. |
| `get_listing` | Full details of an ad: price, attributes, description, seller, images, posted/renewed dates. |
| `get_listings` | Full details of several ads in one call (fetched one at a time, throttled) — for checking a handful of candidates at once. |
| `list_categories` | Discover category IDs (top level, or the subcategories of a category). |
| `save_search` / `check_saved_search` | Save a search and later get only the listings that are new since the last check. |
| `list_saved_searches` / `delete_saved_search` | Manage saved searches (stored in SQLite). |

**Tip:** `get_filters(category_id)` returns each category's own filters (rooms, floor, mileage, renovation...) and the `param`/value to pass in `search_listings`' `extra_params`. A `multi: true` filter accepts more than one value on list.am, but `extra_params` can only set one value per key — for more than one, apply the filter on list.am in a browser and use `search_by_url` or `save_search` with the resulting URL.

## Installation

Requires Python 3.10+. The easiest way is [uv](https://docs.astral.sh/uv/), which runs the server straight from GitHub:

```bash
uvx --from git+https://github.com/<your-username>/listam-mcp listam-mcp --help
```

Or install it locally:

```bash
git clone https://github.com/<your-username>/listam-mcp
cd listam-mcp
pip install -e .
```

### Claude Desktop

Add this to `claude_desktop_config.json` (Settings → Developer → Edit Config) and restart Claude:

```json
{
  "mcpServers": {
    "listam": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/<your-username>/listam-mcp", "listam-mcp"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add listam -- uvx --from git+https://github.com/<your-username>/listam-mcp listam-mcp
```

### Remote (HTTP) mode

```bash
listam-mcp --transport streamable-http --host 127.0.0.1 --port 8000
# MCP endpoint: http://127.0.0.1:8000/mcp
```

## Configuration

All settings are optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `LISTAM_LANG` | `en` | Site language: `en`, `hy` or `ru`. Some fields (dates, description) are parsed best in `en`. |
| `LISTAM_MIN_INTERVAL` | `2.5` | Minimum seconds between requests to list.am. |
| `LISTAM_CACHE_TTL` | `300` | Seconds to cache fetched pages. |
| `LISTAM_TIMEOUT` | `20` | HTTP timeout in seconds. |
| `LISTAM_MAX_RETRIES` | `2` | Retries (with growing backoff) after a 403/429 before giving up. |
| `LISTAM_RATES` | `USD=363.5,EUR=425,RUB=4.3` | Approximate AMD per currency unit, used for cross-currency price filters. |
| `LISTAM_DATA_DIR` | `~/.listam-mcp` | Where the saved-search database lives. |
| `LISTAM_USER_AGENT` | browser-like UA | User-Agent header. |

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .

# Debug commands hit the live site and print JSON — handy when list.am changes its HTML:
listam-mcp search "iphone 15"
listam-mcp search --category 56
listam-mcp get 24201365
listam-mcp categories
```

You can also explore the tools interactively with the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector listam-mcp
```

### How it works

```
src/listam_mcp/
├── server.py    # MCP tools + CLI entry point
├── client.py    # async HTTP client: rate limiting, caching, list.am-only URL guard
├── parsing.py   # HTML → data models (the only place that knows the page layout)
├── storage.py   # SQLite for saved searches and seen listing IDs
├── models.py    # dataclasses returned by tools
└── config.py    # environment-based settings
```

The parser relies on stable signals (`/item/<id>` and `/category/<id>` links, `<h1>`, `og:` meta tags and visible text) rather than CSS class names. Each listing also carries its raw card text and attribute lines, so the model can still read the data if a heuristic misses a field. When list.am changes its layout, update `parsing.py`, refresh the fixtures in `tests/fixtures/` and run the tests.

### Responsible use

- Requests are serialized and throttled, and results are cached.
- Only `list.am` URLs can be fetched, so the tools can't be used to reach other hosts.
- Don't use it for bulk data collection, reselling data or contacting sellers at scale.

## License

[MIT](LICENSE)
