"""Scotch <46% ABV filter for the weekly pull (slice 17).

Marcel's rule (12 Nov 2026): no Scotch below 46% ABV in the winners
list, unless aged 21+ years or premium/collectible. The weekly pull
applies the same rule up front so the run spends no overseas search
budget, LLM name-cleaning calls, or comparison work on bottles that
can never qualify: they are dropped right after dedupe, before terms
are built, and the state diff reports them separately from genuine
delistings (``excluded_by_rule``), never as delisted.

Why the data is curated, not derived: the listing canonical has no
ABV — ``canonical_from_name`` strips it (``46%`` would otherwise
split the key from overseas names that carry no ABV). So ABV is
decided from curated knowledge: a brand-level set for distilleries
that bottle everything at 46% or more, a (brand, age) set for the
expressions that are 46% at a given age, explicit prefixes for the
46% and premium expressions, edition tokens that imply 46% or more,
and two vintage signals. Everything else that is Scotch and under
21 is excluded. Non-Scotch is never excluded: an unknown brand fails
open (stays in the pull), so a missed brand costs work, never a deal.

The sets were built against the slice-16 report (12 Nov 2026):
3,592 in-scope canonicals, 1,730 Scotch, 1,478 under 21/NAS. The
vintage YYYY/YYYY pattern (e.g. ``20142024``) was verified on that
data: 84 of 87 ABV-known canonicals are 46% or more, and the 3
exceptions are all 29-32 years old (kept by age anyway). The
``single-cask`` token: 16 of 17 ABV-known are 46% or more.
``cask-strength``: 24 of 24.
"""

import re

# Leading canonical tokens that identify a Scotch distillery, brand,
# blend, or independent bottler. Matched longest-first (3, then 2,
# then 1 hyphen-word). "old" alone is deliberately absent: Old
# Forester and Old Potrero are bourbon/rye. "jack" is absent: Jack
# Daniel's is Tennessee whiskey. "port" alone is absent: only Port
# Charlotte / Port Ellen / Port Askaig are Scotch.
SCOTCH_BRANDS = frozenset([
    "aberfeldy", "aberlour", "ancnoc", "arran", "ardmore", "ardbeg",
    "ardnamurchan", "auchentoshan", "auchroisk", "aultmore", "balblair",
    "ballindalloch", "balvenie", "benriach", "benrinnes", "benromach",
    "bladnoch", "bowmore", "brora", "bruichladdich", "bunnahabhain",
    "caperdonich", "cardhu", "clynelish", "cragganmore", "craigellachie",
    "daftmill", "dailuaine", "dalmore", "dalwhinnie", "deanston",
    "dufftown", "edradour", "fettercairn", "glenallachie", "glenburgie",
    "glendronach", "glenfiddich", "glengoyne", "glenkinchie", "glenlivet",
    "glenlossie", "glenmorangie", "glenrothes", "glenturret", "inchgower",
    "jura", "kilchoman", "kilkerran", "kingsbarns", "knockando",
    "lagavulin", "laphroaig", "ledaig", "lindores", "linkwood",
    "longmorn", "longrow", "macallan", "macduff", "mannochmore",
    "mortlach", "ncnean", "oban", "raasay", "scapa", "singleton",
    "speyburn", "springbank", "strathisla", "talisker", "tamdhu",
    "tamnavulin", "teaninich", "tobermory", "tomatin", "tomintoul",
    "torabhaig", "tullibardine", "wolfburn", "wildmoor", "smokehead",
    "scallywag", "epicurean", "ballechin", "octomore", "annandale",
    "holyrood", "1770", "cadenhead", "signatory", "wemyss", "adelphi",
    "blackadder", "ballantines", "provenance", "glencadam",
    "glenglassaugh", "lochlea", "samaroli", "dewars", "balmenach",
    "miltonduff", "glentauchers", "knockdhu", "tormore", "strathclyde",
    "north-british",
    "ben-nevis", "blair-athol", "dallas-dhu", "caol-ila", "glen-keith",
    "glen-moray", "glen-scotia", "glen-garioch", "glen-spey", "glen-elgin",
    "glen-grant", "highland-park", "highland-queen", "old-pulteney",
    "old-ballantruan", "port-charlotte", "port-ellen", "port-askaig",
    "isle-of-jura", "isle-of-raasay", "johnnie-walker", "chivas-regal",
    "royal-salute", "royal-brackla", "compass-box", "douglas-laing",
    "gordon-macphail", "berry-bros-rudd", "that-boutique-y",
    "hart-brothers", "murray-mcdavid", "duncan-taylor", "old-malt-cask",
    "black-bottle", "cutty-sark", "famous-grouse", "haig-club",
    "hankey-bannister", "king-robert", "monkey-shoulder", "sheep-dip",
    "vat-69", "white-horse", "100-pipers", "scottish-leader",
    "timorous-beastie", "rock-island", "big-peat", "campbeltown-loch",
    "loch-lomond", "inchmurrin", "cu-bocan", "islay-mist",
])

