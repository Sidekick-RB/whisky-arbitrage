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
- `hunt/parsers/base.py` — shared helpers: price/size parsing, stealth
  browser context, bounded retries.
- `hunt/parsers/paneco.py` — reference parser: Paneco sale page, full
  pagination. Other retailers follow this shape.
- `hunt/run.py` — CLI runner (slice 1: Paneco only).
- `hunt/requirements.txt` — Playwright + stealth only; the engine is
  standard library. The old root `requirements.txt` is UTF-16/CRLF and
  pins the Flask prototype's stack; it is not used by the hunt job.

### Run locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r hunt/requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m hunt.run --out hunt-report.json --state hunt-state.json
```

`--fx-gbp` / `--fx-usd` override the live FX fetch. The run exits 2
when FX is unavailable and no overrides are given. State and report
files are gitignored.

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
