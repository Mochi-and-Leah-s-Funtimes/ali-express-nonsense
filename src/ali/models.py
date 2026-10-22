"""Data models shared across the CLI."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from .money import parse_price


@dataclass
class Product:
    """A single search result."""

    product_id: str
    title: str
    url: str
    price: Decimal | None = None
    original_price: Decimal | None = None
    currency: str = "USD"
    discount_pct: int | None = None
    rating: float | None = None
    rating_count: int | None = None
    sold: str | None = None
    image: str | None = None
    shipping: Decimal | None = None
    free_shipping: bool = False
    store: str | None = None
    source: str = "http"

    @property
    def discount(self) -> int | None:
        if self.discount_pct is not None:
            return self.discount_pct
        if self.original_price and self.price and self.original_price > 0:
            return int(round((1 - self.price / self.original_price) * 100))
        return None

    @property
    def landed_price(self) -> Decimal | None:
        """Item price plus shipping, the number that actually matters for a budget."""
        if self.price is None:
            return None
        if self.free_shipping or self.shipping is None:
            return self.price
        return self.price + self.shipping

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("price", "original_price", "shipping", "landed_price"):
            value = data.get(key)
            data[key] = float(value) if isinstance(value, Decimal) else value
        return data


@dataclass
class Offer:
    """A purchasable variant (SKU) of a product."""

    sku_id: str
    label: str
    price: Decimal | None = None
    original_price: Decimal | None = None
    currency: str = "USD"
    stock: int | None = None
    image: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("price", "original_price"):
            value = data.get(key)
            data[key] = float(value) if isinstance(value, Decimal) else value
        return data


@dataclass
class Coupon:
    code: str | None = None
    title: str = ""
    discount_value: Decimal | None = None
    discount_type: str | None = None
    expires: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(data.get("discount_value"), Decimal):
            data["discount_value"] = float(data["discount_value"])
        return data


@dataclass
class ProductDetail:
    """Everything the PDP exposes for one product."""

    product_id: str
    title: str = ""
    url: str = ""
    currency: str = "USD"
    price: Decimal | None = None
    original_price: Decimal | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    rating: float | None = None
    rating_count: int | None = None
    sold: str | None = None
    stock: int | None = None
    store: str | None = None
    store_url: str | None = None
    shipping: Decimal | None = None
    free_shipping: bool = False
    delivery_estimate: str | None = None
    images: list[str] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)
    coupons: list[Coupon] = field(default_factory=list)
    tax_note: str | None = None
    quantity_limit: int | None = None
    note: str | None = None
    """Free-form status note, e.g. why the record is only partial."""
    partial: bool = False
    """True when the data came from search results rather than the product page.

    Search results carry price, rating and seller activity but not stock,
    shipping or per-variant pricing, so a partial record is good enough for
    budgeting but not for ordering.
    """

    @property
    def discount_pct(self) -> int | None:
        """Discount percentage derived from the struck-through price."""
        if self.original_price and self.price and self.original_price > 0:
            return int(round((1 - self.price / self.original_price) * 100))
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "url": self.url,
            "currency": self.currency,
            "price": _dec(self.price),
            "original_price": _dec(self.original_price),
            "min_price": _dec(self.min_price),
            "max_price": _dec(self.max_price),
            "rating": self.rating,
            "rating_count": self.rating_count,
            "sold": self.sold,
            "stock": self.stock,
            "store": self.store,
            "store_url": self.store_url,
            "shipping": _dec(self.shipping),
            "free_shipping": self.free_shipping,
            "delivery_estimate": self.delivery_estimate,
            "images": self.images,
            "offers": [o.to_dict() for o in self.offers],
            "coupons": [c.to_dict() for c in self.coupons],
            "tax_note": self.tax_note,
            "quantity_limit": self.quantity_limit,
            "discount_pct": self.discount_pct,
            "partial": self.partial,
            "note": self.note,
        }


def _dec(value: Decimal | None) -> float | None:
    return float(value) if isinstance(value, Decimal) else value


def rating_from_text(text: str | None) -> float | None:
    """Parse ``"4.8"`` out of a rating element."""
    if not text:
        return None
    match = re.search(r"\d(?:\.\d)?", text)
    return float(match.group(0)) if match else None


def sold_count(text: str | None) -> int | None:
    """Parse ``"10,000+ sold"`` into ``10000``."""
    if not text:
        return None
    match = re.search(r"([\d.,]+)\s*\+?\s*sold", text.lower())
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def discount_from_text(text: str | None) -> int | None:
    """Parse ``"-71%"`` into ``71``."""
    if not text:
        return None
    match = re.search(r"(\d+)\s*%", text)
    return int(match.group(1)) if match else None


def price_from_text(text: str | None) -> Decimal | None:
    return parse_price(text)
