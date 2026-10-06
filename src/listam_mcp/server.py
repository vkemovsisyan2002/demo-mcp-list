"""MCP server exposing list.am search, listing details and saved searches."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from .client import ListAmClient, ListAmError, ensure_listam_url
from .config import settings
from .models import ListingSummary
from .storage import Storage

INSTRUCTIONS = """\
Tools for searching list.am, Armenia's largest classifieds site (real estate,
vehicles, electronics, jobs, services and more).

Tips:
- Use list_categories to find a category_id; category 0/None searches everything.
- Prices come in AMD, USD, EUR or RUB; `amount_amd_approx` allows comparing them.
- Use get_filters(category_id) to see that category's own filters (condition, rooms,
  mileage, transmission...) and their values, then pass one via extra_params. A
  multi-select filter (param ending "[]", or noted multi) only takes one value this
  way; for more than one, apply it on list.am in a browser and use search_by_url.
- Search results are compact by default (id/title/price/location/details/url). Pass
  full=True for the rest (images, seller badges, raw card text), or call get_listing /
  get_listings for full per-ad detail (description, seller, attributes, images).
- Call get_listing (or get_listings for a few at once) before recommending a specific ad.
"""

# Location IDs used by list.am's `n` URL parameter (observed on the site; may change).
YEREVAN_DISTRICTS = {
    "Ajapnyak": 2, "Arabkir": 3, "Avan": 4, "Davtashen": 5, "Erebuni": 6,
    "Kanaker-Zeytun": 7, "Kentron": 8, "Malatia-Sebastia": 9, "Nor Nork": 10,
    "Nork-Marash": 11, "Nubarashen": 12, "Shengavit": 13,
}

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

mcp = MCPServer(name="listam", instructions=INSTRUCTIONS)
_client: ListAmClient | None = None
_storage: Storage | None = None


def client() -> ListAmClient:
    global _client
    if _client is None:
        _client = ListAmClient(settings)
    return _client


def storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage(settings.data_dir)
    return _storage


def _filter(
    listings: list[ListingSummary],
    price_min: float | None,
    price_max: float | None,
    currency: str,
    exclude_agencies: bool,
) -> list[ListingSummary]:
    rate = settings.rates.get(currency.upper(), 1.0)
    lo = price_min * rate if price_min is not None else None
    hi = price_max * rate if price_max is not None else None
    out = []
    for item in listings:
        if exclude_agencies and item.is_agency:
            continue
        if lo is not None or hi is not None:
            amd = item.price.amount_amd_approx if item.price else None
            if amd is None or (lo is not None and amd < lo) or (hi is not None and amd > hi):
                continue
        out.append(item)
    return out


async def _run_search(
    url: str,
    max_pages: int,
    limit: int,
    price_min: float | None = None,
    price_max: float | None = None,
    currency: str = "AMD",
    exclude_agencies: bool = False,
    full: bool = False,
) -> dict[str, Any]:
    try:
        pages = await client().search_pages(url, max_pages)
    except ListAmError as exc:
        return {"error": str(exc), "url": url}
    seen: set[str] = set()
    listings = []
    for page in pages:
        for item in page.listings:
            if item.id not in seen:
                seen.add(item.id)
                listings.append(item)
    scanned = len(listings)
    listings = _filter(listings, price_min, price_max, currency, exclude_agencies)
    return {
        "url": url,
        "heading": pages[0].heading if pages else None,
        "total_ads_on_site": pages[0].total_ads if pages else None,
        "pages_fetched": len(pages),
        "scanned": scanned,
        "returned": min(len(listings), limit),
        "next_page_url": pages[-1].next_url if pages else None,
        "listings": [item.to_dict(compact=not full) for item in listings[:limit]],
    }


# ============================================================================ tools


@mcp.tool(annotations=READ_ONLY)
async def search_listings(
    query: Annotated[str | None, Field(description="Free-text search, e.g. 'iphone 15' or 'toyota camry'.")] = None,
    category_id: Annotated[int | None, Field(description="list.am category ID from list_categories. Omit to search all categories.")] = None,
    region_id: Annotated[int | None, Field(description=f"list.am location ID. Yerevan districts: {YEREVAN_DISTRICTS}.")] = None,
    price_min: Annotated[float | None, Field(description="Minimum price in `currency`.")] = None,
    price_max: Annotated[float | None, Field(description="Maximum price in `currency`.")] = None,
    currency: Annotated[str, Field(description="Currency of price_min/price_max: AMD, USD, EUR or RUB.")] = "AMD",
    exclude_agencies: Annotated[bool, Field(description="Drop ads marked as posted by an agency.")] = False,
    page: Annotated[int, Field(ge=1, description="Result page to start from (only with category_id).")] = 1,
    max_pages: Annotated[int, Field(ge=1, le=5, description="How many result pages to fetch (each ~1.5s).")] = 1,
    limit: Annotated[int, Field(ge=1, le=200, description="Max listings to return.")] = 50,
    extra_params: Annotated[dict[str, str] | None, Field(description="Raw list.am URL query params. See get_filters for a category's own filters.")] = None,
    full: Annotated[bool, Field(description="Return every field (images, seller badges, raw card text) instead of compact rows.")] = False,
) -> dict[str, Any]:
    """Search list.am listings in any category.

    Price filters are applied locally to the fetched pages using approximate
    exchange rates, so fetch more pages when filtering heavily.
    """
    url = client().search_url(query, category_id, page, region_id, extra_params)
    return await _run_search(url, max_pages, limit, price_min, price_max, currency, exclude_agencies, full)


@mcp.tool(annotations=READ_ONLY)
async def search_by_url(
    url: Annotated[str, Field(description="A list.am search/category URL, e.g. copied from the browser with filters applied.")],
    max_pages: Annotated[int, Field(ge=1, le=5)] = 1,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    full: Annotated[bool, Field(description="Return every field instead of compact rows.")] = False,
) -> dict[str, Any]:
    """Fetch results for an existing list.am search URL, keeping all its filters."""
    try:
        url = ensure_listam_url(url)
    except ListAmError as exc:
        return {"error": str(exc)}
    return await _run_search(url, max_pages, limit, full=full)


@mcp.tool(annotations=READ_ONLY)
async def get_listing(
    item: Annotated[str, Field(description="Listing ID (e.g. '24201365') or full list.am item URL.")],
) -> dict[str, Any]:
    """Get full details of one listing: price, attributes, description, seller, images, dates."""
    try:
        return (await client().get_listing(item)).to_dict()
    except ListAmError as exc:
        return {"error": str(exc), "item": item}


@mcp.tool(annotations=READ_ONLY)
async def get_listings(
    items: Annotated[list[str], Field(min_length=1, max_length=20, description="Listing IDs or URLs, e.g. from a search result.")],
) -> dict[str, Any]:
    """Get full details of several listings in one call (fetched one at a time, throttled).

    Use this to check a handful of candidates from a search instead of calling
    get_listing repeatedly.
    """
    out = []
    for item in items:
        try:
            out.append((await client().get_listing(item)).to_dict())
        except ListAmError as exc:
            out.append({"item": item, "error": str(exc)})
    return {"listings": out}


@mcp.tool(annotations=READ_ONLY)
async def list_categories(
    category_id: Annotated[int | None, Field(description="Parent category ID. Omit for the top-level categories.")] = None,
) -> dict[str, Any]:
    """List list.am categories (ID and name). Pass a category_id to see its subcategories."""
    try:
        cats = await client().categories(category_id)
    except ListAmError as exc:
        return {"error": str(exc)}
    return {"parent": category_id, "categories": [c.__dict__ for c in cats]}


@mcp.tool(annotations=READ_ONLY)
async def get_filters(
    category_id: Annotated[int, Field(description="Category ID from list_categories. Works best on a leaf category (e.g. 'Passenger Cars', not 'Vehicles').")],
) -> dict[str, Any]:
    """List a category's own filters (condition, rooms, mileage, transmission...) and their values.

    Each filter's `param` is a key to set in search_listings' extra_params (or in a
    URL for search_by_url/save_search). A "select" filter takes one of `options`'
    values. A "range" filter instead has a `param`/`to_param` pair for the from/to
    bounds (e.g. floor-from, floor-to) and no options. A `multi: true` filter accepts
    more than one value on list.am, but extra_params can only set one value per key;
    for more than one, apply the filter on list.am in a browser and use search_by_url.
    """
    try:
        filters = await client().filters(category_id)
    except ListAmError as exc:
        return {"error": str(exc)}
    if not filters:
        return {
            "category_id": category_id,
            "filters": [],
            "hint": "No filters found. Try a leaf subcategory from list_categories, not a top-level one.",
        }
    return {"category_id": category_id, "filters": [f.to_dict() for f in filters]}


@mcp.tool()
async def save_search(
    name: Annotated[str, Field(description="Short unique name, e.g. 'arabkir-2room'.")],
    url: Annotated[str | None, Field(description="list.am search URL. Alternatively pass query/category_id/region_id.")] = None,
    query: str | None = None,
    category_id: int | None = None,
    region_id: int | None = None,
) -> dict[str, Any]:
    """Save a search so check_saved_search can report only new listings later."""
    try:
        url = ensure_listam_url(url) if url else client().search_url(query, category_id, 1, region_id)
    except ListAmError as exc:
        return {"error": str(exc)}
    storage().save_search(name, url)
    return {"saved": name, "url": url, "hint": "Run check_saved_search once now to set the baseline."}


@mcp.tool(annotations=ToolAnnotations(open_world_hint=True))
async def check_saved_search(
    name: str,
    max_pages: Annotated[int, Field(ge=1, le=5)] = 2,
) -> dict[str, Any]:
    """Return listings that appeared since the last check of a saved search.

    The first check records a baseline and returns everything currently listed.
    """
    row = storage().get_search(name)
    if row is None:
        return {"error": f"No saved search named '{name}'.", "saved": storage().list_searches()}
    first_run = row["last_checked"] is None
    result = await _run_search(row["url"], max_pages, limit=200)
    if "error" in result:
        return result
    ids = [item["id"] for item in result["listings"]]
    new_ids = storage().mark_seen(name, ids)
    new = [item for item in result["listings"] if item["id"] in new_ids]
    return {
        "name": name,
        "url": row["url"],
        "first_run": first_run,
        "scanned": len(ids),
        "new_count": len(new),
        "new_listings": new,
    }


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
async def list_saved_searches() -> list[dict]:
    """List saved searches with their last check time."""
    return storage().list_searches()


@mcp.tool(annotations=ToolAnnotations(destructive_hint=True))
async def delete_saved_search(name: str) -> dict[str, Any]:
    """Delete a saved search and its seen-listing history."""
    return {"deleted": storage().delete_search(name), "name": name}


# ============================================================================ entry point


def main() -> None:
    import argparse
    import asyncio
    import json

    parser = argparse.ArgumentParser(prog="listam-mcp", description="list.am MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    sub = parser.add_subparsers(dest="cmd")
    s = sub.add_parser("search", help="Debug: run a search and print JSON")
    s.add_argument("query", nargs="?")
    s.add_argument("--category", type=int)
    s.add_argument("--url")
    g = sub.add_parser("get", help="Debug: fetch one listing")
    g.add_argument("item")
    c = sub.add_parser("categories", help="Debug: list categories")
    c.add_argument("category_id", nargs="?", type=int)
    args = parser.parse_args()

    if args.cmd:
        async def run() -> Any:
            if args.cmd == "search":
                if args.url:
                    return await search_by_url(args.url, limit=10)
                return await search_listings(query=args.query, category_id=args.category, limit=10)
            if args.cmd == "get":
                return await get_listing(args.item)
            return await list_categories(args.category_id)

        print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
        return

    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
