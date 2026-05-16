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
import time

import requests

_BDL_BASE = "https://bdl.stat.gov.pl/api/v1"
_HTTP_TIMEOUT = 15
_PAGE_SIZE = 100

log = logging.getLogger(__name__)


# BDL variable IDs for housing — sourced from BDL's variable catalog
# (https://bdl.stat.gov.pl). If a variable is unavailable, we log a warning
# and continue without it (graceful degradation).
BDL_VARS_HOUSING = {
    "dwellings_completed": 60271,   # Mieszkania oddane do użytkowania - ogółem
    "permits": 60559,               # Pozwolenia na budowę - mieszkania
}


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


def fetch_variable_for_powiats(variable_id: int) -> dict[str, dict]:
    """Fetch one BDL variable for every powiat (unit-level=5).

    Returns: {teryt_code: {'value': float, 'year': int}}.
    Empty dict if variable is missing or API failed.
    """
    out: dict[str, dict] = {}
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
                timeout=_HTTP_TIMEOUT,
            )
            if r.status_code == 404:
                log.warning("BDL variable %s not found", variable_id)
                return {}
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
            latest = max(values, key=lambda v: v.get("year", 0))
            try:
                out[unit_id] = {
                    "value": float(latest.get("val", 0)),
                    "year": int(latest.get("year", 0)),
                }
            except (TypeError, ValueError):
                continue

        if len(results) < _PAGE_SIZE:
            break
        page += 1
        time.sleep(0.3)

    return out


def fetch_all_housing_per_powiat() -> dict[str, dict]:
    """Catalog + housing indicators merged into one dict keyed by teryt_code.

    Output shape:
      {teryt_code: {
          'name': str,
          'voivodeship': str,
          'dwellings_completed': float|None,
          'dwellings_completed_year': int|None,
          'permits': float|None,
          'permits_year': int|None,
      }}
    """
    catalog = fetch_powiat_catalog()
    merged: dict[str, dict] = {
        p["teryt_code"]: {
            "name": p["name"],
            "voivodeship": p["voivodeship"],
            "dwellings_completed": None,
            "dwellings_completed_year": None,
            "permits": None,
            "permits_year": None,
        }
        for p in catalog
    }
    for key, var_id in BDL_VARS_HOUSING.items():
        data = fetch_variable_for_powiats(var_id)
        for teryt, payload in data.items():
            if teryt in merged:
                merged[teryt][key] = payload["value"]
                merged[teryt][f"{key}_year"] = payload["year"]
        log.info("BDL var %s (%s): values for %d powiats", var_id, key, len(data))
    return merged
