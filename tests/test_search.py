from decimal import Decimal

from ali.models import Product
from ali.search import extract_product_id, filter_products, parse_cards, price_bounds
from ali.transport import slugify

CARD = """
<div class="list--gallery--C2f2tvm search-item-card-wrapper-gallery">
  <div class="us--modalContext--mhWkOqM">
    <div class="us--outWrapper--22kvi2f card-out-wrapper">
      <a class="us--container--3_xK0hW cards--card--3PJxwBm search-card-item"
         href="https://www.aliexpress.com/item/1005009066568188.html?algo_pvid=abc">
        <div class="us--image--3uexGbd us--imagesGallery--2rGMU5h">
          <img class="images--item--2qh24vj" src="//ae-pic-a1.aliexpress-media.com/kf/abc.jpg">
        </div>
        <div class="us--content--2u7Eb2L">
          <div title="USB C Hub to 4K HDMI Dock"
               class="us--title--1ZMNYmD" role="heading">
            <h3 class="us--titleText--WpU1HVZ">USB C Hub to 4K HDMI Dock</h3>
          </div>
          <div class="us--price--3al65BO" tabindex="0" aria-label="$4.46">
            <div class="us--price-sale--8S2hu0e"><span>$</span><span>4</span><span>.</span><span>46</span></div>
            <div class="us--price-original--2m114k8"><span>$18.39</span></div>
            <span class="us--minDiscount--1usmfHW"> -75%</span>
          </div>
          <div class="us--tradeContainer--39KErpB">
            <div class="us--starList--2tRbsmE">
              <img src="star.png"><span class="us--starRating--ebGM4Ii">4.8</span>
            </div>
            <span><span class="us--trade--1lEAXth">10,000+ sold</span></span>
          </div>
        </div>
      </a>
    </div>
  </div>
</div>
"""


def test_slugify():
    assert slugify("usb c hub") == "usb-c-hub"
    assert slugify("SG90 Servo/Motor") == "sg90-servo-motor"
    assert slugify("  USB   Hub  ") == "usb-hub"


def test_extract_product_id():
    assert extract_product_id("https://www.aliexpress.com/item/1005009066568188.html") == "1005009066568188"
    assert (
        extract_product_id("//www.aliexpress.com/ssr/300001493/x?productIds=1005007244139650")
        == "1005007244139650"
    )
    assert (
        extract_product_id("https://x/?utparam-url=scene%3Asearch%7Cx_object_id%3A1005003296782923")
        == "1005003296782923"
    )
    assert extract_product_id("https://example.com/nothing") is None
    assert extract_product_id(None) is None


def test_parse_cards():
    products = parse_cards(f"<html><body>{CARD}</body></html>")
    assert len(products) == 1
    product = products[0]
    assert product.product_id == "1005009066568188"
    assert product.title == "USB C Hub to 4K HDMI Dock"
    assert product.price == Decimal("4.46")
    assert product.original_price == Decimal("18.39")
    assert product.discount == 75
    assert product.rating == 4.8
    assert product.sold == "10,000+ sold"
    assert product.currency == "USD"
    assert product.url == "https://www.aliexpress.com/item/1005009066568188.html"
    assert product.image.startswith("https://ae-pic-a1.aliexpress-media.com/")


def test_parse_cards_handles_garbage():
    assert parse_cards("") == []
    assert parse_cards("not html at all") == []


def test_filter_and_bounds():
    products = [
        Product(product_id="1", title="a", url="", price=Decimal("5")),
        Product(product_id="2", title="b", url="", price=Decimal("15")),
        Product(product_id="3", title="c", url="", price=None, rating=4.9),
    ]
    assert len(filter_products(products, max_price=Decimal("10"))) == 1
    assert len(filter_products(products, min_price=Decimal("10"))) == 1
    assert len(filter_products(products, min_rating=4.5)) == 1
    assert price_bounds(products) == (Decimal("5"), Decimal("15"))
    assert price_bounds([]) == (None, None)
