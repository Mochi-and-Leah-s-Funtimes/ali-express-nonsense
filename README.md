# ali-nonsense

A command line browser for AliExpress that understands a monthly budget.

```console
$ ali search "sg90 micro servo" --max-price 3
  #        price  rating  sold       title                                  id
  1     $0.23    4.7  10,000+    1PCS 9g micro servo for airplane 6CH…  1005006323922892
  2     $1.14    4.8  1,000+     TZT Official Smart Electronics Rc Mini…  1005006844487617
  3     $1.19    4.9  3,000+     Pro 9g Micro Servo For Airplane 6CH…    1005006645561341
```

---

## Why this isn't just `curl`

AliExpress is a single-page app with a bot-checker in front of it, so the
obvious approaches both fail:

| Approach | Result |
|---|---|
| `curl https://www.aliexpress.com/w/wholesale-*.html` | HTTP 200, 800 KB of HTML, **no products in it** — the grid is rendered client side |
| `curl` the product page | 2 KB CAPTCHA challenge page, no data |
| Plain HTTP to the internal JSON API | `FAIL_SYS_USER_VALIDATE` → CAPTCHA |

What actually works, and what this tool does:

1. **Search is server-rendered.** The first page of results is in the HTML
   that plain HTTP already returns. The product grid is in elements whose
   class names are CSS-module hashes (`us--price--3al65BO`), so the parser
   matches on the *stable prefix* before the hash. No browser needed, one
   request per page, and it does not trip the bot-checker.
2. **Product details live behind a signed JSON API.** The page calls
   `mtop.aliexpress.pdp.pc.query`, which needs a signature
   `md5(token + "&" + t + "&" + appKey + "&" + data)`. The catch is that
   AliExpress' own front-end sometimes signs with an expired token and the
   server answers with a CAPTCHA instead of data. This tool re-signs every
   request with the *current* token, which is what makes the API usable.
3. **That token only exists in a real browser.** The `_m_h5_tk` cookie is set
   by JavaScript, and the risk system also wants a `cna` device cookie. So a
   Playwright Chromium opens the page once, harvests cookies and the
   front-end's own request payload, and from there plain HTTP does the work.

The result: search costs one HTTP request, and a product page costs one
browser load plus a signed API call — instead of a full browser render of
every product.

### Layout of the code

```
src/ali/
  transport.py   HTTP session + Playwright browser (cookie harvesting, API calls)
  mtop.py        mtop request signing, token bootstrap, endpoint fallbacks
  search.py      SSR HTML -> Product parsing, filters, sorting
  detail.py      mtop payload -> ProductDetail (prices, stock, shipping, coupons)
  budget.py      SQLite ledger, watch list, detail cache
  client.py      ties the transports together, handles caching and backoff
  render.py      rich tables
  cli.py         the commands
```

---

## Install

```console
$ python -m venv .venv && . .venv/bin/activate
$ pip install -e '.[browser,dev]'
$ playwright install chromium
```

The `browser` extra is only needed for product details; search works without
it.

---

## Commands

### Search

```console
$ ali search "usb c hub" --max-price 12 --min-rating 4.5 --sort rating
$ ali search "usb c hub" --pages 3          # fetch three result pages
$ ali search "usb c hub" --json             # machine readable
```

Filters: `--max-price`, `--min-price`, `--min-rating`, `--sort`
(`price`, `-price`, `rating`, `discount`), `--limit`, `--pages`.

### Product detail

```console
$ ali product 1005009066568188
USB C Hub to 4K HDMI-Compatible USB 3.0 2.0 Type C PD Charging Dock …
https://www.aliexpress.com/item/1005009066568188.html

Price        US $1.09  US $14.19  (92% off)
Variants     US $1.09 – US $1.79
Rating       4.8 (2149)
Sold         10,000+ sold
Stock        71
Limit        max 1 per order
Shipping     free
Delivery     Delivery: Oct. 27 - Nov. 01
Store        Shop1104759008 Store

Variants
sku                 variant       price   stock
12000047797229526   5-in-1     US $1.09      21
12000047797229527   6-in-1     US $1.55      21
12000047797229528   7-in-1     US $1.79      29
```

This is the part that matters for buying: per-variant prices, stock,
whether shipping is free, the delivery window, the store, and coupons.

