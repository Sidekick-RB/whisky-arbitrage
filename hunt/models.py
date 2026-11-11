"""Data model for the whisky deal hunt.

Every price in this package is either verified or explicitly missing.
Nothing here invents a price: a missing size, a missing overseas price, or
an assumed value yields ``None`` and an "unverified" verdict downstream.
Thresholds are module constants (configuration, not mutable state).
"""

import re
from dataclasses import dataclass

# Qualifier test, from the watchlist (~/workspace/bottles/whisky-weekly-watchlist.md).
QUALIFIER_BAR_PCT = 25.0
CLOSE_FIND_FLOOR_PCT = 15.0
MIN_HUNT_PRICE_SGD = 100.0

VERDICTS = ("qualifier", "close-find", "not-a-deal", "unverified",
            "unavailable")

_FILLERS = ("single malt", "blended malt", "scotch whisky",
            "blended scotch whisky", "tennessee whiskey", "bourbon",
            "scotch", "whisky", "whiskey",
            "with gift box", "w/ gift box", "gift box")


def canonical_key(distillery, age_years, edition, is_willet_rye):
    """Build the match key for one bottling.

    Same-expression matching: distillery + age + edition. For Willet ryes
    the match criterion is age alone, so barrel and proof differences do
    not split the key; they are recorded as notes instead.
    """
    assert isinstance(distillery, str) and distillery.strip()
    assert age_years is None or age_years >= 0
    assert isinstance(edition, str)
    assert isinstance(is_willet_rye, bool)
    slug = distillery.strip().lower().replace(" ", "-")
    age_part = "nas" if age_years is None else str(age_years) + "yr"
    if is_willet_rye:
        key = "willet-rye-" + age_part
    else:
        key = slug + "-" + age_part
        edition_part = edition.strip().lower().replace(" ", "-")
        if edition_part:
            key = key + "-" + edition_part
    assert key, "canonical key must be non-empty"
    assert slug in key or key.startswith("willet-rye-")
    return key


def canonical_from_name(name):
    """Deterministic first-pass canonical key from a raw listing name.

    Lowercases, keeps the age as e.g. ``18yr``, drops the bottle size and
    filler words, keeps edition markers such as ``2020-release`` so an
    edition-specific winner never leaks into another edition. Ambiguous
    names are resolved later by the LLM name-cleaning slice, not here.
    """
    assert isinstance(name, str) and name.strip()
    text = name.strip().lower()
    if text.startswith("the "):
        text = text[4:]
    text = re.sub(r"(\d+)\s*yr\b", r"\1yr", text)
    text = re.sub(r"(\d+)\s*year\s*old", r"\1yr", text)
    text = re.sub(r"(\d+)\s*years?\b", r"\1yr", text)
    text = re.sub(r"\b\d+(\.\d+)?\s?(ml|l|cl)\b", " ", text)
    # ABV is not part of the expression identity: "46%" or "43.2%" would
    # otherwise split the key from overseas names that carry no ABV
    # (iShopChangi names always carry it, e.g. "700ML 46%").
    text = re.sub(r"\b\d+(?:\.\d+)?\s*%", " ", text)
    text = text.replace("w/", " ")
    for filler in _FILLERS:
        text = text.replace(filler, " ")
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    words = [word for word in text.split() if word]
    key = "-".join(words)
    assert key, "canonical key must be non-empty"
    assert "  " not in key and "--" not in key
    return key


def listing_key(listing):
    """Stable state key for one listing: retailer + canonical name +
    fulfilment mode. Two listings of the same bottle at different
    modes (iShopChangi traveller vs non-traveller, KrisShop flight vs
    home) are different listings and must both survive dedupe and
    state diffing, so the mode is part of the key."""
    assert listing.retailer, "retailer must be non-empty"
    assert listing.canonical, "canonical must be non-empty"
    mode = "tr" if listing.traveller_only else "nt"
    key = listing.retailer + "|" + listing.canonical + "|" + mode
    assert "|" in key and len(key) > 2
    return key


@dataclass(frozen=True)
class Listing:
    """One verified retailer listing; price_sgd is the shelf price as shown.

    size_ml None means the size was not confirmed on the page.
    size_verified False means the size was assumed (e.g. standard 700ml)
    and must not support a qualifier verdict.
    """

    retailer: str
    name: str
    canonical: str
    size_ml: int | None
    size_verified: bool
    price_sgd: float
    in_stock: bool
    traveller_only: bool
    eligibility: str
    edition: str
    batch: str
    url: str
    seen_at: str

    def __post_init__(self):
        assert self.retailer, "retailer must be non-empty"
        assert self.name, "name must be non-empty"
        assert self.canonical, "canonical must be non-empty"
        assert self.price_sgd > 0, "price must be positive"
        assert self.size_ml is None or self.size_ml > 0
        assert self.url.startswith("http"), "url must be absolute"
        assert isinstance(self.in_stock, bool)
        assert isinstance(self.size_verified, bool)
        assert isinstance(self.traveller_only, bool)


