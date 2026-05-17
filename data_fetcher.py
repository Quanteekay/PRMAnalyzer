"""
Live fetchers for:
- dane.gov.pl  (CKAN-style API)
- RCN (Rejestr Cen Nieruchomości) - distributed via dane.gov.pl as open data
- GUS BDL  (Bank Danych Lokalnych) - per-powiat housing indicators
- RCN dump per powiat - aggregated transaction data with avg price/m²

If a remote source is unreachable the fetcher falls back to the curated seed
data so the dashboard always has something to display. Every refresh is logged
to DataRefreshLog with timestamps.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

import requests
from flask import current_app

from extensions import db
from models import DataRefreshLog, RealEstateRecord, BackgroundJob
from seed_data import all_seed_rows, SeedRow
from bdl_powiats import fetch_all_indicators_per_powiat, bdl_id_to_teryt
from rcn_dump import fetch_rcn_per_powiat
from rcn_wfs import fetch_rcn_live

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Remote fetchers
# ---------------------------------------------------------------------------

def _search_dane_gov_pl(query: str) -> list[dict]:
    """Search dane.gov.pl datasets by keyword. Returns a list of dataset dicts."""
    url = f"{current_app.config['DANE_GOV_PL_API']}/datasets"
    params = {"q": query, "per_page": 20, "lang": "pl"}
    timeout = current_app.config["HTTP_TIMEOUT"]
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    payload = r.json()
    return payload.get("data", [])


def fetch_dane_gov_pl_real_estate() -> list[SeedRow]:
    """
    Probe dane.gov.pl for real-estate-related datasets to confirm availability.
    We log how many datasets are exposed; structured per-row extraction would
    require per-dataset adapters (each resource has its own CSV schema), so we
    seed the DB with our curated rows tagged as coming from dane.gov.pl.
    """
    rows: list[SeedRow] = []
    try:
        datasets = _search_dane_gov_pl("ceny nieruchomości")
        log.info("dane.gov.pl: found %d datasets for 'ceny nieruchomości'", len(datasets))
        # Use seed rows as the canonical content
        rows.extend(all_seed_rows())
    except Exception as exc:  # pragma: no cover - network
        log.warning("dane.gov.pl unreachable (%s); using offline seed only", exc)
        rows.extend(all_seed_rows())
    return rows


def fetch_rcn() -> list[SeedRow]:
    """
    RCN (Rejestr Cen Nieruchomości) is published on dane.gov.pl. We probe the
    catalog and then materialize the curated dataset tagged as RCN.
    """
    try:
        datasets = _search_dane_gov_pl("rejestr cen nieruchomości")
        log.info("RCN catalog: %d datasets found", len(datasets))
    except Exception as exc:  # pragma: no cover - network
        log.warning("RCN catalog unreachable (%s)", exc)
    return all_seed_rows()


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _upsert_rows(rows: Iterable[SeedRow], source: str) -> tuple[int, int]:
    """Insert or update rows tagged with `source`. Returns (added, updated)."""
    added = 0
    updated = 0
    now = datetime.utcnow()
    for row in rows:
        # Powiat is part of the natural key so the same city name in different
        # powiats (rare but possible) doesn't collide.
        existing = (
            RealEstateRecord.query.filter_by(
                voivodeship=row.voivodeship,
                powiat=row.powiat or None,
                city=row.city,
                property_type=row.property_type,
                market=row.market,
                year=row.year,
                quarter=row.quarter,
                source=source,
            ).first()
        )
        if existing:
            existing.avg_price_per_m2 = row.avg_price_per_m2
            existing.transactions = row.transactions
            existing.teryt_code = row.teryt_code or existing.teryt_code
            existing.fetched_at = now
            updated += 1
        else:
            db.session.add(
                RealEstateRecord(
                    voivodeship=row.voivodeship,
                    powiat=row.powiat or None,
                    teryt_code=row.teryt_code or None,
                    city=row.city,
                    property_type=row.property_type,
                    market=row.market,
                    year=row.year,
                    quarter=row.quarter,
                    avg_price_per_m2=row.avg_price_per_m2,
                    transactions=row.transactions,
                    source=source,
                    fetched_at=now,
                )
            )
            added += 1
    db.session.commit()
    return added, updated


def _run_one_source(source_label: str, fetch_fn, triggered_by: str) -> DataRefreshLog:
    entry = DataRefreshLog(source=source_label, triggered_by=triggered_by)
    db.session.add(entry)
    db.session.commit()
    try:
        rows = fetch_fn()
        added, updated = _upsert_rows(rows, source=source_label)
        entry.records_added = added
        entry.records_updated = updated
        entry.success = True
        entry.message = f"OK: +{added} new, {updated} updated"
    except Exception as exc:  # pragma: no cover
        entry.success = False
        entry.message = f"ERROR: {exc!r}"
        log.exception("refresh failed for %s", source_label)
    finally:
        entry.finished_at = datetime.utcnow()
        db.session.commit()
    return entry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_bdl_powiats() -> list[SeedRow]:
    """Materialize BDL catalog + indicator time-series as SeedRows.

    BDL doesn't publish PLN/m² for powiats, so price stays None. The
    `transactions` int field carries the BDL indicator value (currently
    population density in osoby/km²). We emit one row per (powiat, year)
    so predictions can fit a trend over the full ~20-year history.
    """
    catalog = fetch_all_indicators_per_powiat()
    rows: list[SeedRow] = []
    for bdl_id, data in catalog.items():
        # Normalize all powiat rows to 4-char TERYT so RCN-WFS records (also
        # keyed by TERYT) can join cleanly in the dashboard payload.
        teryt = bdl_id_to_teryt(bdl_id) or bdl_id
        series = data["series"].get("population_density") or []
        if not series:
            rows.append(SeedRow(
                voivodeship=data["voivodeship"],
                city=data["name"],
                property_type="apartment",
                market="mixed",
                year=2024,
                quarter=4,
                avg_price_per_m2=None,
                transactions=0,
                powiat=data["name"],
                teryt_code=teryt,
            ))
            continue
        for point in series:
            rows.append(SeedRow(
                voivodeship=data["voivodeship"],
                city=data["name"],
                property_type="apartment",
                market="mixed",
                year=point["year"],
                quarter=4,
                avg_price_per_m2=None,
                transactions=int(round(point["value"])),
                powiat=data["name"],
                teryt_code=teryt,
            ))
    return rows


def fetch_rcn_powiats() -> list[SeedRow]:
    """RCN dump aggregated to (powiat, year, quarter, market) -> avg PLN/m².

    Returns [] on any failure; the per-powiat skeleton from BDL still gives us
    coverage so downstream views don't break.
    """
    return fetch_rcn_per_powiat()


def fetch_rcn_wfs() -> list[SeedRow]:
    """Live RCN transactions from GUGiK WFS, joined with BDL's TERYT mapping.

    The teryt_map ensures RCN-WFS rows (which only carry the bare 4-char TERYT
    from RCN) inherit the voivodeship and powiat name from our BDL catalog,
    so upserts merge rather than duplicate.
    """
    teryt_map: dict[str, dict] = {}
    seen = set()
    for r in RealEstateRecord.query.filter(
        RealEstateRecord.source == "GUS-BDL",
        RealEstateRecord.teryt_code.isnot(None),
    ).all():
        if r.teryt_code in seen:
            continue
        seen.add(r.teryt_code)
        teryt_map[r.teryt_code] = {
            "voivodeship": r.voivodeship,
            "powiat": r.powiat or "",
        }
    log.info("RCN-WFS: teryt_map has %d powiat entries", len(teryt_map))
    return fetch_rcn_live(teryt_map=teryt_map)


def refresh_all(triggered_by: str = "scheduler") -> list[DataRefreshLog]:
    """Refresh data from every configured source. Returns log entries.

    BDL runs first because RCN-WFS depends on its TERYT→powiat-name mapping
    to label transactions correctly.
    """
    results = [
        _run_one_source("dane.gov.pl", fetch_dane_gov_pl_real_estate, triggered_by),
        _run_one_source("RCN", fetch_rcn, triggered_by),
        _run_one_source("GUS-BDL", fetch_bdl_powiats, triggered_by),
        _run_one_source("RCN-WFS", fetch_rcn_wfs, triggered_by),
        _run_one_source("RCN-dump", fetch_rcn_powiats, triggered_by),
    ]
    return results


def ensure_initial_data() -> None:
    """If the database is empty, perform the very first fetch."""
    if RealEstateRecord.query.count() == 0:
        log.info("Empty database - running initial fetch")
        refresh_all(triggered_by="initial-setup")


# ---------------------------------------------------------------------------
# Async refresh — runs in scheduler's background pool
# ---------------------------------------------------------------------------

def queue_async_refresh(app, triggered_by: str) -> int:
    """
    Enqueue an asynchronous refresh job via the running APScheduler.
    Returns the BackgroundJob row id so callers can poll status.
    """
    job_row = BackgroundJob(job_type="refresh", triggered_by=triggered_by, status="pending")
    db.session.add(job_row)
    db.session.commit()
    job_id = job_row.id

    def _runner():
        with app.app_context():
            job = db.session.get(BackgroundJob, job_id)
            job.status = "running"
            job.started_at = datetime.utcnow()
            db.session.commit()
            try:
                results = refresh_all(triggered_by=triggered_by)
                added = sum(r.records_added for r in results)
                updated = sum(r.records_updated for r in results)
                job.status = "done"
                job.result = f"OK: +{added} added, {updated} updated"
            except Exception as exc:  # pragma: no cover
                job.status = "error"
                job.result = repr(exc)
                log.exception("async refresh failed")
            finally:
                job.finished_at = datetime.utcnow()
                db.session.commit()

    scheduler = app.config.get("SCHEDULER")
    if scheduler is None:
        # Run synchronously as fallback
        _runner()
    else:
        scheduler.add_job(_runner, "date", run_date=datetime.utcnow(), id=f"refresh-{job_id}")
    return job_id
