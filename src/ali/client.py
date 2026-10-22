"""High level client used by the CLI.

Manages the two transports and the browser lifecycle so callers only deal with
:meth:`AliClient.search` and :meth:`AliClient.detail`.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from .budget import BudgetStore
from .config import Config
from .detail import build_detail
from .models import Product, ProductDetail
from .mtop import MtopClient
from .search import filter_products, parse_cards, sort_products
from .transport import AliBrowser, AliHTTP, BlockedError


def _dec(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


class AliClient:
    """Search and product lookup with automatic transport fallbacks."""

    def __init__(self, cfg: Config | None = None, browser: str = "auto") -> None:
        """*browser* is one of ``auto``, ``always`` or ``never``."""
        self.cfg = cfg or Config.load()
        self.cfg.ensure_dirs()
        self.browser_mode = browser
        self.http = AliHTTP(self.cfg)
        self._browser: AliBrowser | None = None
        self._mtop: MtopClient | None = None
        self._budget_store: BudgetStore | None = None
        self._last_detail = 0.0
        self.last_total = 0
        self.last_products: list[Product] = []

    # -- lifecycle -----------------------------------------------------
    def __enter__(self) -> AliClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.__exit__()
            self._browser = None
        if self._budget_store is not None:
            self._budget_store.close()
            self._budget_store = None

    @property
    def browser(self) -> AliBrowser | None:
        if self.browser_mode == "never":
            return None
        if self._browser is None:
            self._browser = AliBrowser(self.cfg)
            self._browser.__enter__()
        return self._browser

    @property
    def mtop(self) -> MtopClient:
        if self._mtop is None:
            self._mtop = MtopClient(self.http, self.browser if self.browser_mode != "never" else None)
        return self._mtop

    # -- api -----------------------------------------------------------
    def search(
        self,
        query: str,
        page: int = 1,
        pages: int = 1,
        max_price: Decimal | None = None,
        min_price: Decimal | None = None,
        min_rating: float | None = None,
        sort: str = "price",
    ) -> list[Product]:
        """Search AliExpress and return filtered, sorted products.

        The number of results seen *before* filtering is recorded on
        :attr:`last_total` so the CLI can explain an empty result set.
        """
        products: list[Product] = []
        seen: set[str] = set()

        for offset in range(pages):
            current = page + offset
            url = self.http.search_url(query, current)
            found = self._fetch_cards(url)
            for product in found:
                if product.product_id in seen:
                    continue
                seen.add(product.product_id)
                products.append(product)

        self.last_total = len(products)
        self.last_products = sort_products(list(products), sort)
        self._cache_products(self.last_products)
        products = filter_products(products, max_price, min_price, min_rating)
        return sort_products(products, sort)

    def _cache_products(self, products: list[Product]) -> None:
        """Remember what the search index knows about each product.

        The product page itself is aggressively rate-limited, so a cached
        search result is the fallback when a detail lookup is refused.
        """
        store = self._store()
        if store is None:
            return
        for product in products:
            store.cache_set("search_product", product.product_id, product.to_dict())

    def _fetch_cards(self, url: str) -> list[Product]:
        try:
            return parse_cards(self.http.get_text(url))
        except BlockedError:
            browser = self.browser
            if browser is None:
                raise
            result = browser.fetch(url, wait_ms=7000, scroll=3)
            return parse_cards(result.html)

    def detail(self, product_id: str, refresh: bool = False, max_age: int = 3600) -> ProductDetail:
        """Fetch the structured detail page for *product_id*.

        Product details come from an endpoint that is aggressively
        rate-limited, so successful lookups are cached.  Within *max_age*
        seconds a cached copy is reused; pass ``refresh=True`` to force a
        fresh fetch.

        When AliExpress refuses the lookup, whatever the search index already
        told us about the product is returned instead, flagged as
        :attr:`ProductDetail.partial`.
        """
        store = self._store()
        if store is not None and not refresh:
            cached = store.cache_get("detail", product_id, max_age)
            if cached:
                return build_detail(product_id, cached, currency=self.cfg.currency)

        self._pace()
        try:
            payload = self.mtop.product_detail(product_id)
        except BlockedError:
            fallback = self._partial_detail(product_id)
            if fallback is not None:
                return fallback
            raise
        if store is not None and payload:
            store.cache_set("detail", product_id, payload)
        return build_detail(product_id, payload, currency=self.cfg.currency)

    def _partial_detail(self, product_id: str) -> ProductDetail | None:
        """Build a detail record from cached search results."""
        store = self._store()
        if store is None:
            return None
        cached = store.cache_get("search_product", product_id)
        if not cached:
            return None
        detail = ProductDetail(
            product_id=str(product_id),
            title=cached.get("title", ""),
            url=cached.get("url", f"https://www.aliexpress.com/item/{product_id}.html"),
            currency=cached.get("currency", self.cfg.currency),
            price=_dec(cached.get("price")),
            original_price=_dec(cached.get("original_price")),
            rating=cached.get("rating"),
            sold=cached.get("sold"),
            partial=True,
            note="product page unavailable (rate limited); figures come from search results",
        )
        detail.images = [cached["image"]] if cached.get("image") else []
        return detail

    def _pace(self) -> None:
        """Keep a polite minimum interval between detail lookups."""
        gap = max(self.cfg.delay, 2.0)
        elapsed = time.monotonic() - self._last_detail
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_detail = time.monotonic()

    def _store(self) -> BudgetStore | None:
        if self._budget_store is None:
            try:
                self._budget_store = BudgetStore(self.cfg.db_path)
            except Exception:
                return None
        return self._budget_store

    def detail_many(self, product_ids: list[str], refresh: bool = False) -> list[ProductDetail]:
        details: list[ProductDetail] = []
        for product_id in product_ids:
            try:
                details.append(self.detail(product_id, refresh=refresh))
            except BlockedError as exc:
                details.append(
                    ProductDetail(
                        product_id=product_id,
                        url=self.http.item_url(product_id),
                        currency=self.cfg.currency,
                        tax_note=str(exc),
                    )
                )
        return details

    def stats(self) -> dict[str, Any]:
        return {"currency": self.cfg.currency, "locale": self.cfg.locale}
