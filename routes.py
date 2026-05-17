"""Main HTTP blueprint - public, member and admin endpoints."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint, render_template, jsonify, request, redirect,
    url_for, flash, abort, Response, current_app,
)
from flask_login import login_required, current_user
from sqlalchemy import func, or_
from openpyxl import Workbook

from extensions import db, limiter
from models import (
    RealEstateRecord, DataRefreshLog, get_latest_fetched_at, User,
    WatchedCity, BackgroundJob, ApiKey,
)
from data_fetcher import refresh_all, queue_async_refresh
from external_apis import (
    nbp_rates, nbp_gold_price, gus_wages_by_voivodeship, affordability_index,
)
from predictions import forecast_density

main_bp = Blueprint("main", __name__)


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def _city_records_latest():
    # Restrict to rows that actually carry a price/m² — BDL rows for whole
    # powiats are price-less and shouldn't pollute the city dashboard.
    subq = (
        db.session.query(
            RealEstateRecord.city,
            func.max(RealEstateRecord.year * 10 + RealEstateRecord.quarter).label("max_yq"),
        )
        .filter(RealEstateRecord.city.isnot(None))
        .filter(RealEstateRecord.property_type == "apartment")
        .filter(RealEstateRecord.avg_price_per_m2.isnot(None))
        .group_by(RealEstateRecord.city)
        .subquery()
    )
    return (
        RealEstateRecord.query.join(
            subq,
            (RealEstateRecord.city == subq.c.city)
            & ((RealEstateRecord.year * 10 + RealEstateRecord.quarter) == subq.c.max_yq),
        )
        .filter(RealEstateRecord.property_type == "apartment")
        .filter(RealEstateRecord.avg_price_per_m2.isnot(None))
        .all()
    )


def _kpis():
    rows = _city_records_latest()
    if not rows:
        return {"avg": 0, "min": 0, "max": 0, "cities": 0, "tx": 0}
    prices = [r.avg_price_per_m2 for r in rows]
    return {
        "avg": round(sum(prices) / len(prices), 0),
        "min": min(prices),
        "max": max(prices),
        "cities": len({r.city for r in rows}),
        "tx": sum((r.transactions or 0) for r in rows),
    }


def _cities_payload(include_affordability: bool = False):
    rows = _city_records_latest()
    grouped: dict[str, dict] = defaultdict(lambda: {"primary": None, "secondary": None})
    for r in rows:
        grouped[r.city][r.market] = r

    wages = gus_wages_by_voivodeship() if include_affordability else {}

    payload = []
    for city, by_market in grouped.items():
        primary = by_market.get("primary")
        secondary = by_market.get("secondary")
        any_row = primary or secondary
        avg_price = None
        prices = [p.avg_price_per_m2 for p in (primary, secondary) if p]
        if prices:
            avg_price = sum(prices) / len(prices)
        row = {
            "city": city,
            "voivodeship": any_row.voivodeship,
            "primary": primary.avg_price_per_m2 if primary else None,
            "secondary": secondary.avg_price_per_m2 if secondary else None,
            "tx_primary": primary.transactions if primary else 0,
            "tx_secondary": secondary.transactions if secondary else 0,
            "year": any_row.year,
            "quarter": any_row.quarter,
        }
        if include_affordability and avg_price:
            row["affordability"] = affordability_index(avg_price, any_row.voivodeship)
        payload.append(row)
    payload.sort(key=lambda x: max(x["primary"] or 0, x["secondary"] or 0), reverse=True)
    return payload


def _voivodeship_trend():
    rows = (
        RealEstateRecord.query.filter(RealEstateRecord.market == "mixed")
        .filter(RealEstateRecord.avg_price_per_m2.isnot(None))
        .order_by(RealEstateRecord.year.asc())
        .all()
    )
    series: dict[str, dict] = defaultdict(lambda: {"years": [], "values": []})
    for r in rows:
        series[r.voivodeship]["years"].append(r.year)
        series[r.voivodeship]["values"].append(r.avg_price_per_m2)
    return series


def _voivodeship_latest_price() -> dict[str, float]:
    """Latest avg apartment price by voivodeship - used by the map."""
    rows = _city_records_latest()
    grouped: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        grouped[r.voivodeship].append(r.avg_price_per_m2)
    return {v: round(sum(p) / len(p), 0) for v, p in grouped.items() if p}


def _powiats_payload() -> list[dict]:
    """Latest snapshot per (voivodeship, powiat): newest non-null avg PLN/m²
    (from RCN seed for big-city powiats) and newest density value (from BDL).
    """
    rows = (
        RealEstateRecord.query.filter(RealEstateRecord.powiat.isnot(None))
        .order_by(RealEstateRecord.year.desc(), RealEstateRecord.quarter.desc())
        .all()
    )
    by_key: dict[tuple, dict] = {}
    for r in rows:
        key = (r.voivodeship, r.powiat)
        bucket = by_key.setdefault(key, {
            "voivodeship": r.voivodeship,
            "powiat": r.powiat,
            "teryt_code": r.teryt_code,
            "avg_price_per_m2": None,
            "density": None,
            "year": None,
            "quarter": None,
            "sources": set(),
        })
        bucket["sources"].add(r.source)
        if not bucket["teryt_code"] and r.teryt_code:
            bucket["teryt_code"] = r.teryt_code
        if bucket["avg_price_per_m2"] is None and r.avg_price_per_m2:
            bucket["avg_price_per_m2"] = r.avg_price_per_m2
            bucket["year"] = r.year
            bucket["quarter"] = r.quarter
        if bucket["density"] is None and r.transactions:
            bucket["density"] = r.transactions
            if not bucket["year"]:
                bucket["year"] = r.year
                bucket["quarter"] = r.quarter

    out = []
    for b in by_key.values():
        b["sources"] = sorted(b["sources"])
        out.append(b)
    out.sort(key=lambda x: (x["voivodeship"], x["powiat"]))
    return out


def _powiats_kpis(payload: list[dict]) -> dict:
    """Aggregate KPIs across all powiats for the dashboard hero row."""
    if not payload:
        return {"total": 0, "with_price": 0, "avg_density": 0, "max_density": 0, "min_density": 0, "avg_price": 0}
    densities = [p["density"] for p in payload if p["density"]]
    prices = [p["avg_price_per_m2"] for p in payload if p["avg_price_per_m2"]]
    return {
        "total": len(payload),
        "with_price": len(prices),
        "avg_density": round(sum(densities) / len(densities), 0) if densities else 0,
        "max_density": max(densities) if densities else 0,
        "min_density": min(densities) if densities else 0,
        "avg_price": round(sum(prices) / len(prices), 0) if prices else 0,
    }


# ---------------------------------------------------------------------------
# Public + member pages
# ---------------------------------------------------------------------------

@main_bp.route("/")
def index():
    kpis = _kpis()
    latest = get_latest_fetched_at()
    return render_template("index.html", kpis=kpis, latest_fetched=latest)


@main_bp.route("/dashboard")
@login_required
def dashboard():
    powiats = _powiats_payload()
    kpis = _powiats_kpis(powiats)
    voivodeships = sorted({p["voivodeship"] for p in powiats})
    watched = {w.city for w in current_user.watched_cities}
    return render_template(
        "dashboard.html",
        powiats=powiats,
        kpis=kpis,
        voivodeships=voivodeships,
        latest_fetched=get_latest_fetched_at(),
        watched=watched,
    )


@main_bp.route("/analytics")
@login_required
def analytics():
    cities = _cities_payload()
    trend = _voivodeship_trend()
    latest = get_latest_fetched_at()
    return render_template(
        "analytics.html",
        cities=cities,
        trend=trend,
        latest_fetched=latest,
    )


def _normalize_powiat_name(name: str) -> str:
    """Match key for joining BDL powiat names with GeoJSON 'nazwa' field.

    BDL examples → GeoJSON examples:
      'Powiat bocheński'    → 'powiat bocheński'
      'Powiat m. Kraków'    → 'powiat Kraków'
    Strip the leading 'powiat' and any 'm.'/'m.st.' marker, lowercase the rest.
    """
    s = name.lower().strip()
    for prefix in ("powiat ",):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for marker in ("m. ", "m.st. ", "m.st.", "m."):
        if s.startswith(marker):
            s = s[len(marker):]
            break
    return s.strip()


@main_bp.route("/map")
@login_required
def map_view():
    payload = _powiats_payload()
    # Map by normalized name so the JS layer can join with the GeoJSON polygons.
    by_name: dict[str, dict] = {}
    for p in payload:
        key = _normalize_powiat_name(p["powiat"])
        if key and key not in by_name:
            by_name[key] = {
                "powiat": p["powiat"],
                "voivodeship": p["voivodeship"],
                "teryt_code": p["teryt_code"],
                "density": p["density"],
                "avg_price_per_m2": p["avg_price_per_m2"],
            }
    voivodeships = sorted({p["voivodeship"] for p in payload})
    return render_template(
        "map.html",
        powiats_by_name=by_name,
        voivodeships=voivodeships,
        latest_fetched=get_latest_fetched_at(),
    )


@main_bp.route("/compare")
@login_required
def compare():
    selected_names = request.args.getlist("powiat")[:5]  # hard cap at 5
    payload = _powiats_payload()
    by_name = {p["powiat"]: p for p in payload}
    selected = [by_name[n] for n in selected_names if n in by_name]
    return render_template(
        "compare.html",
        all_powiats=payload,
        selected=selected,
        latest_fetched=get_latest_fetched_at(),
    )


@main_bp.route("/predictions")
@login_required
def predictions_view():
    teryt = request.args.get("teryt", "")
    result = forecast_density(teryt_code=teryt or None, forecast_years=5)
    powiats = _powiats_payload()
    selected_name = None
    if teryt:
        match = next((p for p in powiats if p["teryt_code"] == teryt), None)
        selected_name = match["powiat"] if match else None
    return render_template(
        "predictions.html",
        result=result,
        powiats=powiats,
        selected_teryt=teryt,
        selected_name=selected_name,
        latest_fetched=get_latest_fetched_at(),
    )


@main_bp.route("/finance")
@login_required
def finance_view():
    """NBP exchange rates + gold price + simple mortgage calculator UI."""
    rates = nbp_rates()
    gold = nbp_gold_price()
    return render_template(
        "finance.html",
        rates=rates,
        gold=gold,
        latest_fetched=get_latest_fetched_at(),
    )


@main_bp.route("/api/powiats")
@login_required
def api_powiats():
    return jsonify({
        "data": _powiats_payload(),
        "fetched_at": get_latest_fetched_at().isoformat() if get_latest_fetched_at() else None,
    })


@main_bp.route("/watchlist")
@login_required
def watchlist_view():
    watched_names = {w.city for w in current_user.watched_cities}
    powiats = [p for p in _powiats_payload() if p["powiat"] in watched_names]
    return render_template(
        "watchlist.html",
        powiats=powiats,
        latest_fetched=get_latest_fetched_at(),
    )


@main_bp.route("/watchlist/toggle", methods=["POST"])
@login_required
def watchlist_toggle():
    city = (request.form.get("city") or "").strip()
    if not city:
        return jsonify({"ok": False, "error": "missing city"}), 400
    existing = WatchedCity.query.filter_by(user_id=current_user.id, city=city).first()
    if existing:
        db.session.delete(existing)
        action = "removed"
    else:
        db.session.add(WatchedCity(user_id=current_user.id, city=city))
        action = "added"
    db.session.commit()
    return jsonify({"ok": True, "action": action, "city": city})


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@main_bp.route("/admin")
@admin_required
def admin():
    logs = (
        DataRefreshLog.query.order_by(DataRefreshLog.started_at.desc()).limit(30).all()
    )
    users = User.query.order_by(User.created_at.desc()).all()
    jobs = BackgroundJob.query.order_by(BackgroundJob.created_at.desc()).limit(10).all()
    api_keys = ApiKey.query.order_by(ApiKey.created_at.desc()).all()
    stats = {
        "records": RealEstateRecord.query.count(),
        "users": User.query.count(),
        "admins": User.query.filter_by(is_admin=True).count(),
        "refreshes": DataRefreshLog.query.count(),
    }
    return render_template(
        "admin.html",
        logs=logs,
        users=users,
        jobs=jobs,
        api_keys=api_keys,
        stats=stats,
        latest_fetched=get_latest_fetched_at(),
    )


@main_bp.route("/admin/refresh", methods=["POST"])
@admin_required
@limiter.limit("10 per minute")
def admin_refresh():
    """Trigger immediate sync refresh."""
    results = refresh_all(triggered_by=current_user.username)
    added = sum(r.records_added for r in results)
    updated = sum(r.records_updated for r in results)
    ok = all(r.success for r in results)
    if ok:
        flash(f"Dane odświeżone — dodano {added}, zaktualizowano {updated}.", "success")
    else:
        flash("Część źródeł zgłosiła błąd — sprawdź dziennik poniżej.", "warning")
    return redirect(url_for("main.admin"))


@main_bp.route("/admin/refresh-async", methods=["POST"])
@admin_required
@limiter.limit("30 per hour")
def admin_refresh_async():
    """Enqueue background refresh job."""
    job_id = queue_async_refresh(current_app._get_current_object(), triggered_by=current_user.username)
    flash(f"Job #{job_id} dodany do kolejki — odśwież panel za chwilę.", "info")
    return redirect(url_for("main.admin"))


@main_bp.route("/admin/api-keys", methods=["POST"])
@admin_required
def admin_issue_api_key():
    label = (request.form.get("label") or "default").strip()
    key_row = ApiKey.issue(current_user, label=label)
    db.session.commit()
    flash(f"Klucz API utworzony: {key_row.key}", "success")
    return redirect(url_for("main.admin"))


@main_bp.route("/admin/api-keys/<int:key_id>/revoke", methods=["POST"])
@admin_required
def admin_revoke_api_key(key_id):
    key = ApiKey.query.get_or_404(key_id)
    key.revoked = True
    db.session.commit()
    flash("Klucz API odwołany.", "info")
    return redirect(url_for("main.admin"))


# ---------------------------------------------------------------------------
# CSV / XLSX export
# ---------------------------------------------------------------------------

@main_bp.route("/export/powiats.csv")
@login_required
def export_powiats_csv():
    powiats = _powiats_payload()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "voivodeship", "powiat", "teryt_code",
        "density_persons_per_km2", "avg_price_per_m2", "year", "quarter", "sources",
    ])
    for p in powiats:
        writer.writerow([
            p["voivodeship"], p["powiat"], p["teryt_code"] or "",
            p["density"] or "",
            p["avg_price_per_m2"] or "",
            p["year"] or "", p["quarter"] or "",
            "|".join(p["sources"]),
        ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="powiats.csv"'},
    )


@main_bp.route("/export/powiats.xlsx")
@login_required
def export_powiats_xlsx():
    powiats = _powiats_payload()
    wb = Workbook()
    ws = wb.active
    ws.title = "Powiaty"
    ws.append([
        "Województwo", "Powiat", "TERYT/BDL",
        "Gęstość (os/km²)", "Cena zł/m²", "Rok", "Kwartał", "Źródła",
    ])
    for p in powiats:
        ws.append([
            p["voivodeship"], p["powiat"], p["teryt_code"] or "",
            p["density"], p["avg_price_per_m2"],
            p["year"], p["quarter"],
            ", ".join(p["sources"]),
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="powiats.xlsx"'},
    )


# ---------------------------------------------------------------------------
# Lightweight JSON API used by the in-page JS
# ---------------------------------------------------------------------------

@main_bp.route("/api/cities")
@login_required
def api_cities():
    return jsonify({
        "data": _cities_payload(include_affordability=True),
        "fetched_at": get_latest_fetched_at().isoformat() if get_latest_fetched_at() else None,
    })


@main_bp.route("/api/trend")
@login_required
def api_trend():
    return jsonify({
        "data": _voivodeship_trend(),
        "fetched_at": get_latest_fetched_at().isoformat() if get_latest_fetched_at() else None,
    })


@main_bp.route("/api/voivodeship-prices")
@login_required
def api_voiv_prices():
    return jsonify({
        "data": _voivodeship_latest_price(),
        "wages": gus_wages_by_voivodeship(),
    })


@main_bp.route("/api/search")
@login_required
@limiter.limit("60 per minute")
def api_search():
    """Autocomplete for powiats + voivodeships."""
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify({"results": []})
    rows = (
        db.session.query(
            RealEstateRecord.powiat,
            RealEstateRecord.voivodeship,
            RealEstateRecord.teryt_code,
        )
        .filter(
            or_(
                func.lower(RealEstateRecord.powiat).contains(q),
                func.lower(RealEstateRecord.voivodeship).contains(q),
            )
        )
        .filter(RealEstateRecord.powiat.isnot(None))
        .distinct()
        .limit(10)
        .all()
    )
    return jsonify({
        "results": [
            {"powiat": r[0], "voivodeship": r[1], "teryt_code": r[2] or ""}
            for r in rows
        ]
    })


@main_bp.route("/api/predict")
@login_required
def api_predict():
    teryt = request.args.get("teryt", "") or None
    return jsonify(forecast_density(teryt_code=teryt, forecast_years=5))


@main_bp.route("/api/jobs/<int:job_id>")
@login_required
def api_job_status(job_id):
    job = BackgroundJob.query.get_or_404(job_id)
    return jsonify(job.to_dict())


@main_bp.route("/api/status")
def api_status():
    latest = get_latest_fetched_at()
    return jsonify({
        "records": RealEstateRecord.query.count(),
        "latest_fetched": latest.isoformat() if latest else None,
        "now": datetime.utcnow().isoformat(),
    })
