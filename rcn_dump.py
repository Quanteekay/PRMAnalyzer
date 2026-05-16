"""
RCN (Rejestr Cen Nieruchomości) dump fetcher.

Strategy:
1. Search dane.gov.pl catalog for the RCN dataset.
2. List its resources (CSV files, one per period).
3. Stream-download the newest CSV (size-capped).
4. Heuristically parse columns — RCN historically uses Polish names that vary
   slightly across years, so we accept several aliases per field.
5. Aggregate transactions into (voivodeship, powiat, year, quarter, market)
   → (avg_price_per_m2 weighted by powierzchnia, n_transactions).

All steps are defensive: any failure logs a warning and returns an empty list,
letting the caller fall back to seed/BDL data.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from typing import Iterable

import requests

from seed_data import SeedRow

log = logging.getLogger(__name__)

_DANE_GOV_PL_API = "https://api.dane.gov.pl/1.4"
_HTTP_TIMEOUT = 30
_MAX_BYTES = 80 * 1024 * 1024  # 80 MB safety cap
_CHUNK = 64 * 1024

# Heuristic column-name aliases (Polish RCN CSVs)
_COL_ALIASES = {
    "voivodeship": ["wojewodztwo", "województwo", "woj"],
    "powiat": ["powiat", "nazwa_powiatu"],
    "teryt": ["teryt", "kod_teryt", "kod_powiatu"],
    "price": ["cena", "cena_transakcyjna", "cena_brutto"],
    "area": ["powierzchnia", "powierzchnia_uzytkowa", "powierzchnia_użytkowa", "pow"],
    "date": ["data_transakcji", "data_zawarcia", "data"],
    "market": ["rynek", "typ_rynku"],
    "property_type": ["rodzaj_nieruchomosci", "rodzaj", "typ_nieruchomosci", "typ"],
}


def _clean_title(raw: str) -> str:
    """Strip <mark>…</mark> highlight wrappers and lowercase for matching."""
    import re
    return re.sub(r"<[^>]+>", "", raw or "").lower()


def _find_rcn_dataset() -> dict | None:
    """Probe dane.gov.pl for the RCN dataset, return the strict match if any.

    dane.gov.pl's search is loose (OR over terms), so we filter strictly by
    title to avoid false positives like 'Rejestr Podmiotów Prowadzących…'.
    RCN is primarily published by GUGiK and may not appear in dane.gov.pl at
    all — in that case we return None and the caller logs and skips.
    """
    try:
        r = requests.get(
            f"{_DANE_GOV_PL_API}/datasets",
            params={"q": "rejestr cen nieruchomości", "per_page": 50, "lang": "pl"},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:  # pragma: no cover - network
        log.warning("RCN: dane.gov.pl catalog query failed: %s", exc)
        return None

    for ds in payload.get("data", []):
        title = _clean_title(ds.get("attributes", {}).get("title", ""))
        if "rejestr cen" in title and "nieruchomo" in title:
            return ds
    log.info("RCN: dane.gov.pl has no dataset matching 'rejestr cen … nieruchomości'")
    return None


def _list_resources(dataset_id: str) -> list[dict]:
    """List file resources attached to a dane.gov.pl dataset."""
    try:
        r = requests.get(
            f"{_DANE_GOV_PL_API}/datasets/{dataset_id}/resources",
            params={"per_page": 50},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as exc:  # pragma: no cover - network
        log.warning("RCN: failed listing resources for dataset %s: %s", dataset_id, exc)
        return []


def _pick_csv_resource(resources: list[dict]) -> dict | None:
    """Pick the newest CSV/XLSX resource."""
    csv_like = []
    for res in resources:
        attrs = res.get("attributes", {})
        fmt = (attrs.get("format") or "").lower()
        if fmt in {"csv", "xlsx", "xls"}:
            csv_like.append(res)
    if not csv_like:
        return None
    # Newest first by created/modified timestamp if available
    csv_like.sort(
        key=lambda r: r.get("attributes", {}).get("modified") or "",
        reverse=True,
    )
    return csv_like[0]


def _download_capped(url: str) -> bytes | None:
    """Stream-download up to _MAX_BYTES; abort and warn beyond cap."""
    try:
        with requests.get(url, stream=True, timeout=_HTTP_TIMEOUT) as r:
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=_CHUNK):
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) > _MAX_BYTES:
                    log.warning("RCN: dump exceeded %d MB cap, aborting", _MAX_BYTES // (1024 * 1024))
                    return None
            return bytes(buf)
    except Exception as exc:  # pragma: no cover - network
        log.warning("RCN: download failed (%s): %s", url, exc)
        return None


def _resolve_columns(header: list[str]) -> dict[str, int]:
    """Map our internal field names to column indexes, using alias heuristics."""
    norm = {h.strip().lower().replace(" ", "_"): i for i, h in enumerate(header)}
    resolved: dict[str, int] = {}
    for field, aliases in _COL_ALIASES.items():
        for alias in aliases:
            if alias in norm:
                resolved[field] = norm[alias]
                break
    return resolved


def _quarter_of(date_str: str) -> tuple[int, int] | None:
    """Extract (year, quarter) from a YYYY-MM-DD or DD.MM.YYYY date string."""
    if not date_str:
        return None
    s = date_str.strip()
    try:
        if "-" in s and len(s) >= 10:  # YYYY-MM-DD
            y, m = int(s[:4]), int(s[5:7])
        elif "." in s and len(s) >= 10:  # DD.MM.YYYY
            d, m, y = s.split(".")[:3]
            y, m = int(y), int(m)
        else:
            return None
        if 1 <= m <= 12:
            return y, (m - 1) // 3 + 1
    except (ValueError, IndexError):
        return None
    return None


def _normalize_market(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s.startswith("p") or "pierwotn" in s:
        return "primary"
    if s.startswith("w") or "wtorn" in s or "wtórn" in s:
        return "secondary"
    return "secondary"  # default — RCN majority is secondary


def _parse_and_aggregate(csv_bytes: bytes) -> list[SeedRow]:
    """Parse CSV bytes and aggregate to one SeedRow per (powiat, year, qtr, market)."""
    # Try utf-8 first, fall back to cp1250 (common in Polish gov CSVs)
    for encoding in ("utf-8", "cp1250", "utf-8-sig"):
        try:
            text = csv_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        log.warning("RCN: could not decode CSV bytes")
        return []

    # Sniff delimiter
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"

    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        return []
    cols = _resolve_columns(header)
    log.info("RCN: resolved columns: %s", cols)

    # Need at minimum: powiat (or voivodeship), price, area, date
    required = {"price", "area", "date"}
    if not required.issubset(cols.keys()):
        log.warning("RCN: required columns missing — header was: %s", header)
        return []
    if "powiat" not in cols and "voivodeship" not in cols:
        log.warning("RCN: neither 'powiat' nor 'voivodeship' column found")
        return []

    # Group: (voivodeship, powiat, year, quarter, market) -> [sum_price, sum_area, n]
    groups: dict[tuple, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
    n_rows = 0
    for row in reader:
        n_rows += 1
        try:
            price_str = row[cols["price"]].strip().replace(" ", "").replace(",", ".")
            area_str = row[cols["area"]].strip().replace(" ", "").replace(",", ".")
            price = float(price_str)
            area = float(area_str)
            if price <= 0 or area <= 0:
                continue
        except (ValueError, IndexError):
            continue

        ydate = _quarter_of(row[cols["date"]]) if "date" in cols else None
        if not ydate:
            continue
        year, quarter = ydate

        powiat = row[cols["powiat"]].strip() if "powiat" in cols else ""
        voivodeship = row[cols["voivodeship"]].strip() if "voivodeship" in cols else ""
        market = _normalize_market(row[cols["market"]]) if "market" in cols else "secondary"

        key = (voivodeship, powiat, year, quarter, market)
        bucket = groups[key]
        bucket[0] += price
        bucket[1] += area
        bucket[2] += 1

    log.info("RCN: parsed %d transactions, %d groups", n_rows, len(groups))

    out: list[SeedRow] = []
    for (voiv, powiat, year, quarter, market), (sum_price, sum_area, n) in groups.items():
        if sum_area <= 0 or n == 0:
            continue
        avg_per_m2 = sum_price / sum_area
        out.append(SeedRow(
            voivodeship=voiv or "nieznane",
            city=powiat,                        # we don't have city-level — store powiat in both
            property_type="apartment",
            market=market,
            year=year,
            quarter=quarter,
            avg_price_per_m2=round(avg_per_m2, 0),
            transactions=n,
            powiat=powiat,
            teryt_code="",
        ))
    return out


def fetch_rcn_per_powiat() -> list[SeedRow]:
    """End-to-end RCN dump fetcher. Returns aggregated rows or [] on failure."""
    ds = _find_rcn_dataset()
    if not ds:
        log.warning("RCN: dataset not found on dane.gov.pl")
        return []
    ds_id = ds.get("id") or ds.get("attributes", {}).get("id")
    if not ds_id:
        return []
    log.info("RCN: candidate dataset id=%s", ds_id)

    resources = _list_resources(str(ds_id))
    if not resources:
        return []
    log.info("RCN: %d resources attached", len(resources))

    pick = _pick_csv_resource(resources)
    if not pick:
        log.warning("RCN: no CSV/XLSX resource in dataset")
        return []
    url = pick.get("attributes", {}).get("file_url") or pick.get("attributes", {}).get("link")
    if not url:
        log.warning("RCN: chosen resource has no download URL")
        return []
    log.info("RCN: downloading %s", url)

    blob = _download_capped(url)
    if not blob:
        return []

    rows = _parse_and_aggregate(blob)
    log.info("RCN: produced %d aggregated rows", len(rows))
    return rows
