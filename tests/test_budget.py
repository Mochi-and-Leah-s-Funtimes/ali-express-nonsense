import json
from decimal import Decimal

import pytest

from ali.budget import BudgetStore, month_key, plan_combination
from ali.detail import build_detail


@pytest.fixture()
def store(tmp_path):
    with BudgetStore(tmp_path / "ali.db") as instance:
        yield instance


def test_cap_roundtrip(store):
    assert store.get_cap() == Decimal("150")
    store.set_cap(Decimal("42.5"))
    assert store.get_cap() == Decimal("42.5")


def test_record_and_status(store):
    store.set_cap(Decimal("150"))
    store.record("1005009066568188", title="Hub", qty=2, unit_price=Decimal("4.46"), shipping=Decimal("1.54"))
    status = store.status()
    assert status["spent"] == 10.46
    assert status["remaining"] == pytest.approx(150 - 10.46)
    assert len(status["entries"]) == 1
    assert status["entries"][0]["qty"] == 2


def test_remove_entry(store):
    entry = store.record("1", title="x", qty=1, unit_price=Decimal("5"))
    assert store.spent() == Decimal("5")
    assert store.remove(entry.id) is True
    assert store.spent() == Decimal("0")
    assert store.remove(9999) is False


def test_watch_list(store):
    store.watch_add("1005009066568188", title="Hub", price=Decimal("4.46"))
    entries = store.watch_list()
    assert len(entries) == 1
    assert entries[0].price == Decimal("4.46")

    store.watch_update_price("1005009066568188", Decimal("3.00"))
    entry = store.watch_get("1005009066568188")
    assert entry.price == Decimal("3.00")
    assert entry.min_price == Decimal("3.00")
    assert entry.max_price == Decimal("4.46")

    store.watch_update_price("1005009066568188", Decimal("9.99"))
    entry = store.watch_get("1005009066568188")
    assert entry.max_price == Decimal("9.99")

    assert store.watch_remove("1005009066568188") is True
    assert store.watch_list() == []


def test_cache(store):
    store.cache_set("detail", "123", {"a": 1})
    assert store.cache_get("detail", "123") == {"a": 1}
    assert store.cache_get("detail", "123", max_age_seconds=3600) == {"a": 1}
    assert store.cache_get("detail", "123", max_age_seconds=0) is None
    assert store.cache_get("detail", "999") is None


def test_month_key():
    assert month_key("2026-10-22T01:00:00+00:00") == "2026-10"
    assert len(month_key()) == 7


def test_plan_combination():
    budget = Decimal("100")
    candidates = [("a", Decimal("30")), ("b", Decimal("40")), ("c", Decimal("45")), ("d", Decimal("200"))]
    chosen = plan_combination(budget, candidates, max_items=3)
    assert chosen == [("a", Decimal("30")), ("b", Decimal("40"))]
    assert sum(price for _, price in chosen) <= budget


PDP_PAYLOAD = {
    "PRODUCT_TITLE": {"text": "USB C Hub"},
    "PRICE": {
        "selectedSkuId": 12000047797229526,
        "skuIdStrPriceInfoMap": {
            "12000047797229526": {
                "salePriceString": "US $4.46",
                "originalPrice": {"currency": "USD", "value": 18.39},
            },
            "12000047797229528": {
                "salePriceString": "US $5.40",
                "originalPrice": {"currency": "USD", "value": 20.44},
            },
        },
    },
    "PC_RATING": {"rating": "4.8", "totalValidNum": 2149, "otherText": "10,000+ sold"},
    "QUANTITY_PC": {"totalAvailableInventory": 72, "currentSkuQuantityView": {"maxBuyCount": 1}},
    "SKU": {
        "skuPaths": [
            {"skuIdStr": "12000047797229526", "skuAttr": "14:504#5-in-1", "skuStock": 21},
            {"skuIdStr": "12000047797229528", "skuAttr": "14:865#7-in-1", "skuStock": 29},
        ]
    },
    "SHIPPING": {
        "deliveryLayoutInfo": [
            {
                "titleLayout": [[{"content": "<strong>Free shipping</strong>"}]],
                "contentLayout": [[{"content": "<strong>Delivery: Oct 29 - Nov 03</strong>"}]],
                "bizData": {"formattedAmount": "US $2.18"},
            }
        ]
    },
    "SHOP_CARD_PC": {"storeName": "Shop1104759008 Store", "storeHomePage": "https://example.com/store"},
    "COUPON_BLOCK_PC": {"upperCouponBlockViewList": []},
    "PRICE_EXTEND": {
        "priceExtendViews": [{"content": "Tax excluded, add at checkout if applicable", "type": "tax"}]
    },
    "HEADER_IMAGE_PC": {"imagePathList": ["https://img/1.jpg", "https://img/2.jpg"]},
}


def test_build_detail():
    detail = build_detail("1005009066568188", PDP_PAYLOAD)
    assert detail.title == "USB C Hub"
    assert detail.price == Decimal("4.46")
    assert detail.original_price == Decimal("18.39")
    assert detail.discount_pct == 76
    assert detail.min_price == Decimal("4.46")
    assert detail.max_price == Decimal("5.40")
    assert detail.rating == 4.8
    assert detail.rating_count == 2149
    assert detail.sold == "10,000+ sold"
    assert detail.stock == 72
    assert detail.quantity_limit == 1
    assert detail.free_shipping is True
    assert detail.shipping is None
    assert detail.delivery_estimate == "Delivery: Oct 29 - Nov 03"
    assert detail.store == "Shop1104759008 Store"
    assert detail.tax_note and "Tax excluded" in detail.tax_note
    assert len(detail.images) == 2
    assert len(detail.offers) == 2
    assert detail.offers[0].label == "5-in-1"
    assert detail.offers[0].stock == 21
    assert detail.offers[0].price == Decimal("4.46")


def test_build_detail_empty():
    detail = build_detail("1", {})
    assert detail.title == ""
    assert detail.price is None
    assert detail.partial is False
    assert json.dumps(detail.to_dict())