# Scotch brands whose whole core range is bottled at 46% or more
# (non-chill-filtered houses and cask-strength bottlers).
SCOTCH_46PLUS_BRANDS = frozenset([
    "ardbeg", "ardnamurchan", "arran", "balblair", "ballechin",
    "ben-nevis", "bladnoch", "bruichladdich", "bunnahabhain",
    "cadenhead", "clynelish", "craigellachie", "cu-bocan", "daftmill",
    "deanston", "douglas-laing", "glen-garioch", "glen-scotia",
    "glenallachie", "glencadam", "holyrood", "isle-of-raasay",
    "kilchoman", "kilkerran", "kingsbarns", "lindores", "loch-lomond",
    "lochlea", "longrow", "ncnean", "octomore", "port-charlotte",
    "springbank", "teaninich", "tobermory", "ledaig", "wolfburn",
    "1770", "aultmore", "annandale", "campbeltown-loch", "inchmurrin",
    "hazelburn", "big-peat", "rock-island", "scallywag",
    "timorous-beastie", "epicurean", "old-ballantruan", "old-malt-cask",
    "provenance", "that-boutique-y", "wemyss", "adelphi", "blackadder",
    "hart-brothers", "murray-mcdavid",
])

# (brand, age) pairs bottled at 46% or more. The age is the only
# thing that separates these from the same brand's 40-43% core
# (e.g. Fettercairn 12 is 40%, Fettercairn 16 is 46.4%).
SCOTCH_46PLUS_AGE = frozenset([
    ("fettercairn", 14), ("fettercairn", 16), ("fettercairn", 17),
    ("fettercairn", 18), ("fettercairn", 22),
    ("fettercairn", 24), ("fettercairn", 28),
    ("tomatin", 14), ("tomatin", 15), ("tomatin", 18),
    ("glendronach", 15), ("glendronach", 16), ("glendronach", 18),
    ("benriach", 20),
    ("old-pulteney", 15), ("old-pulteney", 17), ("old-pulteney", 18),
    ("speyburn", 18),
    ("glen-moray", 18),
    ("ancnoc", 16), ("ancnoc", 18),
    ("royal-brackla", 18),
    ("lagavulin", 8), ("lagavulin", 12),
    ("tamdhu", 15), ("tamdhu", 16), ("tamdhu", 18),
    ("tomintoul", 14),
    ("glenfarclas", 15),
    ("port-askaig", 15), ("port-askaig", 17),
    ("glenlivet", 19),
    ("glenturret", 15),
    ("jura", 19),
])

# Canonical prefixes known to be bottled at 46% or more (NAS
# expressions, where no age pair can carry the decision). Mostly
# travel-retail editions, cask-finish Collections, and limited
# cask-strength releases — each verified against the slice-16 data.
SCOTCH_46PLUS_PREFIXES = (
    "glenmorangie-quinta-ruban", "glenmorangie-nectar-dor",
    "glenmorangie-the-nectar-dor", "glenmorangie-signet",
    "glenmorangie-a-tale-of-the-forest",
    "fettercairn-warehouse-2",
    "tomatin-decades",
    "tomatin-10yr-manzanilla", "tomatin-12yr-amarone",
    "tomatin-12yr-barolo", "tomatin-12yr-marsala",
    "tomatin-17yr-pedro-ximenez",
    "tomintoul-15yr-madeira", "tomintoul-15yr-portwood",
    "tomintoul-16yr-sauternes", "tomintoul-17yr-px",
    "tomintoul-five-decades",
    "glendronach-traditionally-peated",
    "benriach-malting-season", "benriach-the-smoky-ten",
    "benriach-the-smoky-twelve", "benriach-10-ans-the-smoky-ten",
    "benriach-12-ans-the-smoky-twelve",
    "laphroaig-lore", "laphroaig-px-cask",
    "laphroaig-14yr-willem-dafoe", "laphroaig-tube",
    "ardmore-traditional-cask", "ardmore-triple-wood",
    "black-bottle-andean-oak", "black-bottle-captain-cask",
    "black-bottle-smoke-dagger",
    "glenglassaugh-revival", "glenglassaugh-torfa",
    "duncan-taylor-big-smoke",
    "compass-box-peat-monster", "compass-box-flaming-heart",
    "compass-box-nectarosity", "compass-box-juveniles",
    "compass-box-phenomenology", "compass-box-delilahs",
    "compass-box-double-single",
    "talisker-11yo",
    "aberlour-suthainn",
    "auchentoshan-blood-oak",
    "balvenie-12-yo-montilla", "balvenie-pedro-ximnez-cask",
    "balvenie-week-of-peat",
    "chivas-regal-18yo-pauillac",
    "dalmore-anthology-16yo",
    "dewars-double-double",
    "ancnoc-peatheart",
    "edradour-12yr-caledonia",
    "mortlach-nad", "mortlach-the-katana-edge",
    "port-askaig-nouvelle-vague",
    "tamdhu-year-of", "tamdhu-gran-reserva",
    "glen-grant-cask-haven", "glen-grant-exploration",
    "glenturret-10yr-peat-smoked",
    "glenfiddich-perpetual-collection-vat-3",
    "glengoyne-10yr-old-first-filled-edition",
    "glengoyne-15yr-old-px-cask-edition",
    "highland-park-sky-of-orkney",
    "jura-15yo-barrel",
    "linkwood-11yo-rechard",
    "mannochmore-12yo-1st-fill",
    "oban-11yr-the-soul-of-calypso",
    "royal-brackla-10yo-1st-fill",
    "tamnavulin-11yo-madeira",
)

