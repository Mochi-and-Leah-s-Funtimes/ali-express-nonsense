"""Product detail extraction from the ``mtop`` payload.

The payload is a bag of UI modules rather than a clean product record, so this
module walks the modules that matter for shopping decisions: price per
variant, stock, shipping, coupons, seller reputation and the rating.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .models import Coupon, Offer, ProductDetail
from .money import parse_price
from .mtop import MtopClient
from .transport import AliHTTP

_TAG_RE = re.compile(r"<[^>]+>")


def _text(value: str | None) -> str:
    if not value:
        return ""
    return _TAG_RE.sub("", value).strip()


def _html_texts(node: Any) -> list[str]:
    """Collect every ``content`` string in a layout structure."""
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "content" and isinstance(value, str):
                cleaned = _text(value)
                if cleaned:
                    out.append(cleaned)
            else:
                out.extend(_html_texts(value))
    elif isinstance(node, list):
        for item in node:
            out.extend(_html_texts(item))
    return out


def _shipping_from_module(module: dict[str, Any]) -> tuple[Decimal | None, bool, str | None]:
    """Return ``(cost, free_shipping, delivery_estimate)``."""
    layout = (module.get("deliveryLayoutInfo") or [{}])[0]
    texts = _html_texts(layout.get("titleLayout")) + _html_texts(layout.get("contentLayout"))

    free = any("free shipping" in text.lower() for text in texts)
    estimate = None
    for text in texts:
        if text.lower().startswith("delivery"):
            estimate = text
            break

    biz = layout.get("bizData") or {}
    cost = None
    if not free:
        formatted = biz.get("formattedAmount")
        if formatted:
            cost = parse_price(formatted)
    return cost, free, estimate


def _coupons_from_module(module: dict[str, Any]) -> list[Coupon]:
    coupons: list[Coupon] = []
    for entry in module.get("upperCouponBlockViewList") or []:
        title = _text(entry.get("couponTitle") or entry.get("title") or "")
        if not title:
            continue
        value = parse_price(entry.get("discountAmount") or entry.get("discountValue"))
        coupons.append(
            Coupon(
                code=entry.get("couponCode") or entry.get("code"),
                title=title,
                discount_value=value,
                discount_type=entry.get("discountType"),
                expires=entry.get("expireTime") or entry.get("endTime"),
            )
        )
    return coupons


def _sku_label(sku_attr: str | None) -> str:
    """``"14:504#5-in-1"`` -> ``"5-in-1"``."""
    if not sku_attr:
        return ""
    label = sku_attr.split("#", 1)[1] if "#" in sku_attr else sku_attr
    return label.replace("_", " ").strip()


def _variant_labels(sku_module: dict[str, Any]) -> dict[str, str]:
    """Map ``propertyValueIdLong`` -> human readable value name."""
    labels: dict[str, str] = {}
    for prop in sku_module.get("skuProperties") or []:
        for value in prop.get("skuPropertyValues") or []:
            raw = value.get("propertyValueIdLong") or value.get("propertyValueId")
            if raw is None:
                continue
            name = value.get("propertyValueDisplayName") or value.get("propertyValueName") or ""
            labels[str(raw)] = name
    return labels


def build_detail(product_id: str, payload: dict[str, Any], currency: str = "USD") -> ProductDetail:
    """Map an ``mtop`` result payload onto :class:`ProductDetail`."""
    detail = ProductDetail(product_id=str(product_id), currency=currency)
    detail.url = f"https://www.aliexpress.com/item/{product_id}.html"

    title_module = payload.get("PRODUCT_TITLE") or {}
    detail.title = (title_module.get("text") or "").strip()

    price_module = payload.get("PRICE") or {}
    price_map = price_module.get("skuIdStrPriceInfoMap") or {}
    labels = _variant_labels(payload.get("SKU") or {})

    offers: list[Offer] = []
    sale_prices: list[Decimal] = []
    original_prices: list[Decimal] = []
    for sku_id, info in price_map.items():
        sale = parse_price(info.get("salePriceString"))
        original = (info.get("originalPrice") or {}).get("value")
        original_dec = Decimal(str(original)) if original is not None else None
        if sale is not None:
            sale_prices.append(sale)
        if original_dec is not None:
            original_prices.append(original_dec)
        offers.append(
            Offer(
                sku_id=str(sku_id),
                label=str(sku_id),
                price=sale,
                original_price=original_dec,
                currency=currency,
            )
        )

    stock_by_sku: dict[str, int] = {}
    for path in (payload.get("SKU") or {}).get("skuPaths") or []:
        sku_id = str(path.get("skuIdStr") or "")
        stock = path.get("skuStock")
        if sku_id and stock is not None:
            stock_by_sku[sku_id] = int(stock)
        attr = path.get("skuAttr")
        label = _sku_label(attr)
        if label:
            for offer in offers:
                if offer.sku_id == sku_id:
                    offer.label = label
                    offer.stock = stock_by_sku.get(sku_id)
                    break

    # A readable label beats the raw id when the skuAttr has no display name.
    if labels:
        for offer in offers:
            if offer.label == offer.sku_id:
                parts = [labels[k] for k in labels if k in offer.sku_id]
                if parts:
                    offer.label = " / ".join(parts)

    selected = str(price_module.get("selectedSkuId") or "")
    for offer in offers:
        if offer.sku_id == selected:
            detail.price = offer.price
            detail.original_price = offer.original_price
            break
    if detail.price is None and sale_prices:
        detail.price = min(sale_prices)
    if detail.original_price is None and original_prices:
        detail.original_price = max(original_prices)
    detail.min_price = min(sale_prices) if sale_prices else None
    detail.max_price = max(sale_prices) if sale_prices else None
    detail.offers = sorted(offers, key=lambda o: (o.price is None, o.price or Decimal(0)))

    rating = payload.get("PC_RATING") or {}
    try:
        detail.rating = float(rating.get("rating"))
    except (TypeError, ValueError):
        detail.rating = None
    detail.rating_count = rating.get("totalValidNum")
    detail.sold = rating.get("otherText")

    quantity = payload.get("QUANTITY_PC") or {}
    stock = quantity.get("totalAvailableInventory")
    detail.stock = int(stock) if stock is not None else None
    current = (quantity.get("currentSkuQuantityView") or {}).get("maxBuyCount")
    detail.quantity_limit = int(current) if current is not None else None

    shipping_module = payload.get("SHIPPING") or {}
    cost, free, estimate = _shipping_from_module(shipping_module)
    detail.shipping = cost
    detail.free_shipping = free
    detail.delivery_estimate = estimate

    store = payload.get("SHOP_CARD_PC") or {}
    detail.store = store.get("storeName")
    detail.store_url = store.get("storeHomePage")

    detail.coupons = _coupons_from_module(payload.get("COUPON_BLOCK_PC") or {})

    extend = payload.get("PRICE_EXTEND") or {}
    for view in extend.get("priceExtendViews") or []:
        content = view.get("content")
        if content and "tax" in content.lower():
            detail.tax_note = content
            break

    images = (payload.get("HEADER_IMAGE_PC") or {}).get("imagePathList") or []
    detail.images = [str(i) for i in images if i]

    return detail


def get_detail(
    product_id: str,
    http: AliHTTP | None = None,
    client: MtopClient | None = None,
) -> ProductDetail:
    """Fetch and parse the detail page for *product_id*."""
    http = http or AliHTTP()
    client = client or MtopClient(http)
    payload = client.product_detail(product_id)
    return build_detail(product_id, payload, currency=http.cfg.currency)