@dataclass(frozen=True)
class OverseasPrice:
    """One verified overseas retail price. Only in-stock prices count.

    name is the listing name as shown; run.py matches it back to the
    local listing by canonical key, so a price without a name cannot
    be paired and must not be used as a benchmark.
    """

    retailer: str
    country: str
    currency: str
    price: float
    size_ml: int
    in_stock: bool
    url: str
    name: str = ""

    def __post_init__(self):
        assert self.retailer, "retailer must be non-empty"
        assert self.country in ("UK", "US"), "country must be UK or US"
        assert self.currency in ("GBP", "USD"), "currency must be GBP/USD"
        assert self.price > 0, "price must be positive"
        assert self.size_ml > 0, "size must be positive"
        assert isinstance(self.in_stock, bool)
        assert isinstance(self.url, str) and self.url.startswith("http")
        assert isinstance(self.name, str), "name must be a string"


@dataclass(frozen=True)
class FxRates:
    """FX rates used for one run; stored with the report (savings move with FX)."""

    gbp_to_sgd: float
    usd_to_sgd: float
    source: str
    as_of: str

    def __post_init__(self):
        assert self.gbp_to_sgd > 0, "GBP rate must be positive"
        assert self.usd_to_sgd > 0, "USD rate must be positive"
        assert self.source, "FX source must be recorded"
        assert self.as_of, "FX timestamp must be recorded"


@dataclass(frozen=True)
class PromoCode:
    """A promo code as data only. Codes are never tested in a basket."""

    code: str
    kind: str
    value: float
    min_spend_sgd: float
    cap_sgd: float | None
    expires: str | None
    scope: str
    new_users_only: bool

    def __post_init__(self):
        assert self.code, "code must be non-empty"
        assert self.kind in ("percent", "fixed"), "kind must be percent/fixed"
        assert self.value >= 0, "value must be non-negative"
        assert self.min_spend_sgd >= 0, "min spend must be non-negative"
        assert self.cap_sgd is None or self.cap_sgd > 0
        assert isinstance(self.new_users_only, bool)


@dataclass(frozen=True)
class Comparison:
    """Result of the qualifier test for one local listing."""

    verdict: str
    saving_pct: float | None
    local_price_sgd: float
    local_per_litre: float | None
    overseas_per_litre_sgd: float | None
    overseas_best: OverseasPrice | None
    effective_price_sgd: float
    applied_code: str | None
    notes: tuple = ()

    def __post_init__(self):
        assert self.verdict in VERDICTS, "unknown verdict"
        assert self.local_price_sgd > 0, "local price must be positive"
        assert self.effective_price_sgd > 0, "effective price must be positive"
        assert self.effective_price_sgd <= self.local_price_sgd
        if self.verdict == "qualifier":
            assert self.saving_pct is not None
            assert self.saving_pct >= QUALIFIER_BAR_PCT
        if self.verdict == "close-find":
            assert self.saving_pct is not None
            assert CLOSE_FIND_FLOOR_PCT <= self.saving_pct < QUALIFIER_BAR_PCT
        if self.verdict in ("unverified", "unavailable"):
            assert self.saving_pct is None
            assert self.overseas_per_litre_sgd is None
        assert isinstance(self.notes, tuple)

    def to_dict(self):
        """JSON-safe form for the weekly report."""
        assert self.verdict in VERDICTS
        best = None
        if self.overseas_best is not None:
            best = {
                "retailer": self.overseas_best.retailer,
                "country": self.overseas_best.country,
                "currency": self.overseas_best.currency,
                "price": self.overseas_best.price,
                "size_ml": self.overseas_best.size_ml,
                "name": self.overseas_best.name,
                "url": self.overseas_best.url,
            }
        result = {
            "verdict": self.verdict,
            "saving_pct": self.saving_pct,
            "local_price_sgd": self.local_price_sgd,
            "local_per_litre_sgd": self.local_per_litre,
            "overseas_per_litre_sgd": self.overseas_per_litre_sgd,
            "overseas_best": best,
            "effective_price_sgd": self.effective_price_sgd,
            "applied_code": self.applied_code,
            "notes": list(self.notes),
        }
        assert set(result) == {
            "verdict", "saving_pct", "local_price_sgd",
            "local_per_litre_sgd", "overseas_per_litre_sgd",
            "overseas_best", "effective_price_sgd", "applied_code",
            "notes",
        }
        return result
