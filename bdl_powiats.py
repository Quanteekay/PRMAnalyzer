"""
GUS BDL fetcher for per-powiat housing indicators.

BDL (Bank Danych Lokalnych) hosts ~380 powiats (NTS level 5). We:
1. fetch the catalog (id + name + parent voivodeship)
2. fetch selected housing variables for all powiats in one call each
3. merge into a single dict keyed by BDL unit-id (12-char TERYT-aligned)

BDL public API rate limit is 60 req/min without a key. The fetcher
paginates politely with a tiny sleep between pages.
"""

from __future__ import annotations

import logging
import os
import time

import requests

_BDL_BASE = "https://bdl.stat.gov.pl/api/v1"
_HTTP_TIMEOUT = 15
_PAGE_SIZE = 100

log = logging.getLogger(__name__)


def _api_headers() -> dict:
    """Inject BDL API key if BDL_API_KEY env var is set.

    Without a key, BDL caps unauthenticated traffic at 1000 req/12h. With
    a registered key (free at bdl.stat.gov.pl) the limit jumps to 5000–50000
    per day depending on tier — needed to discover proper variable IDs.
    """
    key = os.environ.get("BDL_API_KEY")
    return {"X-ClientId": key} if key else {}


# BDL variable IDs. The list is intentionally short until we can verify ID
# meanings against the live catalog (each fetch confirms via measureUnitName).
# 60559 was originally labelled 'permits' but inspection showed it is actually
# "ludność na 1 km²" (population density). Keeping it as a useful per-powiat
# proxy indicator. Future iterations: register a BDL key and pull genuine
# housing variables (Mieszkania oddane / Pozwolenia na budowę mieszkań).
BDL_VARS = {
    "population_density": 60559,    # Ludność na 1 km² (os/km²)
}


def bdl_id_to_teryt(bdl_id: str) -> str | None:
    """Convert BDL 12-char unit-id to 4-char TERYT code.

    Empirically verified: BDL[2:4] = TERYT województwo (2 digits),
    BDL[7:9] = TERYT powiat (2 digits, ≥60 for grodzkie).
    Examples:
        011212001000 (Bocheński)         → 1201
        011212161000 (m. Kraków)         → 1261
        071412865000 (m. st. Warszawa)   → 1465
        042214361000 (m. Gdańsk)         → 2261
    """
    if not bdl_id or len(bdl_id) < 9:
        return None
    return bdl_id[2:4] + bdl_id[7:9]


def _fetch_voivodeship_map() -> dict[str, str]:
    """Live-fetch BDL voivodeship IDs (level=2). Returns {4-char prefix: name}.

    The 4-char prefix of a BDL unit-id is shared between a voivodeship and all
    its descendant powiats (we verified empirically against the BDL response),
    so we use it as the join key — no need to walk the parent chain.
    """
    try:
        r = requests.get(
            f"{_BDL_BASE}/units",
            params={"level": 2, "page-size": 100, "format": "json"},
            headers=_api_headers(),
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:  # pragma: no cover - network
        log.warning("BDL: voivodeship map fetch failed: %s", exc)
        return {}

    out: dict[str, str] = {}
    for entry in payload.get("results", []):
        unit_id = entry.get("id", "")
        name = (entry.get("name") or "").strip().lower()
        if len(unit_id) >= 4 and name:
            out[unit_id[:4]] = name
    log.info("BDL: voivodeship map built from %d entries", len(out))
    return out


def fetch_powiat_catalog() -> list[dict]:
    """Return [{'teryt_code', 'name', 'voivodeship'}] for every BDL powiat.

    BDL: level=5 = powiat. Roughly 380 entries.
    Empty list on total failure (caller decides whether to fall back).
    """
    voiv_map = _fetch_voivodeship_map()
    if not voiv_map:
        return []

    powiats: list[dict] = []
    page = 0
    while True:
        try:
            r = requests.get(
                f"{_BDL_BASE}/units",
                params={
                    "level": 5,
                    "page-size": _PAGE_SIZE,
                    "page": page,
                    "format": "json",
                },
                headers=_api_headers(),
                timeout=_HTTP_TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
            results = payload.get("results", [])
        except Exception as exc:  # pragma: no cover - network
            log.warning("BDL powiat catalog page %d failed: %s", page, exc)
            break

        for entry in results:
            unit_id = entry.get("id", "")
            voiv = voiv_map.get(unit_id[:4]) if unit_id else None
            if not voiv:
                continue
            powiats.append({
                "teryt_code": unit_id,
                "name": (entry.get("name") or "").strip(),
                "voivodeship": voiv,
            })

        if len(results) < _PAGE_SIZE:
            break
        page += 1
        time.sleep(0.3)

    log.info("BDL: fetched %d powiats", len(powiats))
    return powiats


def fetch_variable_series(variable_id: int) -> dict[str, list[dict]]:
    """Fetch a BDL variable for every powiat — full time-series, not just latest.

    Returns {teryt_code: [{'year': int, 'value': float}, ...]} sorted by year.
    Empty dict if variable is missing or API failed.
    """
    out: dict[str, list[dict]] = {}
    page = 0
    while True:
        try:
            r = requests.get(
                f"{_BDL_BASE}/data/by-variable/{variable_id}",
                params={
                    "unit-level": 5,
                    "page-size": _PAGE_SIZE,
                    "page": page,
                    "format": "json",
                },
                headers=_api_headers(),
                timeout=_HTTP_TIMEOUT,
            )
            if r.status_code == 404:
                log.warning("BDL variable %s not found", variable_id)
                return {}
            if r.status_code == 429:
                log.warning("BDL rate limit hit (var=%s page=%d) — partial data", variable_id, page)
                break
            r.raise_for_status()
            payload = r.json()
            results = payload.get("results", [])
        except Exception as exc:  # pragma: no cover - network
            log.warning("BDL var=%s page=%d failed: %s", variable_id, page, exc)
            break

        for entry in results:
            unit_id = entry.get("id", "")
            values = entry.get("values", [])
            if not unit_id or not values:
                continue
            series = []
            for v in values:
                try:
                    series.append({
                        "year": int(v.get("year", 0)),
                        "value": float(v.get("val", 0)),
                    })
                except (TypeError, ValueError):
                    continue
            if series:
                out[unit_id] = sorted(series, key=lambda x: x["year"])

        if len(results) < _PAGE_SIZE:
            break
        page += 1
        time.sleep(0.3)

    return out


def fetch_all_indicators_per_powiat() -> dict[str, dict]:
    """Catalog + BDL indicators (full time-series) merged by teryt_code.

    Output shape:
      {teryt_code: {
          'name': str,
          'voivodeship': str,
          'series': {<indicator_key>: [{'year', 'value'}, ...]},
      }}
    """
    catalog = fetch_powiat_catalog()
    merged: dict[str, dict] = {
        p["teryt_code"]: {
            "name": p["name"],
            "voivodeship": p["voivodeship"],
            "series": {},
        }
        for p in catalog
    }
    for key, var_id in BDL_VARS.items():
        data = fetch_variable_series(var_id)
        for teryt, series in data.items():
            if teryt in merged:
                merged[teryt]["series"][key] = series
        log.info("BDL var %s (%s): series for %d powiats", var_id, key, len(data))
    return merged
