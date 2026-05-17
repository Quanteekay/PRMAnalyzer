"""
Linear-regression forecast of per-powiat density (BDL — osoby/km²).

We avoid scikit-learn — numpy.polyfit is enough for a univariate linear trend
and keeps the dependency footprint small. Confidence band is approximated from
the residual standard error of the regression.
"""

from __future__ import annotations

import numpy as np

from extensions import db
from models import RealEstateRecord


def _density_series(teryt_code: str | None = None, powiat: str | None = None) -> list[tuple[float, float]]:
    """Yearly density series from BDL rows. Either teryt_code or powiat name."""
    q = RealEstateRecord.query.filter(RealEstateRecord.source == "GUS-BDL")
    if teryt_code:
        q = q.filter(RealEstateRecord.teryt_code == teryt_code)
    elif powiat:
        q = q.filter(RealEstateRecord.powiat == powiat)
    else:
        return []
    rows = q.filter(RealEstateRecord.transactions.isnot(None)).order_by(RealEstateRecord.year.asc()).all()
    return [(float(r.year), float(r.transactions)) for r in rows if r.transactions]


def forecast_density(
    teryt_code: str | None = None,
    powiat: str | None = None,
    forecast_years: int = 5,
) -> dict:
    """Fit linear regression on (year → density), project N years forward."""
    series = _density_series(teryt_code=teryt_code, powiat=powiat)
    if len(series) < 3:
        return {
            "historical": [],
            "forecast": [],
            "slope": 0.0,
            "r_squared": 0.0,
            "teryt_code": teryt_code,
            "powiat": powiat,
        }

    years = np.array([s[0] for s in series])
    values = np.array([s[1] for s in series])

    coeffs = np.polyfit(years, values, deg=1)
    slope, intercept = float(coeffs[0]), float(coeffs[1])

    predicted = slope * years + intercept
    residuals = values - predicted
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((values - values.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    se = float(np.sqrt(ss_res / max(len(values) - 2, 1)))

    last_year = int(years.max())
    future_years = list(range(last_year + 1, last_year + forecast_years + 1))
    forecast = []
    for y in future_years:
        v = slope * y + intercept
        forecast.append({
            "year": y,
            "value": round(v, 1),
            "low": round(v - 1.96 * se, 1),
            "high": round(v + 1.96 * se, 1),
        })

    return {
        "historical": [{"year": int(y), "value": round(v, 1)} for y, v in series],
        "forecast": forecast,
        "slope": round(slope, 3),
        "r_squared": round(r2, 4),
        "teryt_code": teryt_code,
        "powiat": powiat,
    }