# Canonical prefixes considered premium/collectible: flagship luxury
# expressions, limited editions, and closed-distillery bottlings.
# Matched by plain prefix so edition and gift-set suffixes match too.
SCOTCH_PREMIUM_PREFIXES = (
    "macallan-time-space", "macallan-classic-cut", "macallan-rare-cask",
    "macallan-m", "macallan-reflexion", "macallan-no-6",
    "macallan-harmony", "macallan-the-harmony",
    "macallan-a-night-on-earth", "macallan-art-is-the-flower",
    "macallan-distil-your-world", "macallan-exploration-series",
    "macallan-home-collection", "macallan-james-bond", "macallan-litha",
    "macallan-sir-peter-blake", "macallan-estate", "macallan-edition",
    "johnnie-walker-blue", "johnnie-walker-king-george-v",
    "chivas-regal-ultis",
    "royal-salute",
    "dalmore-king-alexander", "dalmore-highland-king-alexander",
    "dalmore-cigar-malt", "dalmore-highland-cigar-malt",
    "dalmore-highland-cigar-ma", "dalmore-luminary",
    "dalmore-portfolio-series", "dalmore-quintessence",
    "bowmore-vault",
    "brora", "port-ellen", "caperdonich",
    "wildmoor", "samaroli",
    "compass-box-artist", "compass-box-brulee-royale",
    "glenmorangie-harrison-ford",
    "glendronach-house-editions", "glendronach-ode-to-the",
)

# (brand, age) pairs that are premium/collectible despite a sub-46%
# ABV. Macallan 18 Sherry Oak is 43% but is the range's flagship
# collectible annual release.
SCOTCH_PREMIUM_AGE = frozenset([
    ("macallan", 18),
])

# Edition words that imply 46% or more, matched on hyphen-word
# boundaries. "single-cask" is in because independent single casks
# in the slice-16 data are 46% or more 16 times out of 17 known.
SCOTCH_46PLUS_TOKENS = frozenset([
    "cask-strength", "un-chillfiltered", "non-chillfiltered",
    "non-chill-filtered", "nonchill-filtered", "unchillfiltered",
    "abunadh", "nadurra",
    "105", "artist", "collective", "foundations", "single-cask",
    "quarter-cask", "wee-beastie", "uigeadail", "corryvreckan",
    "classic-laddie", "machir-bay", "sanaig",
])

# Age spellings in canonicals: "18yr", "18-yo", "18-yrs", "13yo",
# "22-ans" (LMDW French), "30yearold". The hyphen between the number
# and the suffix is optional because canonical_from_name keeps "18 yr"
# as "18yr" but "18 YO" as "18-yo".
_AGE_RE = re.compile(r"(?:^|-)(\d+)-?(?:yearold|yrs|yr|yo|ans)(?:-|$)")
# Two consecutive vintage years, e.g. "20142024" from "2014/2025":
# an LMDW-style single-cask bottling, almost always cask strength.
_VINTAGE_PAIR_RE = re.compile(r"(?:^|-)((?:19|20)\d{2})((?:19|20)\d{2})(?:-|$)")
# One old vintage year, 1900-2020: a vintage-dated Scotch is a
# limited single-cask or old vintage release, i.e. collectible.
# Years 2021-2026 are as likely release years ("macallan-2022-
# classic-cut", "hedonism-2026") as vintages, so the cutoff sits
# at 2020.
_VINTAGE_YEAR_RE = re.compile(r"(?:^|-)(19\d{2}|200\d|201\d|2020)(?:-|$)")
# "110-proof" is 55% ABV; 92 proof is 46%, so 92 is the bar.
_PROOF_RE = re.compile(r"(?:^|-)(\d+)-proof(?:-|$)")
_MIN_PROOF_FOR_46 = 92


