"""Terminal rendering helpers built on ``rich``."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rich.console import Console
from rich.table import Table

from .budget import SpendEntry
from .models import Product, ProductDetail
from .money import format_money, format_money_compact


def make_console(json_mode: bool = False) -> Console:
    return Console(no_color=json_mode, soft_wrap=False)


def truncate(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def render_products(console: Console, products: list[Product], currency: str) -> None:
    if not products:
        console.print("[yellow]No products matched.[/yellow]")
        return

    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("#", justify="right", width=3, style="dim", no_wrap=True)
    table.add_column("price", justify="right", width=11, no_wrap=True)
    table.add_column("rating", justify="right", width=6, no_wrap=True)
    table.add_column("sold", width=9, style="dim", no_wrap=True)
    table.add_column("title", overflow="ellipsis", no_wrap=True)
    table.add_column("id", style="dim", width=16, no_wrap=True)

    # Column widths plus rich's default one-space padding on each side of
    # every cell (outer edges are excluded by pad_edge=False).
    fixed = 3 + 11 + 6 + 9 + 16 + 2 * 5
    title_width = max(12, console.width - fixed)
    table.columns[4].width = title_width
    table.columns[4].max_width = title_width

    for index, product in enumerate(products, start=1):
        price = format_money_compact(product.price, product.currency or currency)
        if product.original_price and product.price and product.discount:
            price += f" [dim]{product.discount}%[/dim]"
        rating = f"{product.rating:g}" if product.rating is not None else "-"
        sold = (product.sold or "").replace(" sold", "")
        table.add_row(
            str(index),
            price,
            rating,
            sold,
            product.title,
            product.product_id,
        )

    console.print(table)
    console.print(f"[dim]{len(products)} result(s)[/dim]")


def render_detail(console: Console, detail: ProductDetail, currency: str) -> None:
    console.print(f"[bold]{detail.title or '(untitled)'}[/bold]")
    console.print(f"[dim]{detail.url}[/dim]\n")

    if detail.partial:
        console.print(
            "[yellow]partial record[/yellow] [dim]- the product page was rate limited, "
            "so this comes from search results (no stock, shipping or variant prices)[/dim]\n"
        )
    price = format_money(detail.price, detail.currency or currency)
    if detail.original_price and detail.original_price != detail.price:
        price += (
            f"  [dim]{format_money(detail.original_price, detail.currency)}"
            f"  ({detail.discount_pct or 0}% off)[/dim]"
        )
    console.print(f"Price        {price}")

    if detail.min_price is not None and detail.max_price and detail.min_price != detail.max_price:
        console.print(
            f"Variants     {format_money(detail.min_price, detail.currency)}"
            f" – {format_money(detail.max_price, detail.currency)}"
        )
    if detail.rating is not None:
        reviews = f" ({detail.rating_count})" if detail.rating_count else ""
        console.print(f"Rating       {detail.rating:g}{reviews}")
    if detail.sold:
        console.print(f"Sold         {detail.sold}")
    if detail.stock is not None:
        console.print(f"Stock        {detail.stock}")
    if detail.quantity_limit:
        console.print(f"Limit        max {detail.quantity_limit} per order")
    if detail.free_shipping:
        console.print("Shipping     free")
    elif detail.shipping is not None:
        console.print(f"Shipping     {format_money(detail.shipping, detail.currency)}")
    if detail.delivery_estimate:
        console.print(f"Delivery     {detail.delivery_estimate}")
    if detail.store:
        console.print(f"Store        {detail.store}")
    if detail.tax_note:
        console.print(f"Tax          [dim]{detail.tax_note}[/dim]")
    if detail.note:
        console.print(f"Note         [dim]{detail.note}[/dim]")

    if detail.offers:
        console.print()
        table = Table(box=None, pad_edge=False, show_edge=False, title="Variants", title_justify="left")
        table.add_column("sku", style="dim", width=18)
        table.add_column("variant")
        table.add_column("price", justify="right", width=10)
        table.add_column("stock", justify="right", width=6)
        for offer in detail.offers:
            table.add_row(
                offer.sku_id,
                offer.label,
                format_money(offer.price, offer.currency or currency),
                str(offer.stock) if offer.stock is not None else "-",
            )
        console.print(table)

    if detail.coupons:
        console.print()
        for coupon in detail.coupons:
            console.print(f"Coupon       {coupon.title}")


def render_budget(console: Console, status: dict, cap: Decimal, spent: Decimal, remaining: Decimal) -> None:
    currency = status.get("currency", "USD")
    console.print(f"[bold]Budget {status['month']}[/bold]")
    console.print(f"  cap       {format_money(cap, currency)}")
    console.print(f"  spent     {format_money(spent, currency)}")
    colour = "green" if remaining >= 0 else "red"
    console.print(f"  remaining [{colour}]{format_money(remaining, currency)}[/{colour}]")

    entries: list[SpendEntry] = status.get("_entries") or []
    if not entries:
        console.print("\n[dim]Nothing recorded this month.[/dim]")
        return

    console.print()
    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("id", justify="right", width=4, style="dim")
    table.add_column("when", width=20, style="dim")
    table.add_column("product", overflow="fold")
    table.add_column("qty", justify="right", width=4)
    table.add_column("total", justify="right", width=10)
    for entry in entries:
        table.add_row(
            str(entry.id),
            entry.ts[:16].replace("T", " "),
            truncate(entry.title or entry.product_id, 48),
            str(entry.qty),
            format_money(entry.total, entry.currency),
        )
    console.print(table)


def render_watch(console: Console, entries: list[Any], currency: str) -> None:
    """Render watch entries.  Accepts :class:`WatchEntry` objects or dicts."""
    if not entries:
        console.print("[yellow]Watch list is empty.[/yellow]")
        return

    def field(entry: Any, name: str, default: Any = None) -> Any:
        if isinstance(entry, dict):
            return entry.get(name, default)
        return getattr(entry, name, default)

    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("product", overflow="fold")
    table.add_column("price", justify="right", width=10)
    table.add_column("low", justify="right", width=10)
    table.add_column("high", justify="right", width=10)
    table.add_column("checked", style="dim", width=18)
    for entry in entries:
        checked = field(entry, "checked") or field(entry, "checked_ts") or "never"
        table.add_row(
            truncate(field(entry, "title") or field(entry, "product_id", ""), 52),
            format_money_compact(field(entry, "price"), field(entry, "currency") or currency),
            format_money_compact(field(entry, "low") or field(entry, "min_price"), currency),
            format_money_compact(field(entry, "high") or field(entry, "max_price"), currency),
            str(checked)[:16].replace("T", " "),
        )
    console.print(table)


def render_plan(console: Console, rows: list[dict], budget: Decimal, currency: str) -> None:
    if not rows:
        console.print("[yellow]Nothing fits inside the remaining budget.[/yellow]")
        return
    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("#", justify="right", width=4, style="dim")
    table.add_column("item", overflow="fold")
    table.add_column("unit", justify="right", width=9)
    table.add_column("ship", justify="right", width=9)
    table.add_column("total", justify="right", width=10)

    total = Decimal("0")
    for index, row in enumerate(rows, start=1):
        line_total = Decimal(str(row["total"]))
        total += line_total
        table.add_row(
            str(index),
            truncate(row.get("title") or row["product_id"], 46),
            format_money(row.get("unit_price"), currency),
            format_money(row.get("shipping"), currency),
            format_money(line_total, currency),
        )
    console.print(table)
    console.print(f"[bold]total {format_money(total, currency)}[/bold] of {format_money(budget, currency)}")
