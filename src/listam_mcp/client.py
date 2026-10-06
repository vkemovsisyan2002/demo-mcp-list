"""Async HTTP client for list.am: rate limited, cached, restricted to list.am."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlencode, urlparse

import httpx

from .config import Settings, settings
from .models import Filter, ListingDetail, SearchPage
from .parsing import parse_categories, parse_filters, parse_listing_page, parse_search_page


class ListAmError(Exception):
    """Raised for user-facing errors (bad URL, not found, blocked)."""


def ensure_listam_url(url: str) -> str:
    """Only allow list.am URLs, so tools can't be used to fetch arbitrary hosts."""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not (host == "list.am" or host.endswith(".list.am")):
        raise ListAmError(f"Only list.am URLs are supported, got: {url}")
    return parsed._replace(scheme="https").geturl()


class ListAmClient:
    def __init__(self, cfg: Settings = settings) -> None:
        self.cfg = cfg
        self._http = httpx.AsyncClient(
            timeout=cfg.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": cfg.user_agent,
                "Accept-Language": {"hy": "hy", "ru": "ru"}.get(cfg.lang, "en") + ",en;q=0.8",
            },
        )
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._cache: dict[str, tuple[float, str]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ low level

    async def get_html(self, url: str) -> str:
        url = ensure_listam_url(url)
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached and now - cached[0] < self.cfg.cache_ttl:
            return cached[1]

        async with self._lock:  # serialize requests, and back off together on a block
            text = await self._fetch(url)
        self._cache[url] = (time.monotonic(), text)
        return text

    async def _fetch(self, url: str) -> str:
        # list.am sits behind Cloudflare, which can 403 a request as a short-lived,
        # burst-triggered block rather than a real ban; retrying after a pause (with
        # every other queued request waiting on the same lock) usually clears it.
        backoff = self.cfg.min_interval
        for attempt in range(self.cfg.max_retries + 1):
            wait = self.cfg.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await self._http.get(url)
            except httpx.HTTPError as exc:
                raise ListAmError(f"Request to list.am failed: {exc}") from exc
            finally:
                self._last_request = time.monotonic()

            if resp.status_code == 404:
                raise ListAmError("Not found (the ad may have been removed).")
            if resp.status_code in (403, 429) and attempt < self.cfg.max_retries:
                await asyncio.sleep(backoff)
                backoff *= 3
                continue
            if resp.status_code in (403, 429):
                raise ListAmError(
                    f"list.am refused the request (HTTP {resp.status_code}) after retrying. "
                    "Slow down (LISTAM_MIN_INTERVAL) and try again later."
                )
            resp.raise_for_status()
            return resp.text
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ URLs

    def search_url(
        self,
        query: str | None = None,
        category_id: int | None = None,
        page: int = 1,
        region_id: int | None = None,
        extra_params: dict[str, str] | None = None,
    ) -> str:
        path = f"/{self.cfg.lang}/category"
        if category_id:
            path += f"/{category_id}"
            if page > 1:
                path += f"/{page}"
        params: dict[str, str] = {}
        if query:
            params["q"] = query
        if region_id:
            params["n"] = str(region_id)
        params.update(extra_params or {})
        qs = urlencode(params)
        return f"{self.cfg.base_url}{path}" + (f"?{qs}" if qs else "")

    def item_url(self, item: str) -> str:
        item = item.strip()
        if item.isdigit():
            return f"{self.cfg.base_url}/{self.cfg.lang}/item/{item}"
        return ensure_listam_url(item)

    # ------------------------------------------------------------------ high level

    async def search_pages(self, start_url: str, max_pages: int = 1) -> list[SearchPage]:
        pages: list[SearchPage] = []
        url: str | None = start_url
        for _ in range(max(1, min(max_pages, self.cfg.max_pages))):
            if not url:
                break
            page = parse_search_page(await self.get_html(url), url)
            pages.append(page)
            url = page.next_url
        return pages

    async def get_listing(self, item: str) -> ListingDetail:
        url = self.item_url(item)
        return parse_listing_page(await self.get_html(url), url)

    async def categories(self, category_id: int | None = None):
        url = (
            f"{self.cfg.base_url}/{self.cfg.lang}/category/{category_id}"
            if category_id
            else f"{self.cfg.base_url}/{self.cfg.lang}/"
        )
        return parse_categories(await self.get_html(url))

    async def filters(self, category_id: int) -> list[Filter]:
        url = f"{self.cfg.base_url}/{self.cfg.lang}/category/{category_id}"
        return parse_filters(await self.get_html(url))
