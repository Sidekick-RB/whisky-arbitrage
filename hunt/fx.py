"""FX rates for one run.

Rates are fetched at run time and stored with the report, because savings
are FX-sensitive (see the Hibiki 30 borderline case). A failed fetch
returns None: the caller must fall back to explicit --fx overrides or
abort the run. A rate is never guessed or carried silently.
"""

import json
import urllib.request
from datetime import datetime, timezone

from .models import FxRates

FX_URL = "https://open.er-api.com/v6/latest/SGD"
FX_TIMEOUT_S = 15
MAX_BODY_BYTES = 65536


def fixed_rates(gbp_to_sgd, usd_to_sgd, source, as_of):
    """Build FxRates from explicit values (CLI overrides, tests)."""
    assert gbp_to_sgd > 0, "GBP rate must be positive"
    assert usd_to_sgd > 0, "USD rate must be positive"
    assert source, "FX source must be recorded"
    assert as_of, "FX timestamp must be recorded"
    rates = FxRates(gbp_to_sgd=gbp_to_sgd, usd_to_sgd=usd_to_sgd,
                    source=source, as_of=as_of)
    assert rates.gbp_to_sgd == gbp_to_sgd
    assert rates.usd_to_sgd == usd_to_sgd
    return rates


def fetch_rates():
    """Fetch live SGD rates. Returns FxRates or None on any failure."""
    assert FX_URL.startswith("https://"), "FX URL must be HTTPS"
    assert FX_TIMEOUT_S > 0, "timeout must be positive"
    try:
        request = urllib.request.Request(FX_URL, method="GET")
        assert request.full_url == FX_URL
        with urllib.request.urlopen(request, timeout=FX_TIMEOUT_S) as resp:
            assert resp.status == 200, "FX endpoint did not return 200"
            body = resp.read(MAX_BODY_BYTES)
        assert body, "FX response body must be non-empty"
        data = json.loads(body.decode("utf-8"))
        assert isinstance(data, dict), "FX response must be a JSON object"
        rates = data.get("rates")
        assert isinstance(rates, dict), "FX response must carry rates"
        gbp_per_sgd = rates.get("GBP")
        usd_per_sgd = rates.get("USD")
        assert gbp_per_sgd and gbp_per_sgd > 0, "GBP rate missing"
        assert usd_per_sgd and usd_per_sgd > 0, "USD rate missing"
        result = FxRates(
            gbp_to_sgd=round(1.0 / gbp_per_sgd, 4),
            usd_to_sgd=round(1.0 / usd_per_sgd, 4),
            source="open.er-api.com",
            as_of=datetime.now(timezone.utc).isoformat(),
        )
        assert result.gbp_to_sgd > 0 and result.usd_to_sgd > 0
        return result
    except Exception:
        return None
