"""Shared parser helpers: price/size parsing and the stealth browser.

Every parser in hunt/parsers/ follows the same shape: a pure
parse_page_html(html, ...) function (unit-testable from a saved fixture)
plus a small async driver that pages through a site with bounded loops.
"""

import json
import re
import urllib.request

MAX_PAGES = 40
MAX_LISTINGS = 2000
MAX_TILES_PER_PAGE = 60
MAX_RETRIES = 3
PAGE_TIMEOUT_MS = 30000
PAGE_DELAY_S = 1.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

STEALTH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
)

_HEAVY_TYPES = ("image", "media", "font", "stylesheet")
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s?(ml|l|cl)\b", re.IGNORECASE)
_PRICE_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d{1,2})?)")

# Bot-wall markers: (substring, wall name). Checked before any product
# parse; a match means the page is a challenge page, not a listing.
BOT_WALL_MARKERS = (
    ("Just a moment...", "cloudflare"),
    ("challenges.cloudflare.com", "cloudflare"),
    ("Vercel Security Checkpoint", "vercel-checkpoint"),
    ("px-captcha", "perimeterx"),
    ("PerimeterX", "perimeterx"),
    ("Please enable JS and disable any ad blocker", "datadome"),
    ("Attention Required! | Cloudflare", "cloudflare"),
)


def detect_bot_wall(html):
    """Bot-wall name when the page is a challenge page, else None."""
    assert isinstance(html, str), "html must be a string"
    assert html, "html must be non-empty"
    for marker, wall in BOT_WALL_MARKERS:
        assert marker and wall, "marker and wall must be non-empty"
        if marker in html:
            return wall
    return None


def fetch_json(url):
    """GET a JSON URL with a browser User-Agent. Returns the parsed
    JSON value (dict or list), or None on any failure (HTTP error,
    non-JSON body, timeout). A None never becomes a price; the caller
    records it as an error."""
    assert isinstance(url, str) and url.startswith("http")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200, "fetch must be HTTP 200"
            body = response.read()
    except Exception:
        return None
    assert body, "feed body must be non-empty"
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, (dict, list)):
        return None
    return data


def fetch_text(url):
    """GET a URL with a browser User-Agent. Returns the decoded body,
    or None on any failure. A None never becomes a listing; the caller
    records it as an error."""
    assert isinstance(url, str) and url.startswith("http")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200, "fetch must be HTTP 200"
            body = response.read()
    except Exception:
        return None
    assert body, "page body must be non-empty"
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text


def parse_price(text):
    """Parse a displayed price amount, any currency. Returns float or None.

    The currency symbol is ignored here; the caller records currency
    from the retailer. Never invents a price from a partial match.
    """
    assert isinstance(text, str), "price text must be a string"
    match = _PRICE_RE.search(text)
    if match is None:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    # A zero or negative amount (e.g. a free event ticket priced
    # "0.00" in a Shopify feed) is not a price: skip it, never crash.
    assert isinstance(value, float), "parsed amount must be a float"
    return value if value > 0 else None


def detect_currency(text):
    """Currency code from a price string: GBP for £, USD for $, else None."""
    assert isinstance(text, str), "price text must be a string"
    if "£" in text:
        return "GBP"
    if "$" in text:
        return "USD"
    if "€" in text:
        return "EUR"
    return None


def parse_price_sgd(text):
    """Parse a displayed SGD price such as "$44.95" or "S$1,234.56".

    Returns a float, or None when the text carries no price. Never
    invents a price from a partial match.
    """
    assert isinstance(text, str), "price text must be a string"
    result = parse_price(text)
    assert result is None or result > 0
    return result


def parse_size_ml(text):
    """Parse a bottle size such as "700ml", "1L", "750ml" or "70cl".

    Returns millilitres as an int, or None when unparseable.
    """
    assert isinstance(text, str), "size text must be a string"
    match = _SIZE_RE.search(text.strip())
    if match is None:
        return None
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "l":
        result = int(round(amount * 1000))
    elif unit == "cl":
        result = int(round(amount * 10))
    else:
        result = int(round(amount))
    assert result > 0, "parsed size must be positive"
    return result


async def _block_heavy_resources(route):
    """Route handler: drop images/media/fonts/stylesheets to save RAM."""
    assert route is not None, "route must be provided"
    resource_type = route.request.resource_type
    assert isinstance(resource_type, str), "resource type must be text"
    if resource_type in _HEAVY_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def new_stealth_context(pw):
    """Launch headless Chromium with the stealth flags from the old
    whiskybase.py scraper. Returns (browser, context); the caller closes
    the browser. Raises on launch failure (never silently continues)."""
    assert pw is not None, "playwright handle required"
    browser = await pw.chromium.launch(headless=True, args=list(STEALTH_ARGS))
    assert browser is not None, "browser launch failed"
    context = await browser.new_context(
        user_agent=USER_AGENT, viewport={"width": 1920, "height": 1080})
    assert context is not None, "browser context failed"
    await context.route("**/*", _block_heavy_resources)
    return browser, context


async def goto_with_retries(page, url, max_retries=MAX_RETRIES):
    """Navigate with bounded retries. Returns True only on HTTP < 400."""
    assert page is not None, "page required"
    assert isinstance(url, str) and url.startswith("http")
    assert 1 <= max_retries <= 5, "retries must be between 1 and 5"
    for _attempt in range(max_retries):
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        except Exception:
            response = None
        if response is not None and response.status < 400:
            return True
    assert max_retries >= 1, "at least one attempt must have run"
    return False
