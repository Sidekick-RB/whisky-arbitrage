"""Comparison engine: the qualifier test, implemented exactly.

Rules (from the watchlist):
- Same expression only (same age, same edition). For Willet ryes the
  match criterion is age alone; proof differences are stated, never
  disqualifiers.
- Per-litre price roughly 25% or more below the cheaper verified IN-STOCK
  overseas price. Out-of-stock overseas prices never count.
- Traveller-only bottles are in scope; eligibility is carried in notes.
- Promo codes are applied as data only, never tested in a basket.
- Anything unverified stays unverified: missing size, assumed size, or no
  in-stock overseas price all yield verdict "unverified", never a number.
"""

from .models import (
    CLOSE_FIND_FLOOR_PCT,
    MIN_HUNT_PRICE_SGD,
    QUALIFIER_BAR_PCT,
    Comparison,
)

MAX_OVERSEAS_PRICES = 12
MAX_PROMO_CODES = 12


def per_litre(price, size_ml):
    """Price per litre, or None when the size is unknown (never guessed)."""
    assert isinstance(price, (int, float)) and price > 0
    assert size_ml is None or size_ml > 0
    if size_ml is None:
        return None
    result = price / (size_ml / 1000.0)
    assert result > 0, "per-litre price must be positive"
    return result


def overseas_per_litre_sgd(overseas, fx):
    """One overseas price converted to SGD per litre, or None if unusable."""
    assert overseas.price > 0 and overseas.size_ml > 0
    assert fx.gbp_to_sgd > 0 and fx.usd_to_sgd > 0
    rate = fx.gbp_to_sgd if overseas.currency == "GBP" else fx.usd_to_sgd
    assert rate > 0, "FX rate must be positive"
    result = per_litre(overseas.price * rate, overseas.size_ml)
    assert result is None or result > 0
    return result


def cheaper_overseas(prices, fx):
    """Cheapest verified in-stock overseas price.

    Returns (price, per-litre SGD) or (None, None) when no in-stock price
    was verified. Averages, RRSPs and out-of-stock listings never count.
    """
    assert isinstance(prices, (list, tuple))
    assert len(prices) <= MAX_OVERSEAS_PRICES, "too many overseas prices"
    assert fx.gbp_to_sgd > 0 and fx.usd_to_sgd > 0
    best = None
    best_per_litre = None
    for overseas in prices:
        if not overseas.in_stock:
            continue
        per_litre_value = overseas_per_litre_sgd(overseas, fx)
        if per_litre_value is None:
            continue
        if best_per_litre is None or per_litre_value < best_per_litre:
            best = overseas
            best_per_litre = per_litre_value
    assert best is None or best_per_litre is not None
    assert best is None or best.in_stock
    return best, best_per_litre


def apply_promo(price_sgd, code, as_of, is_new_user):
    """Apply one promo code as data. Returns (new price, applied?).

    A code applies only when the spend meets its minimum, it has not
    expired, and a new-user-only code is only used for a new user.
    Percent discounts are capped; fixed discounts never go below zero.
    """
    assert price_sgd > 0, "price must be positive"
    assert isinstance(as_of, str) and as_of, "as-of date required"
    assert isinstance(is_new_user, bool)
    if price_sgd < code.min_spend_sgd:
        return price_sgd, False
    if code.expires is not None and as_of > code.expires:
        return price_sgd, False
    if code.new_users_only and not is_new_user:
        return price_sgd, False
    if code.kind == "percent":
        discount = price_sgd * code.value / 100.0
    else:
        discount = code.value
    if code.cap_sgd is not None and discount > code.cap_sgd:
        discount = code.cap_sgd
    if discount > price_sgd:
        discount = price_sgd
    result = round(price_sgd - discount, 2)
    assert 0 < result <= price_sgd, "discounted price out of range"
    return result, True


def best_promo(price_sgd, codes, as_of, is_new_user):
    """Best single promo code for a price. Returns (price, code or None).

    One code per order (stacking unverified). No code is ever basket-tested.
    """
    assert price_sgd > 0, "price must be positive"
    assert len(codes) <= MAX_PROMO_CODES, "too many promo codes"
    assert isinstance(as_of, str) and as_of, "as-of date required"
    best_price = price_sgd
    best_code = None
    for code in codes:
        candidate, applied = apply_promo(price_sgd, code, as_of, is_new_user)
        if applied and candidate < best_price:
            best_price = candidate
            best_code = code.code
    assert best_price <= price_sgd, "promo must not raise the price"
    assert best_code is None or isinstance(best_code, str)
    return best_price, best_code


