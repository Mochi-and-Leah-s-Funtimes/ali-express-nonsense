"""Search: fetch a result page and turn it into :class:`Product` objects.

AliExpress server-renders the first page of results into the HTML, so the fast
path is a plain HTTP GET plus an HTML parse -- no browser required.  When the
markup shape changes (or the site starts challenging plain requests) the
browser transport is used instead, which renders the grid client side.
"""

from __future__ import annotations

import re
from decimal import Decimal

from lxml import html as lxml_html

from .models import Product, discount_from_text, rating_from_text
from .money import detect_currency, parse_price
from .transport import BASE, AliBrowser, AliHTTP, BlockedError

_ITEM_ID_PATTERNS = (
    re.compile(r"/item/(\d{6,})\.html"),
    re.compile(r"[?&]productIds=(\d{6,})"),
    # ``utparam-url`` is percent encoded, so the separator can be ``=`` or ``%3A``.
    re.compile(r"x_object_id(?:=|%3A)(\d{6,})"),
)

_CARD_XPATH = "//div[contains(@class, 'search-item-card-wrapper-gallery')]"


def extract_product_id(href: str | None) -> str | None:
    """Pull the numeric product id out of any of AliExpress' link shapes."""
    if not href:
        return None
    for pattern in _ITEM_ID_PATTERNS:
        match = pattern.search(href)
        if match:
            return match.group(1)
    return None


def absolute(href: str | None) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http"):
        return href
    return BASE + href


def _first(element, xpath: str) -> str | None:
    found = element.xpath(xpath)
    if not found:
        return None
    value = found[0]
    if isinstance(value, str):
        return value.strip()
    return value


def _text(element, xpath: str) -> str | None:
    found = element.xpath(xpath)
    if not found:
        return None
    value = found[0]
    if hasattr(value, "text_content"):
        value = value.text_content()
    return str(value).strip()


def parse_cards(html_text: str) -> list[Product]:
    """Parse the product grid out of a rendered or server-rendered page."""
    if not html_text:
        return []
    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        return []

    products: list[Product] = []
    seen: set[str] = set()

    for card in doc.xpath(_CARD_XPATH):
        anchor = card.xpath(".//a[contains(@class, 'search-card-item')]")
        href = anchor[0].get("href") if anchor else None
        product_id = extract_product_id(href)
        if not product_id or product_id in seen:
            continue

        title = _first(card, ".//div[contains(@class, 'us--title--')]/@title")
        if not title:
            title = _text(card, ".//h3[contains(@class, 'us--titleText--')]")

        price_text = _first(card, ".//div[contains(@class, 'us--price--')]/@aria-label")
        if not price_text:
            price_text = _text(card, ".//div[contains(@class, 'us--price--')]")
        original_text = _text(card, ".//div[contains(@class, 'us--price-original--')]")
        discount_text = _text(card, ".//span[contains(@class, 'us--minDiscount--')]")
        rating_text = _text(card, ".//span[contains(@class, 'us--starRating--')]")
        sold_text = _text(card, ".//span[contains(@class, 'us--trade--')]")
        image = _first(card, ".//img[contains(@class, 'images--item--')]/@src")

        currency = detect_currency(price_text or original_text or "")
        price = parse_price(price_text)
        original = parse_price(original_text)
        if price is None and original is not None:
            price, original = original, None

        seen.add(product_id)
        products.append(
            Product(
                product_id=product_id,
                title=(title or "").strip(),
                url=f"{BASE}/item/{product_id}.html",
                price=price,
                original_price=original,
                currency=currency,
                discount_pct=discount_from_text(discount_text),
                rating=rating_from_text(rating_text),
                sold=sold_text,
                image=absolute(image) if image else None,
            )
        )
    return products


def search(
    query: str,
    page: int = 1,
    pages: int = 1,
    http: AliHTTP | None = None,
    browser: AliBrowser | None = None,
    use_browser: bool = False,
) -> list[Product]:
    """Search AliExpress for *query*.

    *pages* controls how many result pages are fetched; AliExpress exposes
    roughly a dozen products per page.
    """
    http = http or AliHTTP()
    products: list[Product] = []
    seen: set[str] = set()

    for offset in range(pages):
        current = page + offset
        url = http.search_url(query, current)
        if use_browser:
            assert browser is not None, "browser transport requested but not provided"
            result = browser.fetch(url, wait_ms=7000, scroll=3)
            found = parse_cards(result.html)
        else:
            try:
                found = parse_cards(http.get_text(url))
            except BlockedError:
                if browser is None:
                    raise
                result = browser.fetch(url, wait_ms=7000, scroll=3)
                found = parse_cards(result.html)

        for product in found:
            if product.product_id in seen:
                continue
            seen.add(product.product_id)
            products.append(product)

    return products


def filter_products(
    products: list[Product],
    max_price: Decimal | None = None,
    min_price: Decimal | None = None,
    min_rating: float | None = None,
) -> list[Product]:
    """Apply the price and rating filters."""
    if max_price is not None:
        products = [p for p in products if p.price is not None and p.price <= max_price]
    if min_price is not None:
        products = [p for p in products if p.price is not None and p.price >= min_price]
    if min_rating is not None:
        products = [p for p in products if p.rating is not None and p.rating >= min_rating]
    return products


def price_bounds(products: list[Product]) -> tuple[Decimal | None, Decimal | None]:
    """Return ``(lowest, highest)`` price among *products* that have one."""
    prices = [p.price for p in products if p.price is not None]
    if not prices:
        return None, None
    return min(prices), max(prices)


def sort_products(products: list[Product], key: str = "price") -> list[Product]:
    """Sort results.  Missing prices always sort last."""
    if key == "price":
        return sorted(products, key=lambda p: (p.price is None, p.price or Decimal(0)))
    if key == "-price":
        return sorted(products, key=lambda p: (p.price is None, -(p.price or Decimal(0))))
    if key == "rating":
        return sorted(products, key=lambda p: (p.rating is None, -(p.rating or 0)))
    if key == "discount":
        return sorted(products, key=lambda p: (p.discount is None, -(p.discount or 0)))
    return products
