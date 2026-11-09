# whisky-arbitrage

Two things live in this repo. The old Flask prototype (`app.py`,
`scraper.py`, `whiskybase.py`, `agents.py`) scraped The Whisky Exchange
and compared prices against Whiskybase market values; it is kept intact
for reference. The active work is the weekly deal hunt in `hunt/`.

## Whisky deal hunt (`hunt/`)

Weekly automated version of the manual hunt (methodology in
`~/workspace/bottles/whisky-weekly-watchlist.md`). It scrapes the tracked
Singapore retailers in full, compares each listing against verified
in-stock overseas retail prices, and writes a JSON report for Muse to
verify and report on. It never purchases, never adds to cart, and never
tests a promo code in a basket.

Qualifier test (exact): same expression only (same age, same edition;
Willet ryes match on age alone, proof stated not disqualifying), per-litre
price roughly 25% or more below the cheaper verified in-stock overseas
price (US: ReserveBar / Total Wine / Wine.com; UK: The Whisky Exchange /
Master of Malt / The Whisky World). Missing or assumed data stays
unverified — the engine never fabricates a price.

### Layout

- `hunt/models.py` — data model (Listing, OverseasPrice, FxRates,
  PromoCode, Comparison) and canonical-name matching.
- `hunt/engine.py` — the qualifier test: per-litre math, cheapest
  in-stock overseas price, promo-code application as data.
- `hunt/fx.py` — FX fetch (stored with each report; savings move with FX).
- `hunt/report.py` — report schema v1, state diffing (price/stock
  changes, delistings, new listings), parser health, file IO.
- `hunt/names.py` — Groq name-cleaning (stdlib urllib only; model
  openai/gpt-oss-120b; key from GROQ_API_KEY env var only; any failure
  returns None, never a fabricated key).
- `hunt/parsers/base.py` — shared helpers: price/size parsing, stealth
  browser context, bounded retries, bot-wall detection, JSON/text fetch.
- `hunt/parsers/paneco.py` — reference parser: Paneco sale page, full
  pagination. Other retailers follow this shape.
- `hunt/parsers/cellarbration.py` — Cellarbration (Magento HTML tiles).
- `hunt/parsers/shopify.py` — shared Shopify products.json helper
  (whisky-title filter: keywords, age statement, distillery names).
- `hunt/parsers/tlc.py`, `spirited.py`, `tipsy.py`, `asher.py`,
  `pivene.py` — the five Shopify SG retailers.
- `hunt/parsers/lmdw.py` — LMDW (WooCommerce Store API; prices in
  minor units, size from the Volume attribute).
- `hunt/parsers/krisshop.py` — KrisShop (Adobe Commerce GraphQL;
  traveller/flight prices; home-delivery variant not returned).
- `hunt/parsers/twe.py` — The Whisky Exchange (UK benchmark).
- `hunt/parsers/whiskyworld.py` — The Whisky World (UK benchmark;
  size from the product page).
- `hunt/parsers/reservebar.py` — ReserveBar (US benchmark; sitemap +
  product-page JSON-LD).
- `hunt/parsers/blocked.py` — shared bot-wall probe; `masterofmalt.py`,
  `winecom.py`, `totalwine.py`, `maltwineasia.py` report the wall only,
  never a price.
- `hunt/run.py` — CLI runner (slice 2: all SG retailers + overseas
  comparisons; see below).
- `hunt/requirements.txt` — Playwright + stealth only; the engine is
  standard library. The old root `requirements.txt` is UTF-16/CRLF and
  pins the Flask prototype's stack; it is not used by the hunt job.

### Slice 2 — what the runner does

`hunt/run.py` scrapes every SG retailer, dedupes by retailer+canonical,
and compares every listing with `hunt/engine.py`:

- Candidates for an overseas search: in stock, S$100+, verified size.
  Distinct search terms are capped (`--max-overseas-queries`, default
  60; terms shared by the most listings go first, the rest are noted
  as skipped, never silently dropped).
- Overseas benchmarks per term: TWE, The Whisky World, ReserveBar
  (sitemap loaded once per run). Blocked retailers (Master of Malt,
  Wine.com, Total Wine) and Malt & Wine Asia are probed once per run
  and reported as blocked.
- Matching: exact canonical match first; Groq `clean_to_key` only on
  a miss, bounded (`--max-groq-calls`, default 100) and cached by
  name. Without GROQ_API_KEY, ambiguous matches stay unverified.
