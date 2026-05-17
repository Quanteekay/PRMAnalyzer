"""
Realistic fallback data used when remote APIs are unreachable.

These values approximate the publicly reported state of Polish residential real
estate market based on NBP and RCN (Rejestr Cen Nieruchomości) summaries
published on dane.gov.pl. They are intentionally close to reality so the
dashboards remain informative even without a live connection.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class SeedRow:
    voivodeship: str
    city: str
    property_type: str
    market: str
    year: int
    quarter: int
    avg_price_per_m2: float | None
    transactions: int
    powiat: str = ""        # "powiat m. Warszawa" / "powiat krakowski" etc.
    teryt_code: str = ""    # BDL unit-id (12 chars) lub TERYT


# 16 voivodeship capitals + a few extra major cities, primary + secondary, 2024 Q4
SEED_RECORDS_2024_Q4: list[SeedRow] = [
    SeedRow("mazowieckie", "Warszawa", "apartment", "primary", 2024, 4, 17850, 4820),
    SeedRow("mazowieckie", "Warszawa", "apartment", "secondary", 2024, 4, 16420, 6310),
    SeedRow("małopolskie", "Kraków", "apartment", "primary", 2024, 4, 16980, 2940),
    SeedRow("małopolskie", "Kraków", "apartment", "secondary", 2024, 4, 14750, 3870),
    SeedRow("dolnośląskie", "Wrocław", "apartment", "primary", 2024, 4, 13620, 2210),
    SeedRow("dolnośląskie", "Wrocław", "apartment", "secondary", 2024, 4, 12380, 2980),
    SeedRow("pomorskie", "Gdańsk", "apartment", "primary", 2024, 4, 14930, 1860),
    SeedRow("pomorskie", "Gdańsk", "apartment", "secondary", 2024, 4, 13280, 2110),
    SeedRow("wielkopolskie", "Poznań", "apartment", "primary", 2024, 4, 12450, 1640),
    SeedRow("wielkopolskie", "Poznań", "apartment", "secondary", 2024, 4, 10980, 1990),
    SeedRow("łódzkie", "Łódź", "apartment", "primary", 2024, 4, 9870, 1120),
    SeedRow("łódzkie", "Łódź", "apartment", "secondary", 2024, 4, 8420, 1480),
    SeedRow("śląskie", "Katowice", "apartment", "primary", 2024, 4, 10240, 980),
    SeedRow("śląskie", "Katowice", "apartment", "secondary", 2024, 4, 8910, 1240),
    SeedRow("zachodniopomorskie", "Szczecin", "apartment", "primary", 2024, 4, 11150, 760),
    SeedRow("zachodniopomorskie", "Szczecin", "apartment", "secondary", 2024, 4, 9620, 920),
    SeedRow("lubelskie", "Lublin", "apartment", "primary", 2024, 4, 10380, 640),
    SeedRow("lubelskie", "Lublin", "apartment", "secondary", 2024, 4, 8740, 810),
    SeedRow("kujawsko-pomorskie", "Bydgoszcz", "apartment", "primary", 2024, 4, 9420, 580),
    SeedRow("kujawsko-pomorskie", "Bydgoszcz", "apartment", "secondary", 2024, 4, 7890, 720),
    SeedRow("podkarpackie", "Rzeszów", "apartment", "primary", 2024, 4, 9810, 520),
    SeedRow("podkarpackie", "Rzeszów", "apartment", "secondary", 2024, 4, 8120, 670),
    SeedRow("warmińsko-mazurskie", "Olsztyn", "apartment", "primary", 2024, 4, 9380, 410),
    SeedRow("warmińsko-mazurskie", "Olsztyn", "apartment", "secondary", 2024, 4, 7920, 530),
    SeedRow("podlaskie", "Białystok", "apartment", "primary", 2024, 4, 10120, 480),
    SeedRow("podlaskie", "Białystok", "apartment", "secondary", 2024, 4, 8560, 620),
    SeedRow("opolskie", "Opole", "apartment", "primary", 2024, 4, 9650, 380),
    SeedRow("opolskie", "Opole", "apartment", "secondary", 2024, 4, 7980, 470),
    SeedRow("świętokrzyskie", "Kielce", "apartment", "primary", 2024, 4, 9120, 340),
    SeedRow("świętokrzyskie", "Kielce", "apartment", "secondary", 2024, 4, 7560, 430),
    SeedRow("lubuskie", "Zielona Góra", "apartment", "primary", 2024, 4, 8920, 310),
    SeedRow("lubuskie", "Zielona Góra", "apartment", "secondary", 2024, 4, 7340, 390),
]

# Ekstrapolacja 2024 Q4 → 2025 Q4 dla 16 stolic województw.
# YoY cen mieszkań w PL 2024→2025 oscyluje wokół +5-10%; bierzemy 7% jako
# konserwatywną wartość. Transakcje +3% (lekkie odbicie wolumenu).
_PRICE_GROWTH_2025 = 1.07
_TX_GROWTH_2025 = 1.03

SEED_RECORDS_2025_Q4: list[SeedRow] = [
    SeedRow(
        voivodeship=r.voivodeship,
        city=r.city,
        property_type=r.property_type,
        market=r.market,
        year=2025,
        quarter=4,
        avg_price_per_m2=round(r.avg_price_per_m2 * _PRICE_GROWTH_2025, 0),
        transactions=int(r.transactions * _TX_GROWTH_2025),
    )
    for r in SEED_RECORDS_2024_Q4
]


# Historical series (2020-2025) for time-trend chart - per voivodeship average
HISTORICAL_TREND: list[SeedRow] = []
_base_2020 = {
    "mazowieckie": 9800,
    "małopolskie": 9200,
    "dolnośląskie": 8100,
    "pomorskie": 8900,
    "wielkopolskie": 7600,
    "łódzkie": 5400,
    "śląskie": 5800,
    "zachodniopomorskie": 6700,
    "lubelskie": 6200,
    "kujawsko-pomorskie": 5300,
    "podkarpackie": 5800,
    "warmińsko-mazurskie": 5500,
    "podlaskie": 6100,
    "opolskie": 5400,
    "świętokrzyskie": 5200,
    "lubuskie": 5100,
}
# Growth coefficients per year (cumulative).
# 2020→2024 ~ +75%; +7% extrapolated for 2025 → 1.87×.
_growth = {2020: 1.00, 2021: 1.14, 2022: 1.32, 2023: 1.52, 2024: 1.75, 2025: 1.87}

for voiv, base in _base_2020.items():
    for year, factor in _growth.items():
        HISTORICAL_TREND.append(
            SeedRow(
                voivodeship=voiv,
                city=None,  # type: ignore[arg-type]
                property_type="apartment",
                market="mixed",
                year=year,
                quarter=4,
                avg_price_per_m2=round(base * factor, 0),
                transactions=int(800 + (factor - 1) * 2200),
            )
        )


def _enrich_powiat(row: SeedRow) -> SeedRow:
    """All 16 voivodeship capitals are 'miasta na prawach powiatu' — fill powiat field."""
    if not row.city or row.powiat:
        return row
    return SeedRow(
        voivodeship=row.voivodeship,
        city=row.city,
        property_type=row.property_type,
        market=row.market,
        year=row.year,
        quarter=row.quarter,
        avg_price_per_m2=row.avg_price_per_m2,
        transactions=row.transactions,
        powiat=f"powiat m. {row.city}",
        teryt_code=row.teryt_code,
    )


def all_seed_rows() -> list[SeedRow]:
    rows = [_enrich_powiat(r) for r in SEED_RECORDS_2024_Q4]
    rows.extend(_enrich_powiat(r) for r in SEED_RECORDS_2025_Q4)
    rows.extend(HISTORICAL_TREND)
    return rows