### Budget

The budget is the point of the tool. It is a monthly allowance with a
ledger, so the robot cannot commit the same money twice.

```console
$ ali budget --set 150
$ ali budget
Budget 2026-10
  cap       US $150.00
  spent     US $2.18
  remaining US $147.82

  id  when                  product                          qty   total
   1  2026-10-22 01:05      USB C Hub to 4K HDMI-Compatible…    2  US $2.18
```

```console
$ ali spend add 1005009066568188 --qty 2     # looks up price and shipping
$ ali spend add 1005009066568188 --price 4.46 --shipping 1.54   # or set it yourself
$ ali spend list
$ ali spend remove 1
```

`ali spend` records *intent*. It never places an order — see
[Deliberate limits](#deliberate-limits).

### Plan

Build a shopping list that fits the remaining budget:

```console
$ ali plan "sg90 micro servo" --qty 3 --max-items 3 --with-shipping
   #  item                                                 unit       ship       total
   1  1PCS 9g micro servo for airplane aeroplane 6C…   US $0.23   US $5.18    US $5.87
   2  TZT Official Smart Electronics Rc Mini Micro …   US $1.14   US $5.37    US $8.79
   3  Pro 9g Micro Servo For Airplane Aeroplane 6CH…   US $1.19   US $0.00    US $3.57
total US $18.23 of US $150.00
```

The plan is a greedy fill: cheapest first, never exceeding the remaining
allowance, capped at `--max-items`. `--with-shipping` looks up the real
delivery cost per item (slower — it needs the product page); without it the
plan is priced on the item price alone and says so.

`ali plan --json` emits a machine-readable plan, which is the form to use
when a robot is going to act on it.

### Watch list

```console
$ ali watch add 1005009066568188
$ ali watch check        # re-read prices, track the low/high
$ ali watch list
$ ali watch remove 1005009066568188
```

### Configuration

```console
$ ali config
```

Everything is also settable through the environment, which is what you want
for a cron job or a robot process:

| Variable | Default | Meaning |
|---|---|---|
| `ALI_CURRENCY` | `USD` | Display currency |
| `ALI_LOCALE` | `en_US` | Locale |
| `ALI_DATA_DIR` | `~/.local/share/ali-nonsense` | Ledger + cache |
| `ALI_MONTHLY_BUDGET` | `150` | Default monthly cap |
| `ALI_DELAY` | `1.0` | Seconds between requests |
| `ALI_HEADLESS` | `1` | Set to `0` for a visible browser |
| `ALI_TIMEOUT` | `30` | Per-request timeout, seconds |

---

## Working with the bot-checker

The product detail endpoint is aggressively rate-limited; the search endpoint
is not. When AliExpress challenges a session the tool says so plainly
instead of pretending, and falls back to what the search index already
knows about the product (price, rating, seller activity — but no stock,
shipping or variant prices), flagged as a partial record.

Three things keep it working:

- **Detail lookups are cached** in the ledger database for an hour, so
  repeated runs do not re-hit the protected endpoint. `ali product --refresh`
  forces a fresh fetch.
- **Requests are spaced out.** `ALI_DELAY` sets the floor; detail lookups
  additionally keep a two second minimum gap.
- **The browser session warms up before it works.** The first request of a
  session is the one that gets challenged, so the tool loads the homepage
  first to obtain a valid token and only then queries the API.

If you hit a challenge, wait a few minutes. Search keeps working throughout,
so the budget planning never stops.

---

## Deliberate limits

- **No ordering.** The tool reads, prices and plans. It records intent in the
  ledger and hands the plan over. Placing an order is a separate, explicit
  decision — which is the right shape for something that spends real money,
  and it keeps the tool inside AliExpress' terms.
- **No CAPTCHA solving.** When a challenge appears, the tool reports it.
  It does not try to defeat it, and it does not rotate identities to hide
  from it.
- **Polite by default.** One request at a time, a delay between requests,
  and a browser that behaves like a browser.

---

## Development

```console
$ .venv/bin/python -m pytest -q
42 passed
```

The tests cover price and currency parsing, the HTML card parser (against a
fixture of the real markup), the budget ledger, the detail mapper, and the
CLI. Nothing in the test suite touches the network.
