"""Runtime configuration.

Everything is overridable with ``ALI_*`` environment variables so the tool can
be driven from a cron job or a robot process without touching config files.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PROFILE = "en_US"
# NOTE: do not add ``region=`` to this cookie unless you really want the
# ali**express.us** mirror.  That mirror issues its own ``_m_h5_tk`` token and
# the global product ids stop resolving, so the default keeps the global site
# and only overrides the display currency.
CURRENCY_COOKIE_TEMPLATE = "site=glo&c_tp={currency}&b_locale={locale}"
CURRENCY_COOKIE_WITH_REGION = "site=glo&c_tp={currency}&region={region}&b_locale={locale}"


def _env(name: str, default: str) -> str:
    return os.environ.get(f"ALI_{name}", default)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Config:
    currency: str = field(default_factory=lambda: _env("CURRENCY", "USD"))
    locale: str = field(default_factory=lambda: _env("LOCALE", "en_US"))
    region: str = field(default_factory=lambda: _env("REGION", ""))
    country: str = field(default_factory=lambda: _env("COUNTRY", "US"))
    data_dir: Path = field(
        default_factory=lambda: Path(_env("DATA_DIR", "~/.local/share/ali-nonsense")).expanduser()
    )
    profile_dir: Path = field(
        default_factory=lambda: Path(_env("PROFILE_DIR", "~/.cache/ali-nonsense/browser")).expanduser()
    )
    monthly_budget: float = field(default_factory=lambda: float(_env("MONTHLY_BUDGET", "150")))
    timeout: float = field(default_factory=lambda: float(_env("TIMEOUT", "30")))
    headless: bool = field(default_factory=lambda: _env_bool("HEADLESS", True))
    delay: float = field(default_factory=lambda: float(_env("DELAY", "1.0")))
    user_agent: str = field(
        default_factory=lambda: _env(
            "USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/156.0.0.0 Safari/537.36",
        )
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ali.db"

    @property
    def locale_cookie(self) -> str:
        template = CURRENCY_COOKIE_WITH_REGION if self.region else CURRENCY_COOKIE_TEMPLATE
        return template.format(currency=self.currency, region=self.region, locale=self.locale)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "currency": self.currency,
            "locale": self.locale,
            "region": self.region,
            "country": self.country,
            "monthly_budget": self.monthly_budget,
        }
        (self.data_dir / "config.json").write_text(json.dumps(payload, indent=2))

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        path = cfg.data_dir / "config.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                return cfg
            for key in ("currency", "locale", "region", "country"):
                if key in payload:
                    setattr(cfg, key, payload[key])
            if "monthly_budget" in payload:
                with suppress(TypeError, ValueError):
                    cfg.monthly_budget = float(payload["monthly_budget"])
        return cfg
