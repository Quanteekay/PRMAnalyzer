"""
REST API v1 with Swagger UI under /api/v1/.

Auth: Bearer API key passed in the `X-API-Key` header. Keys are issued per
user in the admin panel.
"""

from __future__ import annotations

from datetime import datetime
from functools import wraps

from flask import Blueprint, request, abort
from flask_restx import Api, Resource, fields
from sqlalchemy import or_, func

from extensions import db, limiter
from models import (
    ApiKey, RealEstateRecord, get_latest_fetched_at,
)
from external_apis import (
    nbp_rates, nbp_gold_price, gus_wages_by_voivodeship, affordability_index,
)
from predictions import forecast_density

api_bp = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Authorization: header X-API-Key
authorizations = {
    "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
}

api = Api(
    api_bp,
    version="1.0",
    title="PRMAnalyzer API",
    description=(
        "REST API dla danych rynku nieruchomości w Polsce.\n\n"
        "**Uwierzytelnianie:** klucze API generowane w panelu admina, "
        "przesyłane w nagłówku `X-API-Key`."
    ),
    doc="/docs",
    authorizations=authorizations,
    security="ApiKey",
)


def require_api_key(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if not key:
            api.abort(401, "Missing X-API-Key header")
        row = ApiKey.query.filter_by(key=key, revoked=False).first()
        if not row:
            api.abort(401, "Invalid or revoked API key")
        row.last_used_at = datetime.utcnow()
        db.session.commit()
        return view(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

ns_data = api.namespace("data", description="Surowe dane RCN / dane.gov.pl")
ns_finance = api.namespace("finance", description="Kursy walut i wskaźniki finansowe (NBP)")
ns_stats = api.namespace("stats", description="Statystyki GUS BDL")
ns_pred = api.namespace("predictions", description="Predykcje cen")

# ---------------------------------------------------------------------------
# Models (for swagger doc)
# ---------------------------------------------------------------------------

record_model = api.model("RealEstateRecord", {
    "voivodeship": fields.String,
    "city": fields.String,
    "property_type": fields.String,
    "market": fields.String,
    "year": fields.Integer,
    "quarter": fields.Integer,
    "avg_price_per_m2": fields.Float,
    "transactions": fields.Integer,
    "source": fields.String,
    "fetched_at": fields.String,
})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@ns_data.route("/records")
class RecordsList(Resource):
    @ns_data.doc(params={
        "voivodeship": "Filtr po województwie",
        "city": "Filtr po mieście (LIKE)",
        "year": "Filtr po roku",
        "limit": "Liczba zwracanych rekordów (max 1000)",
    })
    @ns_data.marshal_list_with(record_model)
    @limiter.limit("120 per minute")
    @require_api_key
    def get(self):
        q = RealEstateRecord.query
        if request.args.get("voivodeship"):
            q = q.filter(RealEstateRecord.voivodeship == request.args["voivodeship"])
        if request.args.get("city"):
            q = q.filter(func.lower(RealEstateRecord.city).contains(request.args["city"].lower()))
        if request.args.get("year"):
            try:
                q = q.filter(RealEstateRecord.year == int(request.args["year"]))
            except ValueError:
                api.abort(400, "year must be integer")
        try:
            limit = min(int(request.args.get("limit", "100")), 1000)
        except ValueError:
            limit = 100
        rows = q.order_by(RealEstateRecord.year.desc()).limit(limit).all()
        return [r.to_dict() for r in rows]


@ns_data.route("/cities")
class CitiesList(Resource):
    @ns_data.doc("List distinct cities and voivodeships")
    @limiter.limit("60 per minute")
    @require_api_key
    def get(self):
        rows = (
            db.session.query(RealEstateRecord.city, RealEstateRecord.voivodeship)
            .filter(RealEstateRecord.city.isnot(None))
            .distinct()
            .all()
        )
        return [{"city": r[0], "voivodeship": r[1]} for r in rows]


@ns_data.route("/status")
class Status(Resource):
    @ns_data.doc("Database status")
    def get(self):
        latest = get_latest_fetched_at()
        return {
            "records": RealEstateRecord.query.count(),
            "latest_fetched": latest.isoformat() if latest else None,
            "now": datetime.utcnow().isoformat(),
        }


@ns_finance.route("/rates")
class NbpRates(Resource):
    @ns_finance.doc("Aktualne kursy walut z NBP")
    @limiter.limit("60 per minute")
    @require_api_key
    def get(self):
        return {"rates": nbp_rates()}


@ns_finance.route("/gold")
class NbpGold(Resource):
    @ns_finance.doc("Cena złota z NBP")
    @limiter.limit("60 per minute")
    @require_api_key
    def get(self):
        return {"gold": nbp_gold_price()}


@ns_stats.route("/wages")
class Wages(Resource):
    @ns_stats.doc("Średnie wynagrodzenia wg województw (GUS BDL)")
    @limiter.limit("60 per minute")
    @require_api_key
    def get(self):
        return {"wages": gus_wages_by_voivodeship()}


@ns_stats.route("/affordability")
class Affordability(Resource):
    @ns_stats.doc(params={
        "voivodeship": "Województwo",
        "price": "Cena za m² (zł)",
    })
    @limiter.limit("60 per minute")
    @require_api_key
    def get(self):
        voiv = request.args.get("voivodeship", "")
        try:
            price = float(request.args.get("price", "0"))
        except ValueError:
            api.abort(400, "price must be a number")
        result = affordability_index(price, voiv)
        if not result:
            api.abort(404, "Unknown voivodeship or invalid price")
        return result


@ns_pred.route("/forecast")
class Forecast(Resource):
    @ns_pred.doc(params={
        "teryt": "(opcjonalne) BDL/TERYT kod powiatu do prognozy gęstości",
        "years": "Liczba lat prognozy (1-10)",
    })
    @limiter.limit("30 per minute")
    @require_api_key
    def get(self):
        teryt = request.args.get("teryt", "") or None
        try:
            years = int(request.args.get("years", "5"))
        except ValueError:
            years = 5
        years = max(1, min(years, 10))
        return forecast_density(teryt_code=teryt, forecast_years=years)
