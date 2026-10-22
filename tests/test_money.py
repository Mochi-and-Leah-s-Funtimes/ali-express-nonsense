from decimal import Decimal

import pytest

from ali.money import detect_currency, format_money, format_money_compact, parse_price, parse_price_range


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$4.59", Decimal("4.59")),
        ("US $4.59", Decimal("4.59")),
        ("C$15.39", Decimal("15.39")),
        ("$1,234.56", Decimal("1234.56")),
        ("1.234,56", Decimal("1234.56")),
        ("€12,90", Decimal("12.90")),
        ("¥88", Decimal("88")),
        ("$4.59 - $9.99", Decimal("4.59")),
        ("$1.62 each, >= 2 pieces", Decimal("1.62")),
        ("US $18.39", Decimal("18.39")),
        ("", None),
        (None, None),
        ("no price here", None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_parse_price_range():
    assert parse_price_range("US $4.59 - US $9.99") == (Decimal("4.59"), Decimal("9.99"))
    assert parse_price_range("$3.00") == (Decimal("3.00"), Decimal("3.00"))
    assert parse_price_range("") == (None, None)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("US $4.59", "USD"),
        ("$4.59", "USD"),
        ("C$15.39", "CAD"),
        ("CA$3.00", "CAD"),
        ("£9.99", "GBP"),
        ("€5.00", "EUR"),
        ("", "USD"),
    ],
)
def test_detect_currency(text, expected):
    assert detect_currency(text) == expected


def test_format_money():
    assert format_money(Decimal("4.5"), "USD") == "US $4.50"
    assert format_money(Decimal("4.5"), "CAD") == "C$4.50"
    assert format_money(None) == "-"
    assert format_money_compact(Decimal("4.5"), "USD") == "$4.50"
