"""HTML parsing for list.am pages.

list.am has no public API, so everything here is based on the public HTML.
The parser deliberately relies on stable signals (URLs like /item/<id> and
/category/<id>, <h1>, og: meta tags, visible text) rather than CSS class
names, which change more often. When the site layout changes, this is the
only module that should need updating.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from .config import settings
from .models import Category, Filter, ListingDetail, ListingSummary, Price, SearchPage

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "₽": "RUB", "֏": "AMD"}
_SYM = "".join(re.escape(s) for s in CURRENCY_SYMBOLS)
_NUM = r"\d[\d,]*(?:\.\d+)?"
PRICE_RE = re.compile(
    rf"(?P<pre>[{_SYM}])\s?(?P<n1>{_NUM})|(?P<n2>{_NUM})\s?(?P<post>[{_SYM}])"
)
PERIOD_WORDS = {
    "month": ("monthly", "per month", "ամսական", "в месяц", "месяц"),
    "day": ("daily", "per day", "օրական", "в сутки", "сутки", "в день"),
}
AGENCY_WORDS = ("agency", "գործակալություն", "агентство")
OWNER_WORDS = ("listed by owner", "սեփականատեր", "от собственника", "собственник")
ITEM_HREF_RE = re.compile(r"/item/(\d+)")
CATEGORY_HREF_RE = re.compile(r"/category/(\d+)(?:[/?#]|$)")
TOTAL_ADS_RE = re.compile(r"([\d,]+)\s+(?:Ads|հայտարարություն|объявлени)", re.IGNORECASE)


# --------------------------------------------------------------------------- helpers


def _soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup


def _lines(node: Tag) -> list[str]:
    """Visible text of a node as de-duplicated, non-empty lines."""
    out: list[str] = []
    for s in node.stripped_strings:
        s = re.sub(r"\s+", " ", s)
        if s and (not out or out[-1] != s):
            out.append(s)
    return out


def absolute(url: str) -> str:
    return urljoin(settings.base_url + "/", url)


def clean_item_url(url: str) -> str:
    """Drop tracking params such as ?ld_src=2."""
    p = urlparse(absolute(url))
    return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))


def parse_price(text: str, rates: dict[str, float] | None = None) -> Price | None:
    """Parse strings like '$1,400 monthly', '250,000 ֏', '€1,700'."""
    m = PRICE_RE.search(text)
    if not m:
        return None
    symbol = m.group("pre") or m.group("post")
    number = (m.group("n1") or m.group("n2")).replace(",", "")
    try:
        amount = float(number)
    except ValueError:
        return None
    currency = CURRENCY_SYMBOLS[symbol]
    tail = text[m.end(): m.end() + 25].lower()
    period = next(
        (p for p, words in PERIOD_WORDS.items() if any(w in tail for w in words)), None
    )
    rates = rates if rates is not None else settings.rates
    rate = rates.get(currency)
    return Price(
        amount=amount,
        currency=currency,
        period=period,
        raw=text[m.start(): m.end()].strip(),
        amount_amd_approx=round(amount * rate) if rate else None,
    )


# --------------------------------------------------------------------------- search pages


def _by_class_suffix(node: Tag, *suffixes: str) -> Tag | None:
    """Find a descendant whose class list has an entry ending in one of `suffixes`.

    list.am's BEM-ish class names (`category-data-list-card__amount`, `...__location`)
    are stable across its search-card templates, unlike the short legacy classes
    ("l", "at", "p") which are reused for different fields depending on the template.
    """
    return node.find(attrs={"class": lambda c: c and any(cls.endswith(suffixes) for cls in c.split())})


def _card_title(a: Tag) -> str | None:
    # Keyword-search results use .dltitle > .pt; category-browse grids use a bare div.l.
    el = a.select_one(".pt") or a.find("div", class_="l")
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el and el.get_text(strip=True) else None


def _card_price_text(a: Tag) -> str | None:
    # The amount and currency symbol live in separate nested <span>s (order varies by
    # currency), and the "monthly"/"daily" frequency is a sibling span when present.
    amount = _by_class_suffix(a, "__amount")
    if not amount:
        return None
    text = amount.get_text(" ", strip=True)
    freq = _by_class_suffix(a, "__price-frequency")
    if freq:
        text += " " + freq.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _card_location(a: Tag) -> str | None:
    el = _by_class_suffix(a, "__location", "__where-when")
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else None


def _parse_card(a: Tag) -> ListingSummary | None:
    m = ITEM_HREF_RE.search(a.get("href", ""))
    if not m:
        return None
    parts = _lines(a)
    if not parts:
        return None
    text = " | ".join(parts)

    title = _card_title(a) or parts[0]
    price_text = _card_price_text(a)
    price = parse_price(price_text) if price_text else None
    location = _card_location(a)

    lowered = [p.lower() for p in parts]
    is_agency = any(any(w in p for w in AGENCY_WORDS) for p in lowered)

    exclude = set(price_text.split()) if price_text else set()
    exclude.update({title, location})
    # "›" lines are the category breadcrumb, shown only on keyword-search results.
    rest = [p for p in parts if p not in exclude and "›" not in p]
    details = next((p for p in rest if "," in p or "·" in p), None)
    badges = [p for p in rest if p != details]

    img = a.find("img")
    image = None
    if img:
        image = img.get("data-original") or img.get("data-src") or img.get("src")
        image = absolute(image) if image and not image.startswith("data:") else None

    return ListingSummary(
        id=m.group(1),
        url=clean_item_url(a["href"]),
        title=title,
        price=price,
        location=location,
        details=details,
        is_agency=is_agency,
        badges=badges,
        image=image,
        text=text,
    )


def _find_next_url(soup: BeautifulSoup, current_url: str | None) -> str | None:
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True).lower()
        if a.get("rel") == ["next"] or label.startswith(("next", "հաջորդ", "следующ")):
            return absolute(a["href"])
    # Fallback: /category/<id>/<n+1> pagination links.
    if current_url:
        m = re.search(r"/category/(\d+)(?:/(\d+))?", urlparse(current_url).path)
        if m:
            nxt = int(m.group(2) or 1) + 1
            for a in soup.find_all("a", href=True):
                if re.search(rf"/category/{m.group(1)}/{nxt}(?:\?|$)", a["href"]):
                    return absolute(a["href"])
    return None


def parse_search_page(html: str, url: str | None = None) -> SearchPage:
    soup = _soup(html)
    seen: set[str] = set()
    listings: list[ListingSummary] = []
    for a in soup.find_all("a", href=ITEM_HREF_RE):
        card = _parse_card(a)
        if card and card.id not in seen:
            seen.add(card.id)
            listings.append(card)

    total = None
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and (m := TOTAL_ADS_RE.search(meta.get("content", ""))):
        total = int(m.group(1).replace(",", ""))

    h1 = soup.find("h1")
    return SearchPage(
        listings=listings,
        next_url=_find_next_url(soup, url),
        total_ads=total,
        heading=h1.get_text(" ", strip=True) if h1 else None,
    )


# --------------------------------------------------------------------------- item pages


def _after(lines: list[str], label: str) -> str | None:
    for i, line in enumerate(lines[:-1]):
        if line.lower() == label.lower():
            return lines[i + 1]
    return None


def parse_listing_page(html: str, url: str) -> ListingDetail:
    soup = _soup(html)
    item_id = (ITEM_HREF_RE.search(url) or re.search(r"(\d+)", url)).group(1)
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""

    main = soup.find("body") or soup
    lines = _lines(main)
    start = lines.index(title) if title in lines else 0
    body = lines[start:]

    price = None
    for i, line in enumerate(body[1:8], start=1):
        price = parse_price(line)
        if price:
            if price.period is None and i + 1 < len(body):
                price = parse_price(f"{line} {body[i + 1]}") or price
            break

    # Breadcrumb: the first ordered/unordered list containing category links.
    category_path: list[str] = []
    for lst in soup.find_all(["ol", "ul"]):
        links = [a for a in lst.find_all("a", href=True) if CATEGORY_HREF_RE.search(a["href"])]
        if links:
            category_path = [a.get_text(" ", strip=True) for a in links if a.get_text(strip=True)]
            break

    # Description: lines after "Description" up to the ad metadata footer.
    description = None
    for i, line in enumerate(body):
        if line.lower() in ("description", "նկարագրություն", "описание"):
            chunk = []
            for nxt in body[i + 1:]:
                if nxt.startswith(("Ad id", "Translated from", "Հայտարարության", "Объявление")):
                    break
                chunk.append(nxt)
            description = "\n".join(chunk).strip() or None
            break

    joined = " ".join(body)
    posted = m.group(1) if (m := re.search(r"Posted\s+([\d.]+)", joined)) else None
    renewed = m.group(1) if (m := re.search(r"Renewed\s+([\d.]+(?:,\s*[\d:]+)?)", joined)) else None

    lowered = joined.lower()
    seller_type = (
        "owner" if any(w in lowered for w in OWNER_WORDS)
        else "agency" if any(w in lowered for w in AGENCY_WORDS)
        else None
    )
    seller = soup.find("a", href=re.compile(r"/user/\d+"))
    seller_name = None
    if seller:
        seller_name = re.sub(r"^Avatar", "", seller.get_text(" ", strip=True)).strip() or None

    images: list[str] = []
    og = soup.find("meta", attrs={"property": "og:image"})
    if og and og.get("content"):
        images.append(og["content"])
    for img in soup.find_all("img"):
        src = img.get("data-original") or img.get("data-src") or img.get("src") or ""
        if re.search(r"(?:s|img)\.list\.am/f/", src):
            src = absolute(src)
            if src not in images:
                images.append(src)

    # Everything between price and description holds the structured attributes
    # (area, floor, rooms, renovation...). Label/value order varies by section,
    # so it is returned as ordered lines for the model to interpret.
    end = next(
        (i for i, line in enumerate(body) if line.lower() in ("description", "նկարագրություն", "описание")),
        min(len(body), 120),
    )
    attributes_text = body[1:end][:120]

    return ListingDetail(
        id=item_id,
        url=clean_item_url(url),
        title=title,
        price=price,
        category_path=category_path,
        location=_after(body, "Location"),
        description=description,
        seller_name=seller_name,
        seller_url=absolute(seller["href"]) if seller else None,
        seller_type=seller_type,
        posted=posted,
        renewed=renewed,
        images=images[:15],
        attributes_text=attributes_text,
    )


# --------------------------------------------------------------------------- categories


def parse_categories(html: str) -> list[Category]:
    soup = _soup(html)
    out: dict[int, Category] = {}
    for a in soup.find_all("a", href=CATEGORY_HREF_RE):
        name = a.get_text(" ", strip=True)
        if not name or name.startswith("/"):
            continue
        cid = int(CATEGORY_HREF_RE.search(a["href"]).group(1))
        if cid not in out:
            out[cid] = Category(id=cid, name=name, url=clean_item_url(a["href"]))
    return list(out.values())


# --------------------------------------------------------------------------- filters


# Params already covered by dedicated search_listings/save_search arguments (region_id,
# price_min/max/currency) or that are internal UI state, not content filters.
_FILTER_SKIP_PARAMS = {"n", "s", "crc", "mid", "seen_ids", "unseen"}


def _select_filters_a(form: Tag) -> list[Filter]:
    """Simple attribute filters: a `.label-container` + `.options-container` widget
    (e.g. Transmission, Drive Type, Number of Rooms). Multi-select ones have a
    checkbox per option, named "<param>[]"."""
    out = []
    for opts in form.select(".options-container"):
        widget = opts.parent
        label_el = widget.find("div", class_="label-container") if widget else None
        if not label_el:
            continue
        checkbox = opts.find("input", type="checkbox")
        if checkbox and checkbox.get("name"):
            param, multi = checkbox["name"], True
        else:
            hidden = widget.find("input", type="hidden")
            param, multi = (hidden.get("name") if hidden else None), False
        if not param or param.removesuffix("[]") in _FILTER_SKIP_PARAMS:
            continue
        options = [
            {"value": o["data-value"], "label": o.get_text(" ", strip=True)}
            for o in opts.find_all("div", class_="option-container", recursive=False)
            if o.get("data-value") and o.get_text(strip=True)
        ]
        if options:
            out.append(Filter(param=param, label=label_el.get_text(strip=True), type="select",
                               multi=multi, options=options))
    return out


def _select_filters_b(form: Tag) -> list[Filter]:
    """Searchable dropdown filters (e.g. vehicle Make): a `.mu-wrapper` widget whose
    options carry both `data-value` and `data-name`. Dependent sub-filters with no
    static values (e.g. Model, which depends on Make) are skipped automatically."""
    out = []
    for wrapper in form.select(".mu-wrapper"):
        label_el = wrapper.select_one(".label-wrapper > p")
        hidden = wrapper.find("input", type="hidden")
        if not label_el or not hidden or not hidden.get("name"):
            continue
        param = hidden["name"]
        if param in _FILTER_SKIP_PARAMS:
            continue
        options = [
            {"value": o["data-value"], "label": o["data-name"]}
            for o in wrapper.select("[data-value][data-name]")
            if o["data-value"] and o["data-name"] and not o["data-name"].startswith("@")
        ]
        if options:
            out.append(Filter(param=param, label=label_el.get_text(strip=True), type="select",
                               multi=True, options=options))
    return out


def _range_filters(form: Tag) -> list[Filter]:
    """Numeric from/to filters (e.g. Floor, Area): a `.ra3` widget with a label and
    two hidden inputs. The price range's own `.ra3` has no label and is skipped, since
    price is already a dedicated search_listings argument."""
    out = []
    for ra in form.select(".ra3"):
        label_el = ra.select_one(".range-component-label > span")
        label = label_el.get_text(strip=True) if label_el else ""
        hidden = [i["name"] for i in ra.find_all("input", type="hidden") if i.get("name")]
        if not label or len(hidden) < 2:
            continue
        out.append(Filter(param=hidden[0], label=label, type="range", to_param=hidden[1]))
    return out


def parse_filters(html: str) -> list[Filter]:
    """Extract a category's filter sidebar: what to pass via search_listings'
    extra_params (or a copied URL to search_by_url) for filters this server doesn't
    expose directly, e.g. condition, rooms, mileage, transmission."""
    soup = _soup(html)
    form = soup.find("form", id="ff")
    if not form:
        return []
    out: list[Filter] = []
    seen: set[str] = set()
    for f in _select_filters_a(form) + _select_filters_b(form) + _range_filters(form):
        if f.param in seen:
            continue
        seen.add(f.param)
        out.append(f)
    return out
