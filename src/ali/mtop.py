"""Signed access to AliExpress' internal ``mtop`` JSON API.

The API behind the product detail page is plain JSON, which is far nicer to
work with than the rendered DOM.  It is protected by Alibaba's ``mtop``
scheme:

1. The first unsigned call answers ``FAIL_SYS_TOKEN_EMPTY`` but sets the
   ``_m_h5_tk`` cookie, which looks like ``<token>_<expiry>``.
2. Every later call must carry
   ``sign = md5(token + "&" + t + "&" + appKey + "&" + data)``.

Two wrinkles make this fiddly:

* AliExpress' own front-end sometimes signs with a token that has already
  expired, and the server answers with a CAPTCHA challenge instead of data.
* The risk system expects a ``cna`` device cookie that only their JavaScript
  creates, so a bare HTTP client is rejected.

This module therefore keeps two routes: a fast plain-HTTP route using cookies
harvested from a real browser, and a guaranteed route that issues the request
from inside the browser page itself.  Either way the signature is computed
locally from the *current* token, which avoids the stale-token CAPTCHA.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from typing import Any

from .transport import AliBrowser, AliHTTP, BlockedError

MTOP_HOST = "https://acs.aliexpress.com"
DEFAULT_APPKEY = "12574478"
MOBILE_APPKEY = "24815441"
PDP_API = "mtop.aliexpress.pdp.pc.query"
PDP_MOBILE_API = "mtop.aliexpress.pdp.msite.query"


def sign(token: str, timestamp: str, app_key: str, data: str) -> str:
    """``md5(token & t & appKey & data)`` -- the mtop request signature."""
    return hashlib.md5(f"{token}&{timestamp}&{app_key}&{data}".encode()).hexdigest()


class MtopClient:
    """Client for the ``mtop`` gateway with automatic fallbacks."""

    def __init__(
        self,
        http: AliHTTP | None = None,
        browser: AliBrowser | None = None,
        app_key: str = DEFAULT_APPKEY,
    ) -> None:
        self.http = http or AliHTTP()
        self.browser = browser
        self.app_key = app_key
        self._template: dict[str, Any] | None = None
        self._harvested = False

    # -- session state -------------------------------------------------
    def _token(self) -> str:
        return (self.http.cookies().get("_m_h5_tk") or "").split("_")[0]

    def _has_device_state(self) -> bool:
        cookies = self.http.cookies()
        return bool(cookies.get("_m_h5_tk")) and bool(cookies.get("cna"))

    def harvest(self, product_id: str | None = None) -> None:
        """Pull device cookies and the request template from a real browser."""
        if self.browser is None:
            return
        url = (
            f"https://www.aliexpress.com/item/{product_id}.html"
            if product_id
            else "https://www.aliexpress.com/"
        )
        cookies, template = self.browser.harvest(url)
        for name, value in cookies.items():
            self.http.session.cookies.set(name, value, domain=".aliexpress.com")
        if template:
            self._template = template
        self._harvested = True

    # -- transports ----------------------------------------------------
    def _http_call(self, api: str, payload: dict[str, Any], app_key: str | None = None) -> dict[str, Any]:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        timestamp = str(int(time.time() * 1000))
        signature = sign(self._token(), timestamp, app_key or self.app_key, data)
        url = (
            f"{MTOP_HOST}/h5/{api}/1.0/?jsv=2.5.1&appKey={app_key or self.app_key}&t={timestamp}"
            f"&sign={signature}&api={urllib.parse.quote(api)}&v=1.0&type=originaljson"
            f"&dataType=json&data={urllib.parse.quote(data)}"
        )
        response = self.http.get(
            url,
            headers={
                "Accept": "application/json",
                "Referer": "https://www.aliexpress.com/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
            },
        )
        if response.status_code != 200:
            raise BlockedError(f"mtop {api} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise BlockedError(f"mtop {api} returned non-JSON: {response.text[:200]}") from exc

    def _browser_call(self, api: str, payload: dict[str, Any], app_key: str | None = None) -> dict[str, Any]:
        if self.browser is None:
            raise BlockedError(f"mtop {api} rejected and no browser transport is available")
        response = self.browser.mtop_call(api, payload, app_key or self.app_key)
        if "data" not in response:
            ret = " ".join(response.get("ret") or [])
            raise BlockedError(f"mtop {api} failed in browser: {ret}")
        return response

    # -- public --------------------------------------------------------
    def call(
        self,
        api: str,
        payload: dict[str, Any],
        product_id: str | None = None,
        app_key: str | None = None,
    ) -> dict[str, Any]:
        """Call *api*, escalating through the available transports.

        Order of preference: plain HTTP with cookies harvested from a browser,
        then the browser's own network stack.
        """
        if self.browser is not None and not self._has_device_state():
            self.harvest(product_id)

        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                response = self._http_call(api, payload, app_key)
            except BlockedError as exc:
                last_error = exc
                if self.browser is None:
                    raise
                response = self._browser_call(api, payload, app_key)

            ret = " ".join(response.get("ret") or [])
            if "SUCCESS" in ret:
                return response
            last_error = BlockedError(f"mtop {api} failed: {ret}")

            if "TOKEN_EMPTY" in ret or "TOKEN_EXOIRED" in ret or "ILLEGAL" in ret:
                # The failure is what hands us the token cookie; retry with it.
                time.sleep(0.3)
                continue
            if "USER_VALIDATE" in ret and self.browser is not None:
                # Risk control kicked in -- re-issue through the browser.
                self._harvested = False
                self.harvest(product_id)
                response = self._browser_call(api, payload, app_key)
                ret = " ".join(response.get("ret") or [])
                if "SUCCESS" in ret:
                    return response
                last_error = BlockedError(f"mtop {api} failed: {ret}")
            break

        raise last_error or BlockedError(f"mtop {api} failed")

    def product_detail(self, product_id: str, **overrides: Any) -> dict[str, Any]:
        """Fetch the structured product detail payload for *product_id*."""
        payload: dict[str, Any] = {}
        if self._template:
            # Reuse the front-end's own payload so every session-bound field
            # (locale, ship-to country, device token) matches our cookies.
            payload = dict(self._template)
            payload["productId"] = str(product_id)
            for stale in ("pdpNPI", "pdp_ext_f", "pdp_npi"):
                if stale in payload:
                    payload[stale] = ""
        else:
            payload = {
                "productId": str(product_id),
                "_lang": self.http.cfg.locale,
                "_currency": self.http.cfg.currency,
                "country": self.http.cfg.country,
                "province": "",
                "city": "",
                "channel": "",
                "pdp_ext_f": "",
                "pdpNPI": "",
                "sourceType": "",
                "clientType": "pc",
            }
        payload.update(overrides)
        try:
            response = self.call(PDP_API, payload, product_id=product_id)
        except BlockedError as exc:
            # The desktop query and the mobile one are policed separately, so
            # a challenge on one does not necessarily mean the other is closed.
            mobile = dict(payload, clientType="msite")
            try:
                response = self.call(PDP_MOBILE_API, mobile, product_id=product_id, app_key=MOBILE_APPKEY)
            except BlockedError:
                raise exc from None
        result = ((response.get("data") or {}).get("result")) or {}
        if not result:
            # A "successful" response with no modules means the request was
            # accepted but the product was not resolved -- treat it as a
            # failure rather than an empty product.
            raise BlockedError(
                f"no product data returned for {product_id} (the product page is likely being challenged)"
            )
        return result