def _saving_vs_overseas(local, overseas, fx):
    """Saving of a local listing vs the cheapest in-stock overseas price.

    Returns (saving_pct, best_price, best_per_litre_sgd), or
    (None, None, None) when no verified in-stock overseas price exists.
    Averages, RRSPs and out-of-stock prices never count as a benchmark.
    """
    assert local.size_ml is not None and local.size_ml > 0
    assert isinstance(overseas, (list, tuple))
    assert fx.gbp_to_sgd > 0 and fx.usd_to_sgd > 0
    best, best_per_litre = cheaper_overseas(overseas, fx)
    assert best is None or best.in_stock, "benchmark must be in stock"
    assert best_per_litre is None or best_per_litre > 0
    if best is None:
        return None, None, None
    local_pl = per_litre(local.price_sgd, local.size_ml)
    assert local_pl is not None and local_pl > 0, "local per-litre known"
    saving = (best_per_litre - local_pl) / best_per_litre * 100.0
    # Saving is unbounded below: a local bottle more than twice as dear
    # per litre as the overseas benchmark scores below -100%. That is
    # real data (a steep local markup), never an error.
    return saving, best, best_per_litre


def _result(verdict, local, saving, local_per_litre, best,
            best_per_litre, effective, applied_code, notes):
    """Build one Comparison from compare()'s decided values."""
    assert verdict in ("qualifier", "close-find", "not-a-deal",
                       "unverified", "unavailable")
    assert local.price_sgd > 0, "local price must be positive"
    assert saving is None or saving < 100.0, "saving below 100%"
    result = Comparison(
        verdict=verdict, saving_pct=saving,
        local_price_sgd=local.price_sgd, local_per_litre=local_per_litre,
        overseas_per_litre_sgd=best_per_litre, overseas_best=best,
        effective_price_sgd=effective, applied_code=applied_code,
        notes=tuple(notes))
    assert result.verdict == verdict, "result must carry the verdict"
    return result


def compare(local, overseas, fx, codes=(), as_of="", is_new_user=False,
            proof_note=""):
    """Run the qualifier test for one local listing.

    overseas: same-expression overseas prices (the caller pairs them by
    canonical key; for Willet ryes the key is age alone). as_of is the
    run date (YYYY-MM-DD) used for promo expiry checks.
    """
    assert local.price_sgd > 0, "local price must be positive"
    assert isinstance(overseas, (list, tuple))
    assert len(overseas) <= MAX_OVERSEAS_PRICES
    assert fx.gbp_to_sgd > 0 and fx.usd_to_sgd > 0
    assert isinstance(as_of, str) and as_of, "as-of date required"
    effective, applied_code = best_promo(
        local.price_sgd, codes, as_of, is_new_user)
    notes = []
    if local.traveller_only:
        notes.append("traveller-only: " + local.eligibility)
    if local.edition:
        notes.append("edition: " + local.edition)
    if proof_note:
        notes.append(proof_note)
    if not local.in_stock:
        return _result("unavailable", local, None, None, None, None,
                       effective, applied_code, notes + ["out of stock"])
    if local.price_sgd < MIN_HUNT_PRICE_SGD:
        return _result("not-a-deal", local, None, None, None, None,
                       effective, applied_code,
                       notes + ["below S$100 hunt floor"])
    if local.size_ml is None or not local.size_verified:
        note = "size not verified" if local.size_ml is None else "size assumed"
        return _result("unverified", local, None, None, None, None,
                       effective, applied_code, notes + [note])
    saving, best, best_per_litre = _saving_vs_overseas(local, overseas, fx)
    if best is None:
        local_pl = per_litre(local.price_sgd, local.size_ml)
        assert local_pl is not None and local_pl > 0
        return _result("unverified", local, None, local_pl, None, None,
                       effective, applied_code,
                       notes + ["no verified in-stock overseas price"])
    local_pl = per_litre(local.price_sgd, local.size_ml)
    assert local_pl is not None and local_pl > 0
    assert saving is not None and best_per_litre is not None
    if saving >= QUALIFIER_BAR_PCT:
        verdict = "qualifier"
    elif saving >= CLOSE_FIND_FLOOR_PCT:
        verdict = "close-find"
    else:
        verdict = "not-a-deal"
    return _result(verdict, local, round(saving, 1), round(local_pl, 2),
                   best, round(best_per_litre, 2), effective,
                   applied_code, notes)