- Promo codes are not applied in this slice; effective price is the
  shelf price. iShopChangi is not scraped (see below).

```sh
.venv/bin/python -m hunt.run --out hunt-report.json --state hunt-state.json
```

`--fx-gbp` / `--fx-usd` override the live FX fetch. The run exits 2
when FX is unavailable and no overrides are given. State and report
files are gitignored.

### Retailer status (slice 2, verified 9 Nov 2026)

| Retailer | Source | Status |
| --- | --- | --- |
| Paneco | HTML sale page | Done |
| Cellarbration | Magento HTML tiles | Done |
| The Liquid Collection | Shopify products.json (whisky + bourbon feeds) | Done |
| Spirited | Shopify products.json (all feed) | Done — no size anywhere, so all listings are unverified on size |
| Tipsy Topsy | Shopify products.json (all feed; the whisky-collection feed omits sold-out items) | Done |
| Asher | Shopify products.json (all feed, 4000+ products) | Done |
| PIVENE | Shopify products.json (all feed, 1158 products; wine-style titles caught by the distillery-name rule) | Done |
| LMDW | WooCommerce Store API | Done |
| KrisShop | Adobe Commerce GraphQL | Done — traveller/flight prices; home-delivery variant not returned |
| iShopChangi | AEM search API | **Not done — browser-verified-only.** The search endpoint answers with a session cookie but returns no prices and ignores the search term; the traveller range needs a flight-linked session. |
| The Whisky Exchange | Search HTML | Done (synthetic fixture; live site is Cloudflare-challenged to curl, driver reports blocked) |
| The Whisky World | Search HTML + product page (size) | Done |
| ReserveBar | Sitemap + product-page JSON-LD | Done |
| Master of Malt | — | Bot-wall only (Vercel checkpoint) |
| Wine.com | — | Bot-wall only (DataDome) |
| Total Wine | — | Bot-wall only (PerimeterX) |
| Malt & Wine Asia | — | Bot-wall only (Cloudflare); its three watchlist bottles stay unverified |

Two corrections found while building slice 2 (both verified live, not
assumed): TLC's Blanton's Straight From The Barrel (~S$198) is gone
from TLC's complete bourbon feed — only Original S$125 and Gold S$178
remain, so the S$198 price is delisted, not current. And
`canonical_from_name` now normalises "21 Years" → "21yr" and drops
bare "whisky"/"whiskey" as fillers, so Asher/PIVENE titles match
overseas names exactly more often.

### Run the tests

```sh
.venv/bin/pip install pyflakes
.venv/bin/python -m py_compile hunt/*.py hunt/parsers/*.py tests/*.py
.venv/bin/python -m pyflakes hunt tests
.venv/bin/python -m unittest discover -s tests -v
```

Engine cases come from the real watchlist: Royal Brackla 18, Redbreast 21
(the 22.6% figure was the wrong benchmark — the engine must return
~16%), Glenfarclas 25 (27.6% below UK but above US, so not a qualifier),
Glenfarclas 21 delisting, and the fabricated-price guard (missing data
yields "unverified", never a number).

Live parser test (read-only, 2 pages of the Paneco sale page):

```sh
HUNT_LIVE=1 .venv/bin/python -m unittest tests.test_paneco_live -v
```

It needs direct internet: headless Chromium cannot egress through an
authenticated proxy that drops unauthenticated CONNECTs, so in this
sandbox the real-site test fails even though the same code works with
direct internet (e.g. AWS Fargate). The local fixture-server test in
the same file always runs and covers the driver end to end offline.

### AWS (target; not yet wired)

Docker image → ECR, built by `.github/workflows/build-push-ecr.yml` on
push to `main` (needs repo secrets `AWS_ROLE_TO_ASSUME`,
`AWS_ACCOUNT_ID` and variables `AWS_REGION`, `ECR_REPOSITORY`).
Planned runtime: ECS Fargate scheduled task, Mondays ~09:00 SGT,
Versiform AWS account, ap-southeast-1; state and reports in a versioned
S3 bucket; Groq API key in Secrets Manager. See
`~/workspace/bottles/whisky-scraper-plan.md`.

### Coding standard

JPL Power of Ten, Python adaptation (see `~/AGENTS.md`): simple control
flow, bounded loops, functions under ~60 lines, at least two runtime
assertions per function, no globals, every return value checked,
py_compile + pyflakes clean before push.
