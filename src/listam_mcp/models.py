"""Plain data models returned by the parser and the MCP tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Price:
    amount: float
    currency: str  # AMD, USD, EUR, RUB
    period: str | None = None  # "month", "day" or None for one-off prices
    raw: str = ""
    amount_amd_approx: float | None = None


@dataclass
class ListingSummary:
    id: str
    url: str
    title: str
    price: Price | None = None
    location: str | None = None
    details: str | None = None
    is_agency: bool = False
    badges: list[str] = field(default_factory=list)
    image: str | None = None
    text: str = ""  # full card text, useful when heuristics miss something

    # Fields dropped from the compact (default) search result rows. They're still
    # available per-listing from get_listing/get_listings.
    _FULL_ONLY = ("is_agency", "badges", "image", "text")

    def to_dict(self, compact: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if compact:
            for key in self._FULL_ONLY:
                d.pop(key, None)
        return d


@dataclass
class ListingDetail:
    id: str
    url: str
    title: str
    price: Price | None = None
    category_path: list[str] = field(default_factory=list)
    location: str | None = None
    description: str | None = None
    seller_name: str | None = None
    seller_url: str | None = None
    seller_type: str | None = None  # "owner", "agency" or None
    posted: str | None = None
    renewed: str | None = None
    images: list[str] = field(default_factory=list)
    attributes_text: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchPage:
    listings: list[ListingSummary]
    next_url: str | None
    total_ads: int | None
    heading: str | None


@dataclass
class Category:
    id: int
    name: str
    url: str


@dataclass
class Filter:
    """A category-specific filter, as scraped from list.am's filter sidebar.

    `param` is the extra_params/search_by_url query key. A "select" filter takes one
    of `options`' values; a multi-select one has `multi=True` and `param` ending in
    "[]". A "range" filter instead has `to_param` set (a `param`..`to_param` pair,
    e.g. floor-from/floor-to) and no options.
    """

    param: str
    label: str
    type: str  # "select" or "range"
    multi: bool = False
    options: list[dict[str, str]] = field(default_factory=list)
    to_param: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
