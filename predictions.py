"""
Simple linear-regression forecast of average price per m² for the next N quarters.

We avoid scikit-learn — numpy.polyfit is enough for a univariate linear trend
and keeps the dependency footprint small. Confidence interval is approximated
from the residual standard error of the regression.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from extensions import db
from models import RealEstateRecord


def _historical_series(voivodeship: str | None = None) -> list[tuple[float, float]]:
    """
    Build a yearly time series of average price/m² (mixed market).
    Returns list of (year_as_float, price) tuples, sorted by year.
    """
    q = RealEstateRecord.query.filter(RealEstateRecord.market == "mixed")
    if voivodeship:
        q = q.filter(RealEstateRecord.voivodeship == voivodeship)
    rows = q.order_by(RealEstateRecord.year.asc()).all()
    if not rows:
        return []

    # Group by year — average across voivodeships when no filter is set
    by_year: dict[int, list[float]] = {}
    for r in rows:
        by_year.setdefault(r.year, []).append(r.avg_price_per_m2)
    return sorted(
        (float(year), float(sum(vals) / len(vals))) for year, vals in by_year.items()
    )


def forecast_price_series(
    voivodeship: str | None = None,
    forecast_years: int = 3,
) -> dict:
    """
    Fit a simple linear regression on (year → price) and project N years forward.

    Returns:
        {
            "historical": [{"year": int, "price": float}, ...],
            "forecast":   [{"year": int, "price": float, "low": float, "high": float}, ...],
            "slope":      float,   # zł/m² per year
            "r_squared":  float,
        }
    """
    series = _historical_series(voivodeship)
    if len(series) < 2:
        return {"historical": [], "forecast": [], "slope": 0.0, "r_squared": 0.0}

    years = np.array([s[0] for s in series])
    prices = np.array([s[1] for s in series])

    coeffs = np.polyfit(years, prices, deg=1)  # [slope, intercept]
    slope, intercept = float(coeffs[0]), float(coeffs[1])

    predicted = slope * years + intercept
    residuals = prices - predicted
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((prices - prices.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    se = float(np.sqrt(ss_res / max(len(prices) - 2, 1)))

    last_year = int(years.max())
    future_years = list(range(last_year + 1, last_year + forecast_years + 1))
    forecast = []
    for y in future_years:
        price = slope * y + intercept
        forecast.append({
            "year": y,
            "price": round(price, 0),
            "low": round(price - 1.96 * se, 0),
            "high": round(price + 1.96 * se, 0),
        })

    return {
        "historical": [{"year": int(y), "price": round(p, 0)} for y, p in series],
        "forecast": forecast,
        "slope": round(slope, 2),
        "r_squared": round(r2, 4),
        "voivodeship": voivodeship or "Polska (średnia)",
    }


def forecast_for_all_voivodeships(forecast_years: int = 3) -> list[dict]:
    voivs = (
        db.session.query(RealEstateRecord.voivodeship)
        .filter(RealEstateRecord.market == "mixed")
        .distinct()
        .all()
    )
    return [forecast_price_series(v[0], forecast_years) for v in voivs]
