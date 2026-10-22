"""Price and currency handling.

AliExpress renders prices in several shapes depending on locale and surface:
``$4.59``, ``US $4.59``, ``C$15.39``, ``€12,90``, ``4.59 - 9.99`` (ranges) and
``$1.62 each, >= 2 pieces`` (per-unit pricing).  Everything here works on
:class:`~decimal.Decimal` so budget arithmetic never drifts.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = {
    "US $": "USD",
    "US$": "USD",
    "CA$": "CAD",
    "C$": "CAD",
    "A$": "AUD",
    "AU$": "AUD",
    "NZ$": "NZD",
    "UK£": "GBP",
    "£": "GBP",
    "€": "EUR",
    "¥": "CNY",
    "₹": "INR",
    "R$": "BRL",
    "MX$": "MXN",
    "₽": "RUB",
    "₺": "TRY",
    "R": "ZAR",
    "$": "USD",
}

_NUMBER = r"\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_PRICE_RE = re.compile(rf"(?P<sym>[A-Z]{{0,3}}\$|£|€|¥|₹|₽|₺|R)?\s*(?P<num>{_NUMBER})")

_RANGE_SEPARATORS = (" - ", "–", "—", "~")


def detect_currency(text: str) -> str:
    """Return an ISO code for the currency symbol used in *text*."""
    if not text:
        return "USD"
    for symbol in ("US $", "US$", "CA$", "C$", "A$", "AU$", "NZ$", "UK£"):
        if symbol in text:
            return CURRENCY_SYMBOLS[symbol]
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return "USD"


def _to_decimal(raw: str) -> Decimal | None:
    """Normalise a captured number into a Decimal.

    Handles ``1,234.56`` and the European ``1.234,56`` convention, guessing
    by whichever separator appears last.
    """
    raw = raw.strip()
    if not raw:
        return None
    # Strip spaces used as thousands separators.
    raw = raw.replace("\u00a0", "").replace(" ", "")
    if "," in raw and "." in raw:
        # Whichever separator is last is the decimal point.
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        # A single comma is a decimal separator only when it is not followed by
        # exactly three digits (which would make it a thousands separator).
        tail = raw.rsplit(",", 1)[1]
        raw = raw.replace(",", "") if len(tail) == 3 else raw.replace(",", ".")
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def parse_price(text: str | None) -> Decimal | None:
    """Extract the leading price from *text*.

    ``"$4.59 - $9.99"`` returns ``Decimal("4.59")`` (the low end of a range).
    ``None`` is returned when no number is present.
    """
    if not text:
        return None
    cleaned = text.replace("US $", " ").replace("US$", " ")
    for sep in _RANGE_SEPARATORS:
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0]
    match = _PRICE_RE.search(cleaned)
    if not match:
        return None
    return _to_decimal(match.group("num"))


def parse_price_range(text: str | None) -> tuple[Decimal | None, Decimal | None]:
    """Return ``(low, high)`` for a price range, or ``(price, price)``."""
    if not text:
        return None, None
    parts = re.split(r"\s+(?:-|–|—|~)\s+", text.strip())
    values = [parse_price(part) for part in parts]
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return min(values), max(values)


def format_money(amount: Decimal | float | int | None, currency: str = "USD") -> str:
    """Render *amount* the way AliExpress does (``US $4.59``)."""
    if amount is None:
        return "-"
    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    symbol = {"USD": "US $", "CAD": "C$", "GBP": "£", "EUR": "€", "CNY": "¥"}.get(currency, "")
    if not symbol:
        return f"{value:.2f} {currency}"
    return f"{symbol}{value:.2f}"


def format_money_compact(amount: Decimal | float | int | None, currency: str = "USD") -> str:
    """Shorthand for table cells: ``$4.59`` instead of ``US $4.59``."""
    if amount is None:
        return "-"
    value = Decimal(str(amount)).quantize(Decimal("0.01"))
    symbol = {"USD": "$", "CAD": "C$", "GBP": "£", "EUR": "€", "CNY": "¥"}.get(currency, "")
    if not symbol:
        return f"{value:.2f} {currency}"
    return f"{symbol}{value:.2f}"