def brand_of(canonical):
    """Longest leading token (up to 3 hyphen-words) in SCOTCH_BRANDS,
    or None when the canonical is not a known Scotch brand."""
    assert isinstance(canonical, str) and canonical
    words = canonical.split("-")
    for size in (3, 2, 1):
        if len(words) >= size:
            token = "-".join(words[:size])
            if token in SCOTCH_BRANDS:
                return token
    return None


def is_scotch(canonical):
    """Whether the canonical is a known Scotch brand."""
    assert isinstance(canonical, str) and canonical
    return brand_of(canonical) is not None


def age_years(canonical):
    """Age statement from the canonical, or None for NAS.

    Handles "18yr", "18-yo", "18-yrs", "13yo", "22-ans" and
    "30yearold". A bare year ("1998", "2022") is not an age.
    """
    assert isinstance(canonical, str) and canonical
    match = _AGE_RE.search(canonical)
    if match is None:
        return None
    age = int(match.group(1))
    assert age >= 0
    return age


def _has_token(canonical, token):
    """Whether token appears as a whole hyphen-word in the canonical."""
    assert isinstance(canonical, str) and canonical
    assert isinstance(token, str) and token
    padded = "-" + canonical + "-"
    return ("-" + token + "-") in padded


def _proof_at_46(canonical):
    """Whether a "-NNN-proof" token in the canonical is 46% or more."""
    assert isinstance(canonical, str) and canonical
    match = _PROOF_RE.search(canonical)
    if match is None:
        return False
    proof = int(match.group(1))
    assert proof > 0
    return proof >= _MIN_PROOF_FOR_46


def _has_vintage_pair(canonical):
    """Whether the canonical carries two consecutive vintage years."""
    assert isinstance(canonical, str) and canonical
    return _VINTAGE_PAIR_RE.search(canonical) is not None


def _has_old_vintage_year(canonical):
    """Whether the canonical carries one vintage year, 1900-2020."""
    assert isinstance(canonical, str) and canonical
    return _VINTAGE_YEAR_RE.search(canonical) is not None


# Canonical prefixes KNOWN to be below 46% even though a brand,
# token, or age-pair rule would keep them (e.g. Arran is a 46%+
# brand, but The Barrel Reserve is 43%). Checked after the age
# and premium exceptions, before the 46+ keep, so it corrects a
# wrong keep without overriding Marcel's 21+ or premium rules.
SCOTCH_SUB46_PREFIXES = (
    "arran-the-barrel-reserve",
    "cadenhead-enigma-irish",
    "glencadam-american-oak-reserve",
    "glencadam-the-rather-elegant",
)


def _has_sub46_prefix(canonical):
    """Whether the canonical is a known sub-46% exception."""
    assert isinstance(canonical, str) and canonical
    for prefix in SCOTCH_SUB46_PREFIXES:
        if canonical.startswith(prefix):
            return True
    return False


def is_46_plus(canonical):
    """Whether the canonical is known to be bottled at 46% or more."""
    assert isinstance(canonical, str) and canonical
    brand = brand_of(canonical)
    if brand is not None and brand in SCOTCH_46PLUS_BRANDS:
        return True
    age = age_years(canonical)
    if brand is not None and age is not None \
            and (brand, age) in SCOTCH_46PLUS_AGE:
        return True
    for prefix in SCOTCH_46PLUS_PREFIXES:
        if canonical.startswith(prefix):
            return True
    for token in SCOTCH_46PLUS_TOKENS:
        if _has_token(canonical, token):
            return True
    if _proof_at_46(canonical):
        return True
    if _has_vintage_pair(canonical):
        return True
    return False


def is_premium(canonical):
    """Whether the canonical is premium/collectible despite a sub-46%
    ABV: a flagship or limited edition, a closed distillery, or an
    old vintage-dated release."""
    assert isinstance(canonical, str) and canonical
    brand = brand_of(canonical)
    age = age_years(canonical)
    if brand is not None and age is not None \
            and (brand, age) in SCOTCH_PREMIUM_AGE:
        return True
    for prefix in SCOTCH_PREMIUM_PREFIXES:
        if canonical.startswith(prefix):
            return True
    if _has_old_vintage_year(canonical):
        return True
    return False


def excluded_by_abv_rule(canonical):
    """Whether the weekly pull drops this canonical: Scotch, under
    21 years (or NAS), below 46% ABV, and not premium/collectible.
    Non-Scotch and unknown brands are never excluded."""
    assert isinstance(canonical, str) and canonical
    brand = brand_of(canonical)
    if brand is None:
        return False
    age = age_years(canonical)
    if age is not None and age >= 21:
        return False
    if is_premium(canonical):
        return False
    if _has_sub46_prefix(canonical):
        return True
    if is_46_plus(canonical):
        return False
    return True
