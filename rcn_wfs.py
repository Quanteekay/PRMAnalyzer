"""
GUGiK RCN (Rejestr Cen Nieruchomości) live fetcher via WFS.

Endpoint: https://mapy.geoportal.gov.pl/wss/service/rcn
Feature type: ms:lokale (individual flat transactions — TERYT, price, area, date, market type).

The WFS is intentionally feature-poor: CQL filter, sortBy and resultType=hits
are all ignored or blocked, so we paginate naively via startIndex and aggregate
client-side per (TERYT_4, year, quarter, market) → weighted PLN/m².

Free, no auth, no per-call rate limit beyond polite delays.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict

import requests

from seed_data import SeedRow

log = logging.getLogger(__name__)

_WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/rcn"
_PAGE_SIZE = 1000
_DEFAULT_MAX_PAGES = 100          # ~100k transactions per refresh — RCN holds
                                  # a lot of historical 2008-2019 rows; we need
                                  # to sift through them to reach every powiat.
_HTTP_TIMEOUT = 60
_POLITE_DELAY = 0.4

_FEATURE_RE = re.compile(r"<ms:lokale[^>]*>(.*?)</ms:lokale>", re.DOTALL)
_FIELD_RE = re.compile(r"<ms:(\w+)>([^<]+)</ms:\w+>")


def _parse_features(gml: str) -> list[dict]:
    out: list[dict] = []
    for fmatch in _FEATURE_RE.finditer(gml):
        block = fmatch.group(1)
        out.append(dict(_FIELD_RE.findall(block)))
    return out


def _quarter_of(date_str: str) -> tuple[int, int] | None:
    """RCN dates look like '2024-07-13 02:00:00+02'; we only need year/quarter."""
    if not date_str or len(date_str) < 10:
        return None
    try:
        year, month = int(date_str[:4]), int(date_str[5:7])
        if 1 <= month <= 12:
            return year, (month - 1) // 3 + 1
    except ValueError:
        pass
    return None


def _normalize_market(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s.startswith("pierwot"):
        return "primary"
    if s.startswith("wtorn") or s.startswith("wtórn"):
        return "secondary"
    return "secondary"


def _is_full_share(value: str) -> bool:
    """RCN's nier_udzial is '1/1' or empty for whole-property sales; anything
    else (e.g. '1/2', '3/8') is a fractional share where price/area can't be
    compared 1:1 with other rows. We keep only whole-property transactions."""
    v = (value or "").strip()
    if not v or v in {"1", "1/1", "1.0", "1/1.0"}:
        return True
    return False


def fetch_rcn_live(
    teryt_map: dict[str, dict] | None = None,
    max_pages: int = _DEFAULT_MAX_PAGES,
    year_min: int = 2020,
    year_max: int = 2030,
    price_per_m2_min: float = 1000.0,
    price_per_m2_max: float = 100000.0,
) -> list[SeedRow]:
    """Paginate RCN WFS, parse GML, aggregate to (TERYT, year, quarter, market).

    Drops fractional-share sales and outlier transaction-level price/m² figures
    before aggregation so per-bucket weighted means reflect normal market sales.
    """
    teryt_map = teryt_map or {}
    buckets: dict[tuple, dict] = defaultdict(
        lambda: {"sum_price": 0.0, "sum_area": 0.0, "n": 0}
    )
    fetched = 0
    skipped = {"non_residential": 0, "share": 0, "bad_value": 0, "year_oor": 0, "outlier": 0}

    for page in range(max_pages):
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "ms:lokale",
            "count": _PAGE_SIZE,
            "startIndex": page * _PAGE_SIZE,
        }
        try:
            r = requests.get(_WFS_URL, params=params, timeout=_HTTP_TIMEOUT)
            r.raise_for_status()
        except Exception as exc:  # pragma: no cover - network
            log.warning("RCN-WFS page %d failed: %s", page, exc)
            break

        feats = _parse_features(r.text)
        if not feats:
            break

        for f in feats:
            if f.get("lok_funkcja") != "mieszkalna":
                skipped["non_residential"] += 1
                continue
            if not _is_full_share(f.get("nier_udzial", "")):
                skipped["share"] += 1
                continue
            # Prefer the per-flat price (lok_cena_brutto) over the transaction
            # total — the latter sums multiple flats sold in one act.
            try:
                price = float(
                    (f.get("lok_cena_brutto") or "").strip()
                    or f.get("tran_cena_brutto")
                    or 0
                )
                area = float(f.get("lok_pow_uzyt") or 0)
            except (ValueError, TypeError):
                skipped["bad_value"] += 1
                continue
            if price <= 0 or area <= 0:
                skipped["bad_value"] += 1
                continue
            yq = _quarter_of(f.get("dok_data", ""))
            if not yq or not (year_min <= yq[0] <= year_max):
                skipped["year_oor"] += 1
                continue
            per_m2 = price / area
            if not (price_per_m2_min <= per_m2 <= price_per_m2_max):
                skipped["outlier"] += 1
                continue
            year, quarter = yq
            teryt = (f.get("teryt") or "").strip()
            if not teryt:
                continue
            market = _normalize_market(f.get("tran_rodzaj_rynku", ""))
            key = (teryt, year, quarter, market)
            bucket = buckets[key]
            bucket["sum_price"] += price
            bucket["sum_area"] += area
            bucket["n"] += 1

        fetched += len(feats)
        if len(feats) < _PAGE_SIZE:
            break
        time.sleep(_POLITE_DELAY)

    log.info(
        "RCN-WFS: parsed %d features, %d aggregated rows, skipped %s",
        fetched, len(buckets), skipped,
    )

    rows: list[SeedRow] = []
    for (teryt, year, quarter, market), data in buckets.items():
        if data["sum_area"] <= 0:
            continue
        info = teryt_map.get(teryt, {})
        rows.append(SeedRow(
            voivodeship=info.get("voivodeship", "nieznane"),
            city=info.get("powiat", ""),
            property_type="apartment",
            market=market,
            year=year,
            quarter=quarter,
            avg_price_per_m2=round(data["sum_price"] / data["sum_area"], 0),
            transactions=data["n"],
            powiat=info.get("powiat", ""),
            teryt_code=teryt,
        ))
    return rows
