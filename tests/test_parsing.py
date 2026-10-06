from pathlib import Path

import pytest

from listam_mcp.client import ListAmClient, ListAmError, ensure_listam_url
from listam_mcp.parsing import (
    parse_categories,
    parse_filters,
    parse_listing_page,
    parse_price,
    parse_search_page,
)
from listam_mcp.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"
RATES = {"AMD": 1.0, "USD": 400.0, "EUR": 450.0, "RUB": 5.0}


@pytest.mark.parametrize(
    "text, amount, currency, period",
    [
        ("$1,400 monthly", 1400, "USD", "month"),
        ("250,000 ֏ monthly", 250000, "AMD", "month"),
        ("250,000֏monthly", 250000, "AMD", "month"),
        ("10,000֏daily", 10000, "AMD", "day"),
        ("€1,700", 1700, "EUR", None),
        ("311,875 ₽ monthly", 311875, "RUB", "month"),
        ("45,000 ֏ օրական", 45000, "AMD", "day"),
    ],
)
def test_parse_price(text, amount, currency, period):
    p = parse_price(text, RATES)
    assert (p.amount, p.currency, p.period) == (amount, currency, period)
    assert p.amount_amd_approx == round(amount * RATES[currency])


def test_parse_price_none():
    assert parse_price("Negotiable") is None


def test_parse_search_page():
    page = parse_search_page((FIXTURES / "search.html").read_text(), "https://www.list.am/en/category/56")
    assert page.total_ads == 25122
    assert page.heading == "Long Term Apartment Rentals"
    assert page.next_url == "https://www.list.am/category/56/2"
    ids = ["995104779", "24201365", "24000613", "24500001"]
    assert [x.id for x in page.listings] == ids  # duplicate dropped

    first = page.listings[0]
    assert first.url == "https://www.list.am/en/item/995104779"
    assert first.price.amount == 1400 and first.price.currency == "USD" and first.price.period == "month"
    assert first.title.startswith("2 room apartment on Nar-Dos Street")
    assert first.details == "2 rm., 45 sq.m., 10/12 floor"
    assert first.location == "Kentron"
    assert first.is_agency
    assert first.image == "https://s.list.am/r/779/1.jpg"

    owner = page.listings[1]
    assert not owner.is_agency and "Listed by Owner" in owner.badges

    # Keyword-search results use a different card template (title in .dltitle > .pt,
    # plus a category breadcrumb on the card) that must not be mistaken for the title.
    studio = page.listings[3]
    assert studio.title == "Studio apartment near Republic Square"
    assert studio.price.amount == 220000 and studio.price.currency == "AMD" and studio.price.period == "month"
    assert studio.details == "Studio · 30 sq.m."
    assert studio.location == "Kentron"


def test_parse_listing_page():
    d = parse_listing_page(
        (FIXTURES / "item.html").read_text(), "https://www.list.am/en/item/24201365?ld_src=2"
    )
    assert d.id == "24201365"
    assert d.url == "https://www.list.am/en/item/24201365"
    assert d.title.startswith("3 room apartment in a new building")
    assert d.price.amount == 3700 and d.price.currency == "USD" and d.price.period == "month"
    assert d.category_path == ["Real Estate", "Long Term Rentals", "Apartments"]
    assert d.location == "10 Vazgen Sargsyan Street, Yerevan"
    assert d.description.startswith("Bright apartment near")
    assert d.seller_type == "owner"
    assert d.seller_name == "Anna Petrosyan"
    assert d.posted == "08.09.2026" and d.renewed.startswith("18.09.2026")
    assert d.images[0] == "https://s.list.am/f/953/101566953.jpg"
    assert "Floor Area" in d.attributes_text


def test_parse_categories():
    cats = parse_categories((FIXTURES / "search.html").read_text())
    names = {c.id: c.name for c in cats}
    assert names[54] == "Real Estate" and names[1377] == "Houses"


def test_parse_filters():
    filters = {f.param: f for f in parse_filters((FIXTURES / "filters.html").read_text())}

    assert "n" not in filters and "mid" not in filters and "crc" not in filters and "pmin" not in filters

    transmission = filters["_a13"]
    assert transmission.label == "Transmission" and not transmission.multi
    assert {o["value"]: o["label"] for o in transmission.options} == {"1": "Manual", "2": "Automatic"}

    rooms = filters["_a4[]"]
    assert rooms.label == "Number of Rooms" and rooms.multi
    assert [o["value"] for o in rooms.options] == ["1", "2"]

    make = filters["bid"]
    assert make.label == "Make" and make.multi
    assert {o["value"]: o["label"] for o in make.options} == {"49": "Mercedes-Benz", "76": "Toyota"}

    floor = filters["_a11_1"]
    assert floor.type == "range" and floor.to_param == "_a11_2" and floor.options == []


def test_listing_summary_compact():
    page = parse_search_page((FIXTURES / "search.html").read_text())
    full = page.listings[0].to_dict(compact=False)
    compact = page.listings[0].to_dict(compact=True)
    for key in ("id", "title", "price", "location", "details", "url"):
        assert key in compact
    for key in ("text", "badges", "image", "is_agency"):
        assert key in full and key not in compact


@pytest.mark.parametrize(
    "url", ["https://evil.com/x", "https://list.am.evil.com/", "file:///etc/passwd", "https://notlist.am/"]
)
def test_rejects_non_listam(url):
    with pytest.raises(ListAmError):
        ensure_listam_url(url)


def test_accepts_listam():
    assert ensure_listam_url("http://www.list.am/en/item/1") == "https://www.list.am/en/item/1"


def test_search_url():
    c = ListAmClient()
    assert c.search_url("iphone") == "https://www.list.am/en/category?q=iphone"
    assert (
        c.search_url(None, 56, page=3, region_id=8)
        == "https://www.list.am/en/category/56/3?n=8"
    )


def test_storage(tmp_path):
    s = Storage(tmp_path)
    s.save_search("a", "https://www.list.am/en/category/56")
    assert s.mark_seen("a", ["1", "2"]) == {"1", "2"}
    assert s.mark_seen("a", ["2", "3"]) == {"3"}
    assert s.list_searches()[0]["seen_count"] == 3
    assert s.delete_search("a") and s.list_searches() == []


async def test_tools_registered():
    from mcp import Client

    from listam_mcp.server import mcp

    async with Client(mcp) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    assert {"search_listings", "search_by_url", "get_listing", "get_listings", "get_filters",
            "list_categories", "save_search", "check_saved_search"} <= tools
