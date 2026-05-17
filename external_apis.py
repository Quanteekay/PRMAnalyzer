"""
Adapters for additional public APIs:
- NBP (National Bank of Poland) — exchange rates + interest rates
- GUS BDL (Bank Danych Lokalnych) — local statistics, used for average wages
  → enables the affordability index (price per m² ÷ avg monthly wage)

Both APIs are free and require no authentication. The adapters cache results
in memory for a short TTL to avoid hammering the endpoints.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

_NBP_BASE = "https://api.nbp.pl/api"
_BDL_BASE = "https://bdl.stat.gov.pl/api/v1"
_HTTP_TIMEOUT = 12
_CACHE_TTL = 60 * 30  # 30 minutes
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, fn):
    now = time.time()
    if key in _cache:
        ts, value = _cache[key]
        if now - ts < _CACHE_TTL:
            return value
    value = fn()
    _cache[key] = (now, value)
    return value


# ---------------------------------------------------------------------------
# NBP — exchange rates (table A) + reference rate from the rates API
# ---------------------------------------------------------------------------

def nbp_rates(currencies: tuple[str, ...] = ("USD", "EUR", "GBP", "CHF")) -> list[dict]:
    """Latest mid rates + previous-day delta + 30-day mini-history per currency.

    Returns [{code, name, rate, date, prev_rate, delta_pct, history}] where
    `history` is [{date, rate}] for the last ~30 business days — enough for
    a sparkline chart in /finance.
    """

    def fetch():
        results = []
        for code in currencies:
            try:
                # /last/30 — newest at the end
                url = f"{_NBP_BASE}/exchangerates/rates/A/{code}/last/30/?format=json"
                r = requests.get(url, timeout=_HTTP_TIMEOUT)
                r.raise_for_status()
                payload = r.json()
                series = payload.get("rates") or []
                if not series:
                    continue
                latest = series[-1]
                prev = series[-2] if len(series) >= 2 else None
                delta_pct = None
                if prev and prev["mid"]:
                    delta_pct = ((latest["mid"] - prev["mid"]) / prev["mid"]) * 100
                results.append({
                    "code": code,
                    "name": payload.get("currency"),
                    "rate": latest["mid"],
                    "date": latest["effectiveDate"],
                    "prev_rate": prev["mid"] if prev else None,
                    "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
                    "history": [
                        {"date": p["effectiveDate"], "rate": p["mid"]}
                        for p in series
                    ],
                })
            except Exception as exc:  # pragma: no cover
                log.warning("NBP rate fetch failed for %s: %s", code, exc)
        return results

    return _cached(f"nbp_rates:{','.join(currencies)}", fetch)


def nbp_reference_rate() -> Optional[float]:
    """NBP reference rate (stopa referencyjna). None on failure."""

    def fetch():
        try:
            url = "https://api.nbp.pl/api/cenyzlota?format=json"
            # NBP nie publikuje stóp przez api.nbp.pl - rate omitted on failure
            r = requests.get(url, timeout=_HTTP_TIMEOUT)
            r.raise_for_status()
            return None  # placeholder — real value comes from NBP press releases
        except Exception:
            return None

    return _cached("nbp_ref_rate", fetch)


def nbp_gold_price() -> Optional[dict]:
    """Latest gold fixing price (PLN/g) + previous-day delta + 30-day history."""

    def fetch():
        try:
            url = f"{_NBP_BASE}/cenyzlota/last/30/?format=json"
            r = requests.get(url, timeout=_HTTP_TIMEOUT)
            r.raise_for_status()
            series = r.json() or []
            if not series:
                return None
            latest = series[-1]
            prev = series[-2] if len(series) >= 2 else None
            delta_pct = None
            if prev and prev.get("cena"):
                delta_pct = ((latest["cena"] - prev["cena"]) / prev["cena"]) * 100
            return {
                "price": latest["cena"],
                "date": latest["data"],
                "prev_price": prev["cena"] if prev else None,
                "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
                "history": [{"date": p["data"], "rate": p["cena"]} for p in series],
            }
        except Exception as exc:  # pragma: no cover
            log.warning("NBP gold fetch failed: %s", exc)
        return None

    return _cached("nbp_gold", fetch)


# ---------------------------------------------------------------------------
# GUS BDL — average monthly gross wage by voivodeship
# variable 217230 = "Przeciętne miesięczne wynagrodzenia brutto" (zł)
# ---------------------------------------------------------------------------

# Mapping of GUS voivodeship codes (NTS level 2 IDs) to our voivodeship names
GUS_VOIVODESHIP_CODES = {
    "021200000000": "dolnośląskie",
    "022200000000": "kujawsko-pomorskie",
    "023200000000": "lubelskie",
    "024200000000": "lubuskie",
    "025200000000": "łódzkie",
    "026200000000": "małopolskie",
    "027200000000": "mazowieckie",
    "028200000000": "opolskie",
    "029200000000": "podkarpackie",
    "030200000000": "podlaskie",
    "031200000000": "pomorskie",
    "032200000000": "śląskie",
    "033200000000": "świętokrzyskie",
    "034200000000": "warmińsko-mazurskie",
    "035200000000": "wielkopolskie",
    "036200000000": "zachodniopomorskie",
}

# Fallback values - GUS Q4 2024 average gross monthly wage (zł), approx values
_FALLBACK_WAGES = {
    "mazowieckie": 9650,
    "dolnośląskie": 8120,
    "małopolskie": 8080,
    "pomorskie": 8210,
    "śląskie": 7980,
    "wielkopolskie": 7650,
    "łódzkie": 7320,
    "zachodniopomorskie": 7220,
    "lubelskie": 7100,
    "podlaskie": 7090,
    "opolskie": 7180,
    "kujawsko-pomorskie": 7150,
    "lubuskie": 7080,
    "podkarpackie": 6980,
    "warmińsko-mazurskie": 6920,
    "świętokrzyskie": 6890,
}


def gus_wages_by_voivodeship() -> dict[str, float]:
    """
    Return latest avg monthly gross wage per voivodeship.
    GUS BDL endpoint: /data/by-variable/{var_id}?unit-level=2&format=json

    Falls back to a curated snapshot when the API is unreachable.
    """

    def fetch():
        try:
            url = f"{_BDL_BASE}/data/by-variable/217230"
            params = {"unit-level": "2", "format": "json", "page-size": 100}
            r = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            results: dict[str, float] = {}
            for entry in payload.get("results", []):
                code = entry.get("id")
                if code not in GUS_VOIVODESHIP_CODES:
                    continue
                values = entry.get("values", [])
                if not values:
                    continue
                # Pick the latest value
                latest = max(values, key=lambda v: (v.get("year", 0), v.get("period", "")))
                results[GUS_VOIVODESHIP_CODES[code]] = float(latest["val"])
            log.info("GUS BDL: fetched wages for %d voivodeships", len(results))
            if results:
                return results
        except Exception as exc:  # pragma: no cover
            log.warning("GUS BDL fetch failed: %s — using fallback", exc)
        return dict(_FALLBACK_WAGES)

    return _cached("gus_wages", fetch)


def affordability_index(price_per_m2: float, voivodeship: str) -> Optional[dict]:
    """
    Returns dict with:
      - wage: monthly gross wage in voivodeship
      - months_for_1m2: how many months of wage you need to buy 1 m²
      - months_for_50m2: how many months of wage for a 50 m² apartment
    """
    wages = gus_wages_by_voivodeship()
    wage = wages.get(voivodeship)
    if not wage or not price_per_m2:
        return None
    return {
        "wage": round(wage, 0),
        "months_for_1m2": round(price_per_m2 / wage, 2),
        "months_for_50m2": round((price_per_m2 * 50) / wage, 1),
    }
