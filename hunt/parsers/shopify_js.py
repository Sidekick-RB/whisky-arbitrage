"""Shared Shopify per-product .js resolver (US overseas benchmarks).

DramFellows, Wooden Cork and Mash & Grape are all Shopify stores
whose search cards carry no reliable bottle size. Each product's
`.js` endpoint (/products/<handle>.js) returns one JSON object with
`price` (cents), `available` (bool), `variants[]`, `options[]`,
`tags[]` and `description`. This module resolves one such payload
into a single (price, size_ml, available) triple — or None when the
item must be dropped. Price and size always come from the SAME
variant, never mixed across variants.
"""

import re
from urllib.parse import urlparse

from .base import fetch_json, parse_size_ml

_BARE_SIZE_RE = re.compile(r"^\s*\d+(\.\d+)?\s*(ml|l|cl)\s*$", re.IGNORECASE)


def _size_for_variant(variant, data):
    """Bottle size in ml for the chosen variant, or None.

    Order: (1) the variant title when it is a bare size ("750ml");
    (2) the Size option's first value; (3) a tag carrying a size
    ("Size_750ml", "glenfiddich 750ml"); (4) the description.
    """
    assert isinstance(variant, dict), "variant must be a dict"
    assert isinstance(data, dict), "product data must be a dict"
    title = variant.get("title")
    if isinstance(title, str) and _BARE_SIZE_RE.match(title):
        size = parse_size_ml(title)
        if size is not None:
            return size
    options = data.get("options")
    if isinstance(options, list):
        for option in options:
            assert isinstance(option, dict), "option must be a dict"
            name = option.get("name")
            if not isinstance(name, str):
                continue
            if name.strip().lower() != "size":
                continue
            values = option.get("values")
            if isinstance(values, list) and values:
                first = values[0]
                if isinstance(first, str):
                    size = parse_size_ml(first)
                    if size is not None:
                        return size
            break
    tags = data.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str):
                size = parse_size_ml(tag)
                if size is not None:
                    return size
    description = data.get("description")
    if isinstance(description, str) and description:
        size = parse_size_ml(description)
        if size is not None:
            return size
    return None


def resolve_product_js(data):
    """Resolve one Shopify .js payload to (price, size_ml, available).

    The variant is picked first: the first variant whose title is a
    BARE size only ("750ml" qualifies; "3 Pack 750mL" and
    "Default Title" do not); when none qualifies, variants[0].
    Price is the chosen variant's price in cents / 100. Returns None
    (drop the item) when the payload is not a dict, has no variants,
    the chosen variant is unavailable, its price is not positive, no
    size resolves, or the chosen variant is a pack ("3 Pack 750mL"):
    a pack price must never pair with a single-bottle size.
    """
    if not isinstance(data, dict):
        return None
    variants = data.get("variants")
    if not isinstance(variants, list) or not variants:
        return None
    chosen = None
    for variant in variants:
        assert isinstance(variant, dict), "variant must be a dict"
        title = variant.get("title")
        if isinstance(title, str) and _BARE_SIZE_RE.match(title):
            chosen = variant
            break
    if chosen is None:
        chosen = variants[0]
    assert isinstance(chosen, dict), "chosen variant must be a dict"
    if chosen.get("available") is not True:
        return None
    title = chosen.get("title")
    if isinstance(title, str) and re.search(r"\bpack\b", title, re.I):
        return None
    price_cents = chosen.get("price")
    if isinstance(price_cents, bool):
        return None
    if not isinstance(price_cents, (int, float)):
        return None
    price = price_cents / 100
    if price <= 0:
        return None
    assert isinstance(price, float), "price must be a float"
    size_ml = _size_for_variant(chosen, data)
    if size_ml is None:
        return None
    assert size_ml > 0, "resolved size must be positive"
    return price, size_ml, True


def product_js_url(base_url, product_url):
    """Per-product .js URL for a Shopify product URL (query stripped)."""
    assert isinstance(base_url, str) and base_url.startswith("http")
    assert isinstance(product_url, str) and product_url.startswith("http")
    path = urlparse(product_url).path
    assert path.startswith("/products/"), "not a Shopify product path"
    url = base_url.rstrip("/") + path + ".js"
    assert url.startswith(base_url), ".js URL must stay on the retailer"
    return url


def fetch_product_js(url):
    """GET a Shopify /products/<handle>.js endpoint.

    Returns the parsed JSON value (dict), or None on any failure.
    A None never becomes a price; the caller drops the item.
    """
    assert isinstance(url, str) and url.startswith("http"), ".js URL required"
    data = fetch_json(url)
    assert data is None or isinstance(data, (dict, list)), "unexpected payload"
    return data
