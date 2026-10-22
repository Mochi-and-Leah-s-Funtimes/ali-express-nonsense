"""Command line interface.

ali search "usb c hub" --max-price 20
ali product 1005009066568188
ali budget
ali plan "servo motor" --qty 2
ali watch 1005009066568188
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

import click

from .budget import BudgetStore, plan_combination
from .client import AliClient
from .config import Config
from .money import format_money
from .render import (
    make_console,
    render_budget,
    render_detail,
    render_plan,
    render_products,
    render_watch,
)
from .search import price_bounds
from .transport import BlockedError

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace("$", "").strip())
    except (InvalidOperation, ValueError):
        raise click.BadParameter(f"not a number: {value}") from None


def _emit_json(console: Any, payload: Any) -> None:
    click.echo(json.dumps(payload, indent=2, default=str))


def _client(ctx: click.Context) -> AliClient:
    client = ctx.obj.get("client")
    if client is None:
        client = AliClient(ctx.obj["config"])
        ctx.obj["client"] = client
        ctx.call_on_close(client.close)
    return client


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--currency", default=None, help="Display currency (default USD).")
@click.option("--locale", default=None, help="Locale, e.g. en_US.")
@click.option(
    "--browser",
    type=click.Choice(["auto", "always", "never"]),
    default="auto",
    help="When to use the real browser transport.",
)
@click.option("--data-dir", default=None, help="Where to keep the ledger and browser profile.")
@click.pass_context
def main(
    ctx: click.Context,
    currency: str | None,
    locale: str | None,
    browser: str,
    data_dir: str | None,
) -> None:
    """Browse AliExpress from the command line, within a monthly budget."""
    cfg = Config.load()
    if currency:
        cfg.currency = currency.upper()
    if locale:
        cfg.locale = locale
    if data_dir:
        cfg.data_dir = data_dir
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.obj = {"config": cfg, "client": None, "browser": browser, "json": False}


@main.command()
@click.argument("query", nargs=-1, required=True)
@click.option("--page", default=1, show_default=True, help="First result page to fetch.")
@click.option("--pages", default=1, show_default=True, help="How many result pages to fetch.")
@click.option("--max-price", default=None, help="Only show items at or below this price.")
@click.option("--min-price", default=None, help="Only show items at or above this price.")
@click.option("--min-rating", default=None, type=float, help="Only show items rated at least this high.")
@click.option(
    "--sort", default="price", type=click.Choice(["price", "-price", "rating", "discount"]), show_default=True
)
@click.option("--limit", default=25, show_default=True, help="Maximum rows to print.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for machine consumption.")
@click.pass_context
def search(
    ctx: click.Context,
    query: tuple[str, ...],
    page: int,
    pages: int,
    max_price: str | None,
    min_price: str | None,
    min_rating: float | None,
    sort: str,
    limit: int,
    as_json: bool,
) -> None:
    """Search AliExpress for QUERY."""
    console = make_console(as_json)
    client = _client(ctx)
    products = client.search(
        " ".join(query),
        page=page,
        pages=pages,
        max_price=_decimal(max_price),
        min_price=_decimal(min_price),
        min_rating=min_rating,
        sort=sort,
    )

    if as_json:
        _emit_json(console, [p.to_dict() for p in products[:limit]])
        return
    if not products:
        found = client.last_total
        if found:
            low, high = price_bounds(client.last_products)
            console.print(
                f"[yellow]No products matched the filter[/yellow] "
                f"[dim]({found} result(s) ranged from {format_money(low, client.cfg.currency)} "
                f"to {format_money(high, client.cfg.currency)})[/dim]"
            )
        else:
            console.print("[yellow]No products found.[/yellow]")
        return
    render_products(console, products[:limit], client.cfg.currency)


@main.command()
@click.argument("product_id")
@click.option("--refresh", is_flag=True, help="Ignore the cache and re-fetch the product page.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for machine consumption.")
@click.pass_context
def product(ctx: click.Context, product_id: str, refresh: bool, as_json: bool) -> None:
    """Show the detail page for PRODUCT_ID."""
    console = make_console(as_json)
    client = _client(ctx)
    try:
        detail = client.detail(product_id, refresh=refresh)
    except BlockedError as exc:
        if as_json:
            _emit_json(console, {"product_id": product_id, "error": str(exc)})
            return
        console.print(f"[red]Could not fetch the product page.[/red] [dim]{exc}[/dim]")
        console.print(
            "[dim]AliExpress rate-limits this endpoint. Wait a few minutes and retry; "
            "cached details are reused automatically.[/dim]"
        )
        raise SystemExit(2) from None
    if as_json:
        _emit_json(console, detail.to_dict())
        return
    render_detail(console, detail, client.cfg.currency)


@main.command()
@click.option("--month", default=None, help="Budget month (YYYY-MM), defaults to the current one.")
@click.option("--set", "set_cap", default=None, help="Set the monthly cap instead of showing status.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for machine consumption.")
@click.pass_context
def budget(ctx: click.Context, month: str | None, set_cap: str | None, as_json: bool) -> None:
    """Show or set the monthly shopping budget."""
    console = make_console(as_json)
    cfg = ctx.obj["config"]
    with BudgetStore(cfg.db_path) as store:
        if set_cap is not None:
            amount = _decimal(set_cap)
            if amount is None:
                raise click.BadParameter("not a number")
            store.set_cap(amount)
            cfg.monthly_budget = float(amount)
            cfg.save()
            if as_json:
                _emit_json(console, {"monthly_cap": float(amount)})
            else:
                console.print(f"Monthly budget set to {format_money(amount, cfg.currency)}")
            return

        status = store.status(month, default_cap=Decimal(str(cfg.monthly_budget)))
        if as_json:
            _emit_json(console, status)
            return
        cap = Decimal(str(status["cap"]))
        spent = Decimal(str(status["spent"]))
        remaining = Decimal(str(status["remaining"]))
        status["_entries"] = store.entries(month)
        render_budget(console, status, cap, spent, remaining)


@main.group()
@click.pass_context
def spend(ctx: click.Context) -> None:
    """Record what the robot intends to buy."""


@spend.command("add")
@click.argument("product_id")
@click.option("--title", default="", help="Product title, for your own records.")
@click.option("--qty", default=1, show_default=True, help="How many units.")
@click.option("--price", default=None, help="Unit price; looked up when omitted.")
@click.option("--shipping", default="0", show_default=True, help="Shipping cost.")
@click.option("--note", default="", help="Free-form note.")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def spend_add(
    ctx: click.Context,
    product_id: str,
    title: str,
    qty: int,
    price: str | None,
    shipping: str,
    note: str,
    as_json: bool,
) -> None:
    """Record PRODUCT_ID against the monthly budget."""
    console = make_console(as_json)
    cfg = ctx.obj["config"]
    unit = _decimal(price)
    ship = _decimal(shipping) or Decimal("0")

    if unit is None:
        client = _client(ctx)
        try:
            detail = client.detail(product_id)
        except BlockedError as exc:
            raise click.ClickException(
                f"could not look up the price ({exc}). Pass --price to record it manually."
            ) from exc
        unit = detail.price or Decimal("0")
        title = title or detail.title
        if not shipping or ship == 0:
            if detail.free_shipping:
                ship = Decimal("0")
            elif detail.shipping is not None:
                ship = detail.shipping
        if detail.quantity_limit and qty > detail.quantity_limit:
            console.print(f"[yellow]warning: store limits this to {detail.quantity_limit} per order[/yellow]")

    with BudgetStore(cfg.db_path) as store:
        entry = store.record(
            product_id,
            title=title,
            qty=qty,
            unit_price=unit,
            shipping=ship,
            currency=cfg.currency,
            note=note,
        )
        status = store.status(default_cap=Decimal(str(cfg.monthly_budget)))
    if as_json:
        _emit_json(console, {"entry": entry.to_dict(), "budget": status})
    else:
        console.print(f"Recorded {entry.product_id} x{entry.qty} = {format_money(entry.total, cfg.currency)}")
        console.print(
            f"[dim]remaining {format_money(Decimal(str(status['remaining'])), cfg.currency)}"
            f" of {format_money(Decimal(str(status['cap'])), cfg.currency)}[/dim]"
        )


@spend.command("list")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def spend_list(ctx: click.Context, as_json: bool) -> None:
    """List recorded purchases for the current month."""
    console = make_console(as_json)
    cfg = ctx.obj["config"]
    with BudgetStore(cfg.db_path) as store:
        status = store.status(default_cap=Decimal(str(cfg.monthly_budget)))
    if as_json:
        _emit_json(console, status)
        return
    cap = Decimal(str(status["cap"]))
    spent = Decimal(str(status["spent"]))
    remaining = Decimal(str(status["remaining"]))
    status["_entries"] = store.entries()
    render_budget(console, status, cap, spent, remaining)


@spend.command("remove")
@click.argument("entry_id", type=int)
@click.pass_context
def spend_remove(ctx: click.Context, entry_id: int) -> None:
    """Remove ledger entry ENTRY_ID."""
    cfg = ctx.obj["config"]
    with BudgetStore(cfg.db_path) as store:
        removed = store.remove(entry_id)
    click.echo("removed" if removed else "no such entry")


@main.group()
@click.pass_context
def watch(ctx: click.Context) -> None:
    """Track prices of products you are interested in."""


@watch.command("add")
@click.argument("product_id")
@click.option("--title", default="")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def watch_add(ctx: click.Context, product_id: str, title: str, as_json: bool) -> None:
    """Add PRODUCT_ID to the watch list."""
    console = make_console(as_json)
    cfg = ctx.obj["config"]
    price = None
    if not title:
        client = _client(ctx)
        detail = client.detail(product_id)
        title = detail.title
        price = detail.price
    with BudgetStore(cfg.db_path) as store:
        entry = store.watch_add(
            product_id,
            title=title,
            url=f"https://www.aliexpress.com/item/{product_id}.html",
            price=price,
            currency=cfg.currency,
        )
    if as_json:
        _emit_json(console, entry.to_dict())
    else:
        console.print(f"Watching {entry.product_id} — {entry.title}")


@watch.command("list")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def watch_list(ctx: click.Context, as_json: bool) -> None:
    """Show the watch list."""
    console = make_console(as_json)
    cfg = ctx.obj["config"]
    with BudgetStore(cfg.db_path) as store:
        entries = store.watch_list()
    if as_json:
        _emit_json(console, [e.to_dict() for e in entries])
        return
    render_watch(console, entries, cfg.currency)


@watch.command("check")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def watch_check(ctx: click.Context, as_json: bool) -> None:
    """Re-check prices of everything on the watch list."""
    console = make_console(as_json)
    cfg = ctx.obj["config"]
    client = _client(ctx)
    with BudgetStore(cfg.db_path) as store:
        entries = store.watch_list()
        results = []
        for entry in entries:
            price = None
            title = entry.title
            error = None
            try:
                detail = client.detail(entry.product_id)
                price = detail.price
                title = detail.title or title
            except Exception as exc:  # noqa: BLE001 - report and continue
                error = str(exc)
            store.watch_update_price(entry.product_id, price)
            updated = store.watch_get(entry.product_id)
            results.append(
                {
                    "product_id": entry.product_id,
                    "title": title,
                    "price": float(price) if price is not None else None,
                    "currency": entry.currency,
                    "low": float(updated.min_price) if updated and updated.min_price else None,
                    "high": float(updated.max_price) if updated and updated.max_price else None,
                    "checked": (updated.checked_ts if updated else None),
                    "error": error,
                }
            )
    if as_json:
        _emit_json(console, results)
        return
    render_watch(console, results, cfg.currency)
    for row in results:
        if row["error"]:
            console.print(f"[yellow]{row['product_id']}: {row['error']}[/yellow]")


@watch.command("remove")
@click.argument("product_id")
@click.pass_context
def watch_remove(ctx: click.Context, product_id: str) -> None:
    """Remove PRODUCT_ID from the watch list."""
    cfg = ctx.obj["config"]
    with BudgetStore(cfg.db_path) as store:
        removed = store.watch_remove(product_id)
    click.echo("removed" if removed else "not on the watch list")


@main.command()
@click.argument("query", nargs=-1, required=True)
@click.option("--qty", default=1, show_default=True, help="Units to plan for.")
@click.option("--pages", default=2, show_default=True, help="How many result pages to consider.")
@click.option("--max-items", default=5, show_default=True, help="Maximum items in the plan.")
@click.option(
    "--with-shipping",
    is_flag=True,
    help="Look up each candidate's shipping cost (slower, needs the browser).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON for machine consumption.")
@click.pass_context
def plan(
    ctx: click.Context,
    query: tuple[str, ...],
    qty: int,
    pages: int,
    max_items: int,
    with_shipping: bool,
    as_json: bool,
) -> None:
    """Build a shopping plan that fits the remaining monthly budget."""
    console = make_console(as_json)
    cfg = ctx.obj["config"]
    client = _client(ctx)

    with BudgetStore(cfg.db_path) as store:
        status = store.status(default_cap=Decimal(str(cfg.monthly_budget)))
    budget_left = Decimal(str(status["remaining"]))
    if budget_left <= 0:
        message = {
            "error": "budget exhausted",
            "month": status["month"],
            "cap": status["cap"],
            "spent": status["spent"],
            "remaining": status["remaining"],
        }
        if as_json:
            _emit_json(console, message)
        else:
            console.print(
                f"[red]Monthly budget of {format_money(Decimal(str(status['cap'])), cfg.currency)} "
                f"is already committed.[/red]"
            )
        return

    products = client.search(" ".join(query), pages=pages, sort="price")
    candidates = [
        (p.product_id, (p.price or Decimal("0")) * qty)
        for p in products
        if p.price is not None and p.price * qty <= budget_left
    ]
    chosen = plan_combination(budget_left, candidates, max_items=max_items)

    rows: list[dict[str, Any]] = []
    by_id = {p.product_id: p for p in products}
    for product_id, estimate in chosen:
        product = by_id[product_id]
        row = {
            "product_id": product_id,
            "title": product.title,
            "url": product.url,
            "qty": qty,
            "unit_price": float(product.price or 0),
            "shipping": 0.0,
            "total": float(estimate),
            "rating": product.rating,
            "shipping_known": False,
        }
        if with_shipping:
            try:
                detail = client.detail(product_id)
                shipping = Decimal("0") if detail.free_shipping else (detail.shipping or Decimal("0"))
                row["shipping"] = float(shipping)
                row["shipping_known"] = True
                row["total"] = float((product.price or Decimal("0")) * qty + shipping)
            except Exception:  # noqa: BLE001 - fall back to the estimate
                pass
        rows.append(row)

    total = sum(Decimal(str(row["total"])) for row in rows)
    payload = {
        "query": " ".join(query),
        "month": status["month"],
        "budget_remaining": float(budget_left),
        "planned_total": float(total),
        "fits": total <= budget_left,
        "items": rows,
    }
    if as_json:
        _emit_json(console, payload)
        return
    render_plan(console, rows, budget_left, cfg.currency)
    if not with_shipping:
        console.print("[dim]shipping not included — pass --with-shipping to look it up[/dim]")


@main.command()
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def config(ctx: click.Context, as_json: bool) -> None:
    """Show the current configuration."""
    console = make_console(as_json)
    cfg = ctx.obj["config"]
    payload = {
        "currency": cfg.currency,
        "locale": cfg.locale,
        "region": cfg.region or "(global)",
        "data_dir": str(cfg.data_dir),
        "monthly_budget": cfg.monthly_budget,
    }
    if as_json:
        _emit_json(console, payload)
        return
    for key, value in payload.items():
        console.print(f"{key:16}{value}")


def _cleanup(ctx: click.Context) -> None:
    client = ctx.obj.get("client") if ctx.obj else None
    if client is not None:
        client.close()


def entrypoint() -> None:
    try:
        main(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except click.Abort:
        click.echo("aborted", err=True)
        sys.exit(130)


if __name__ == "__main__":
    entrypoint()
