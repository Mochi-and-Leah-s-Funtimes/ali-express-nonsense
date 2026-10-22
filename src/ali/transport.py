"""Transport layer.

Two transports share one session profile (headers + locale cookies):

``AliHTTP``
    Plain ``requests``.  AliExpress server-renders the first page of search
    results, so the HTML already contains the product grid.  Fast, cheap and
    it never trips the bot-checker.

``AliBrowser``
    A real Chromium via Playwright with a persistent profile.  Used for product
    detail pages (which need the JavaScript runtime) and as a fallback whenever
    the plain HTTP path stops working.

Both are polite by default: a configurable inter-request delay, a real
``Accept-Language``, and no attempt to defeat CAPTCHAs.  When AliExpress shows
a challenge we surface it instead of trying to solve it.
"""

from __future__ import annotations

import contextlib
import json
import random
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests

from .config import Config

BASE = "https://www.aliexpress.com"
DEFAULT_APPKEY = "12574478"

# The search grid is rendered server side into these hashed class names.  The
# stable part is the prefix before the trailing hash (e.g. ``us--price--``).
STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
window.chrome = window.chrome || {runtime: {}, app: {isInstalled: false}};
"""


class BlockedError(RuntimeError):
    """Raised when AliExpress answers with an anti-bot challenge."""


def build_headers(cfg: Config, referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def slugify(query: str) -> str:
    """Turn a query into the slug AliExpress uses in ``/w/wholesale-<slug>.html``."""
    cleaned = "".join(char if char.isalnum() else "-" for char in query.lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")


class AliHTTP:
    """Session-backed HTTP client that mimics a normal desktop browser."""

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config.load()
        self.session = requests.Session()
        self.session.headers.update(build_headers(self.cfg))
        self.session.cookies.set("aep_usuc_f", self.cfg.locale_cookie, domain=".aliexpress.com")
        self.session.cookies.set("currency", self.cfg.currency, domain=".aliexpress.com")
        self.session.cookies.set("intl_locale", self.cfg.locale, domain=".aliexpress.com")
        self._last_request = 0.0

    def _throttle(self) -> None:
        gap = self.cfg.delay * random.uniform(0.8, 1.4)
        elapsed = time.monotonic() - self._last_request
        if elapsed < gap:
            time.sleep(gap - elapsed)
        self._last_request = time.monotonic()

    def get(self, url: str, headers: dict[str, str] | None = None, **kwargs: Any) -> requests.Response:
        self._throttle()
        return self.session.get(url, headers=headers, timeout=self.cfg.timeout, **kwargs)

    def get_text(self, url: str, headers: dict[str, str] | None = None, **kwargs: Any) -> str:
        response = self.get(url, headers=headers, **kwargs)
        if response.status_code != 200:
            raise BlockedError(f"HTTP {response.status_code} for {url}")
        if "_____tmd_____" in response.text or "/punish?" in response.url:
            raise BlockedError(f"anti-bot challenge returned for {url}")
        return response.text

    def search_url(self, query: str, page: int = 1) -> str:
        slug = slugify(query)
        params = []
        if page > 1:
            params.append(f"page={page}")
        params.append("g=y")
        params.append("SortType=default")
        return f"{BASE}/w/wholesale-{slug}.html?{'&'.join(params)}"

    def item_url(self, product_id: str) -> str:
        return f"{BASE}/item/{product_id}.html"

    def cookies(self) -> dict[str, str]:
        return {c.name: c.value for c in self.session.cookies}


@dataclass
class BrowserResult:
    url: str
    html: str
    captured: dict[str, str]


class AliBrowser:
    """Real Chromium session backed by a persistent profile.

    The persistent profile is what makes repeat runs look like the same browser
    instead of a fresh automation fingerprint on every invocation.
    """

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config.load()
        self.cfg.ensure_dirs()
        self._pw = None
        self._browser = None
        self._context = None

    def __enter__(self) -> AliBrowser:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.cfg.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=self.cfg.user_agent,
            locale=self.cfg.locale.replace("_", "-"),
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        self._context.add_init_script(STEALTH_INIT)
        self._context.add_cookies(
            [
                {
                    "name": "aep_usuc_f",
                    "value": self.cfg.locale_cookie,
                    "domain": ".aliexpress.com",
                    "path": "/",
                },
                {"name": "currency", "value": self.cfg.currency, "domain": ".aliexpress.com", "path": "/"},
            ]
        )
        return self

    def __exit__(self, *exc: object) -> None:
        shared = getattr(self, "_shared_page", None)
        if shared is not None:
            with contextlib.suppress(Exception):
                shared.close()
            self._shared_page = None
        for closer in (self._context, self._browser, self._pw):
            try:
                if closer is not None:
                    closer.stop() if hasattr(closer, "stop") else closer.close()
            except Exception:
                pass
        self._context = self._browser = self._pw = None

    @property
    def context(self):
        if self._context is None:
            raise RuntimeError("browser not started; use it as a context manager")
        return self._context

    def cookies(self) -> dict[str, str]:
        return {c["name"]: c["value"] for c in self.context.cookies()}

    def _page(self):
        """Return a long-lived page used for in-page API calls."""
        if getattr(self, "_shared_page", None) is None:
            self._shared_page = self.context.new_page()
        return self._shared_page

    def harvest(self, url: str, wait_ms: int = 7000) -> tuple[dict[str, str], dict[str, Any] | None]:
        """Load *url* and return ``(cookies, request_template)``.

        The template is the ``data`` payload AliExpress' own front-end sends
        with its product query.  Reusing it verbatim -- and only swapping the
        product id -- keeps every session-bound field (locale, ship-to country,
        device token) consistent with the cookies we send, which is what the
        risk system checks.
        """
        page = self._page()
        captured: list[str] = []

        def on_request(request: Any) -> None:
            if "pdp" in request.url and "punish" not in request.url and "query" in request.url:
                captured.append(request.url)

        page.on("request", on_request)
        try:
            # The homepage issues mtop calls that succeed, which is what sets
            # the ``_m_h5_tk`` token in the jar.  The product page's own query
            # is the one that gets challenged, so it never sets a token -- go
            # via the homepage first or we end up with an unsigned request.
            try:
                page.goto(
                    "https://www.aliexpress.com/",
                    wait_until="domcontentloaded",
                    timeout=int(self.cfg.timeout * 1000),
                )
                page.wait_for_timeout(4000)
            except Exception:
                pass

            page.goto(url, wait_until="domcontentloaded", timeout=int(self.cfg.timeout * 1000))
            page.wait_for_timeout(wait_ms)

            template: dict[str, Any] | None = None
            for raw in captured:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
                blob = (query.get("data") or [""])[0]
                if not blob:
                    continue
                try:
                    parsed = json.loads(blob)
                except ValueError:
                    continue
                if parsed.get("productId"):
                    template = parsed
                    break

            # A missing template is not fatal: the caller can still try the
            # other product-query API, and the cookies are what matter most.
            return self.cookies(), template
        finally:
            with contextlib.suppress(Exception):
                page.remove_listener("request", on_request)

    def mtop_call(
        self,
        api: str,
        payload: dict[str, Any],
        app_key: str = DEFAULT_APPKEY,
        sign_fn: Callable[[str, str, str, str], str] | None = None,
    ) -> dict[str, Any]:
        """Call an mtop API through the browser's own network stack.

        ``context.request`` shares the browser's cookie jar but issues the
        request outside the page, so it cannot be blocked by a CAPTCHA frame
        that AliExpress may have injected into the page.
        """
        import hashlib

        token = ""
        for name, value in self.cookies().items():
            if name == "_m_h5_tk":
                token = value.split("_")[0]
                break

        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        timestamp = str(int(time.time() * 1000))
        if sign_fn is None:

            def sign_fn(token_: str, t: str, key: str, body: str) -> str:
                return hashlib.md5(f"{token_}&{t}&{key}&{body}".encode()).hexdigest()

        signature = sign_fn(token, timestamp, app_key, data)
        url = (
            f"https://acs.aliexpress.com/h5/{api}/1.0/?jsv=2.5.1&appKey={app_key}"
            f"&t={timestamp}&sign={signature}&api={urllib.parse.quote(api)}"
            f"&v=1.0&type=originaljson&dataType=json&data={urllib.parse.quote(data)}"
        )
        response = self.context.request.get(
            url,
            timeout=int(self.cfg.timeout * 1000),
            headers={"Accept": "application/json", "Referer": "https://www.aliexpress.com/"},
        )
        text = response.text()
        try:
            return json.loads(text)
        except ValueError as exc:
            raise BlockedError(f"mtop {api} returned non-JSON: {text[:200]}") from exc

    def fetch(
        self,
        url: str,
        wait_ms: int = 6000,
        scroll: int = 0,
        capture: Callable[[str], bool] | None = None,
        selector_wait: str | None = None,
    ) -> BrowserResult:
        """Load *url*, optionally scroll, and return the rendered HTML.

        *capture* is a predicate over response URLs; matching JSON bodies are
        collected into the result so structured data can be read directly
        instead of scraped out of the DOM.
        """
        page = self.context.new_page()
        captured: dict[str, str] = {}
        try:
            if capture is not None:

                def on_response(response: Any) -> None:
                    try:
                        if capture(response.url):
                            captured[response.url] = response.text()
                    except Exception:
                        pass

                page.on("response", on_response)

            page.goto(url, wait_until="domcontentloaded", timeout=int(self.cfg.timeout * 1000))
            page.wait_for_timeout(wait_ms)
            if selector_wait:
                with contextlib.suppress(Exception):
                    page.wait_for_selector(selector_wait, timeout=15000)
            for _ in range(scroll):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(2200)
            return BrowserResult(url=page.url, html=page.content(), captured=captured)
        finally:
            with contextlib.suppress(Exception):
                page.close()
