"""Budget ledger and price watch list, backed by SQLite.

The robot needs three things from this module:

* how much of the monthly allowance is already committed,
* a record of what it intends to buy (so it cannot double-spend), and
* a watch list to notice when something it wants becomes cheap.

The ledger is intentionally *advisory*: it records intent, it never places an
order.  Ordering stays a human (or a separately authorised agent) decision.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    product_id  TEXT    NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    qty         INTEGER NOT NULL DEFAULT 1,
    unit_price  REAL    NOT NULL DEFAULT 0,
    shipping    REAL    NOT NULL DEFAULT 0,
    currency    TEXT    NOT NULL DEFAULT 'USD',
    note        TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS watch (
    product_id  TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    price       REAL,
    currency    TEXT NOT NULL DEFAULT 'USD',
    added_ts    TEXT NOT NULL,
    checked_ts  TEXT,
    min_price   REAL,
    max_price   REAL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache (
    kind    TEXT NOT NULL,
    key     TEXT NOT NULL,
    ts      TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);

CREATE INDEX IF NOT EXISTS spend_ts ON spend (ts);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def month_key(ts: str | None = None) -> str:
    """Return the ``YYYY-MM`` bucket for *ts* (defaults to now, in UTC)."""
    moment = datetime.now(UTC) if ts is None else datetime.fromisoformat(ts)
    return moment.strftime("%Y-%m")


@dataclass
class SpendEntry:
    id: int
    ts: str
    product_id: str
    title: str
    qty: int
    unit_price: Decimal
    shipping: Decimal
    currency: str
    note: str

    @property
    def total(self) -> Decimal:
        return self.unit_price * self.qty + self.shipping

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "product_id": self.product_id,
            "title": self.title,
            "qty": self.qty,
            "unit_price": float(self.unit_price),
            "shipping": float(self.shipping),
            "total": float(self.total),
            "currency": self.currency,
            "note": self.note,
        }


@dataclass
class WatchEntry:
    product_id: str
    title: str
    url: str
    price: Decimal | None
    currency: str
    added_ts: str
    checked_ts: str | None
    min_price: Decimal | None
    max_price: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        def dec(value: Decimal | None) -> float | None:
            return float(value) if value is not None else None

        return {
            "product_id": self.product_id,
            "title": self.title,
            "url": self.url,
            "price": dec(self.price),
            "currency": self.currency,
            "added_ts": self.added_ts,
            "checked_ts": self.checked_ts,
            "min_price": dec(self.min_price),
            "max_price": dec(self.max_price),
        }


class BudgetStore:
    """SQLite-backed ledger."""

    def __init__(self, db_path: Path | str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> BudgetStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- settings ------------------------------------------------------
    def set_cap(self, amount: Decimal) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('monthly_cap', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(amount),),
        )
        self.conn.commit()

    def get_cap(self, default: Decimal = Decimal("150")) -> Decimal:
        row = self.conn.execute("SELECT value FROM meta WHERE key = 'monthly_cap'").fetchone()
        if row is None:
            return default
        try:
            return Decimal(row["value"])
        except Exception:
            return default

    # -- spending ------------------------------------------------------
    def record(
        self,
        product_id: str,
        title: str = "",
        qty: int = 1,
        unit_price: Decimal | Decimal | float = Decimal("0"),
        shipping: Decimal | float = Decimal("0"),
        currency: str = "USD",
        note: str = "",
        ts: str | None = None,
    ) -> SpendEntry:
        """Record an intended purchase against the budget."""
        unit = Decimal(str(unit_price))
        ship = Decimal(str(shipping))
        cursor = self.conn.execute(
            "INSERT INTO spend (ts, product_id, title, qty, unit_price, shipping, currency, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts or _now(), str(product_id), title, int(qty), float(unit), float(ship), currency, note),
        )
        self.conn.commit()
        return SpendEntry(
            id=int(cursor.lastrowid or 0),
            ts=ts or _now(),
            product_id=str(product_id),
            title=title,
            qty=int(qty),
            unit_price=unit,
            shipping=ship,
            currency=currency,
            note=note,
        )

    def entries(self, month: str | None = None) -> list[SpendEntry]:
        bucket = month or month_key()
        rows = self.conn.execute(
            "SELECT * FROM spend WHERE ts LIKE ? ORDER BY id DESC", (f"{bucket}%",)
        ).fetchall()
        return [
            SpendEntry(
                id=row["id"],
                ts=row["ts"],
                product_id=row["product_id"],
                title=row["title"],
                qty=row["qty"],
                unit_price=Decimal(str(row["unit_price"])),
                shipping=Decimal(str(row["shipping"])),
                currency=row["currency"],
                note=row["note"],
            )
            for row in rows
        ]

    def remove(self, entry_id: int) -> bool:
        cursor = self.conn.execute("DELETE FROM spend WHERE id = ?", (entry_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def spent(self, month: str | None = None) -> Decimal:
        bucket = month or month_key()
        row = self.conn.execute(
            "SELECT COALESCE(SUM(unit_price * qty + shipping), 0) AS total FROM spend WHERE ts LIKE ?",
            (f"{bucket}%",),
        ).fetchone()
        return Decimal(str(row["total"]))

    def status(self, month: str | None = None, default_cap: Decimal = Decimal("150")) -> dict[str, Any]:
        bucket = month or month_key()
        cap = self.get_cap(default_cap)
        entries = self.entries(bucket)
        spent = self.spent(bucket)
        return {
            "month": bucket,
            "cap": float(cap),
            "spent": float(spent),
            "remaining": float(cap - spent),
            "currency": "USD",
            "entries": [e.to_dict() for e in entries],
        }

    # -- watch list ----------------------------------------------------
    def watch_add(
        self,
        product_id: str,
        title: str = "",
        url: str = "",
        price: Decimal | float | None = None,
        currency: str = "USD",
    ) -> WatchEntry:
        price_dec = Decimal(str(price)) if price is not None else None
        self.conn.execute(
            "INSERT INTO watch (product_id, title, url, price, currency, added_ts, min_price, max_price) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(product_id) DO UPDATE SET title = excluded.title, url = excluded.url, "
            "price = excluded.price, currency = excluded.currency",
            (
                str(product_id),
                title,
                url,
                float(price_dec) if price_dec is not None else None,
                currency,
                _now(),
                float(price_dec) if price_dec is not None else None,
                float(price_dec) if price_dec is not None else None,
            ),
        )
        self.conn.commit()
        return self.watch_get(str(product_id))  # type: ignore[return-value]

    def watch_get(self, product_id: str) -> WatchEntry | None:
        row = self.conn.execute("SELECT * FROM watch WHERE product_id = ?", (str(product_id),)).fetchone()
        return _watch_row(row) if row else None

    def watch_list(self) -> list[WatchEntry]:
        rows = self.conn.execute("SELECT * FROM watch ORDER BY added_ts DESC").fetchall()
        return [_watch_row(row) for row in rows]

    def watch_remove(self, product_id: str) -> bool:
        cursor = self.conn.execute("DELETE FROM watch WHERE product_id = ?", (str(product_id),))
        self.conn.commit()
        return cursor.rowcount > 0

    def watch_update_price(self, product_id: str, price: Decimal | float | None) -> WatchEntry | None:
        """Record a fresh price reading (or just the check time)."""
        row = self.conn.execute("SELECT * FROM watch WHERE product_id = ?", (str(product_id),)).fetchone()
        if row is None:
            return None
        if price is None:
            self.conn.execute(
                "UPDATE watch SET checked_ts = ? WHERE product_id = ?", (_now(), str(product_id))
            )
            self.conn.commit()
            return self.watch_get(str(product_id))
        value = Decimal(str(price))
        lowest = row["min_price"]
        highest = row["max_price"]
        lowest = value if lowest is None else min(Decimal(str(lowest)), value)
        highest = value if highest is None else max(Decimal(str(highest)), value)
        self.conn.execute(
            "UPDATE watch SET price = ?, min_price = ?, max_price = ?, checked_ts = ? WHERE product_id = ?",
            (float(value), float(lowest), float(highest), _now(), str(product_id)),
        )
        self.conn.commit()
        return self.watch_get(str(product_id))

    def watch_history(self, product_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT ts, unit_price FROM spend WHERE product_id = ? ORDER BY ts", (str(product_id),)
        ).fetchall()
        return [{"ts": r["ts"], "price": r["unit_price"]} for r in rows]

    # -- cache ---------------------------------------------------------
    def cache_get(self, kind: str, key: str, max_age_seconds: int | None = None) -> Any | None:
        """Return a cached payload, or ``None`` when absent or stale."""
        row = self.conn.execute(
            "SELECT ts, payload FROM cache WHERE kind = ? AND key = ?", (kind, str(key))
        ).fetchone()
        if row is None:
            return None
        if max_age_seconds is not None:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(row["ts"])).total_seconds()
            except (TypeError, ValueError):
                age = 0
            if age > max_age_seconds:
                return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    def cache_set(self, kind: str, key: str, payload: Any) -> None:
        self.conn.execute(
            "INSERT INTO cache (kind, key, ts, payload) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(kind, key) DO UPDATE SET ts = excluded.ts, payload = excluded.payload",
            (kind, str(key), _now(), json.dumps(payload)),
        )
        self.conn.commit()

    def cache_age(self, kind: str, key: str) -> float | None:
        """Age of a cached entry in seconds, or ``None`` when not cached."""
        row = self.conn.execute(
            "SELECT ts FROM cache WHERE kind = ? AND key = ?", (kind, str(key))
        ).fetchone()
        if row is None:
            return None
        try:
            return (datetime.now(UTC) - datetime.fromisoformat(row["ts"])).total_seconds()
        except (TypeError, ValueError):
            return None


def _watch_row(row: sqlite3.Row) -> WatchEntry:
    def dec(value: Any) -> Decimal | None:
        return Decimal(str(value)) if value is not None else None

    return WatchEntry(
        product_id=row["product_id"],
        title=row["title"],
        url=row["url"],
        price=dec(row["price"]),
        currency=row["currency"],
        added_ts=row["added_ts"],
        checked_ts=row["checked_ts"],
        min_price=dec(row["min_price"]),
        max_price=dec(row["max_price"]),
    )


def plan_combination(
    budget: Decimal,
    candidates: Iterable[tuple[str, Decimal]],
    max_items: int = 5,
) -> list[tuple[str, Decimal]]:
    """Greedily pick the cheapest items that fit inside *budget*.

    Sorted by price so the result is deterministic and spends as much of the
    allowance as possible without ever exceeding it.
    """
    ordered = sorted((c for c in candidates if c[1] > 0), key=lambda c: c[1])
    chosen: list[tuple[str, Decimal]] = []
    remaining = budget
    for product_id, price in ordered:
        if len(chosen) >= max_items:
            break
        if price <= remaining:
            chosen.append((product_id, price))
            remaining -= price
    return chosen
