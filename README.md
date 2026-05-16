# PRMAnalyzer

Pełnowymiarowa aplikacja webowa do analizy polskiego rynku nieruchomości.
Łączy dane z **dane.gov.pl**, **RCN** (Rejestr Cen Nieruchomości), **NBP** (kursy
walut, cena złota) oraz **GUS BDL** (wynagrodzenia regionalne) i prezentuje
je w nowoczesnym interfejsie z Chart.js i Leaflet.

Projekt zrealizowany w ramach przedmiotu **Języki Obiektowe I (Python)** —
zgodny z założeniami z pliku `zagadnienia.pdf` i znacznie ponad nie rozbudowany.

---

## Funkcje

### Dla użytkownika
- **System logowania** oparty o bazę danych (Flask-Login + PBKDF2)
- **Rejestracja**, **reset hasła** przez email (Flask-Mail, w dev maile logowane do konsoli)
- **Dashboard** z KPI, tabelą miast, wskaźnikiem dostępności mieszkania
- **Sortowanie, filtrowanie, paginacja** tabeli + **eksport CSV/XLSX**
- **Wyszukiwarka** miast i województw (live API z autocomplete)
- **Mapa choropleth Polski** (Leaflet + GeoJSON woj.) z popupami
- **Porównywarka 2-5 miast** side-by-side z wykresami i wskaźnikami
- **Predykcje cen** na 3 lata z przedziałem ufności (regresja liniowa)
- **Strona finansów** — kursy walut NBP + cena złota + kalkulator zdolności
- **Watchlist** ulubionych miast
- **Tryb jasny / ciemny** (localStorage)

### Dla administratora
- **Panel admina** z dziennikiem odświeżeń, jobami w tle, użytkownikami
- **Ręczne odświeżenie danych** — synchroniczne lub w tle (APScheduler)
- **Klucze API** generowane per użytkownik do REST API
- **Audit log** każdej operacji

### Dla deweloperów
- **REST API v1** z dokumentacją **Swagger UI** pod `/api/v1/docs`
- **Rate limiting** (Flask-Limiter) dla login/refresh/API

---

## Stos technologiczny

| Warstwa | Technologia |
|---|---|
| Backend | Flask 3, Flask-SQLAlchemy, Flask-Login, Flask-WTF, Flask-Mail, Flask-Limiter, Flask-RESTX |
| Scheduler | APScheduler (BackgroundScheduler) |
| Baza danych | SQLite (`instance/rcn.db`) |
| Frontend | Jinja2, Chart.js 4, Leaflet 1.9, własny CSS (Geist + Geist Mono) |
| Mapa | Leaflet + GeoJSON województw |
| HTTP | requests |
| ML | NumPy (regresja liniowa) |
| Export | openpyxl |
| Źródła danych | dane.gov.pl · RCN · NBP · GUS BDL |

---

## Uruchomienie (macOS)

### Opcja 1 — lokalnie (Python venv)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

→ **http://127.0.0.1:8080** (na macOS port 5000 jest zajęty przez AirPlay).

Przy pierwszym uruchomieniu:
- tworzona jest baza `instance/rcn.db`
- zakładane jest konto admina: **`admin` / `admin123`**
- wykonywane jest pierwsze pobranie danych ze źródeł

### Opcja 2 — szybki launcher

```bash
./run.sh
```

Tworzy venv, instaluje zależności, uruchamia.

---

## Konfiguracja

Wszystkie ustawienia w `config.py`, nadpisywalne env varami:

| Zmienna | Opis | Domyślna |
|---|---|---|
| `SECRET_KEY` | klucz sesji Flask | `rcn-analytics-dev-secret-change-me` |
| `ADMIN_USERNAME` | login admina | `admin` |
| `ADMIN_PASSWORD` | hasło admina | `admin123` |
| `ADMIN_EMAIL` | email admina | `admin@rcn.local` |
| `REFRESH_INTERVAL_HOURS` | częstotliwość auto-refresh | `6` |
| `DATABASE_URL` | URL bazy (SQLite domyślnie) | sqlite local |
| `MAIL_SERVER` / `MAIL_PORT` | SMTP | localhost:1025 |
| `MAIL_SUPPRESS_SEND` | logowanie maili zamiast wysyłania | `true` |
| `RATELIMIT_DEFAULT` | globalny limit żądań | `300 per hour` |
| `PORT` | port HTTP | `8080` |

---

## Struktura projektu

```
PythonProject/
├── app.py                  # factory + scheduler + filtry + handlery błędów
├── config.py               # ustawienia + env vars
├── extensions.py           # db, login, mail, limiter
├── models.py               # User, RealEstateRecord, DataRefreshLog,
│                           # WatchedCity, PasswordResetToken, ApiKey, BackgroundJob
├── auth.py                 # blueprint /auth (login, register, reset)
├── routes.py               # główny blueprint (dashboard, analytics, …)
├── api.py                  # REST API v1 + Swagger
├── forms.py                # Flask-WTF
├── data_fetcher.py         # dane.gov.pl, RCN, upsert, async refresh
├── external_apis.py        # NBP + GUS BDL
├── predictions.py          # regresja liniowa numpy.polyfit
├── email_utils.py          # password reset email
├── seed_data.py            # realistyczny fallback dataset
├── requirements.txt
├── run.sh                  # quick launcher (venv + install + run)
├── static/
│   ├── css/style.css       # ciemny + jasny motyw, glassmorphism, Chart.js + Leaflet
│   ├── js/main.js          # wykresy, mapa, theme toggle, table tools
│   └── data/voivodeships.geojson
├── templates/              # base + 13 stron + 3 błędy
└── instance/rcn.db         # SQLite (tworzona automatycznie)
```

---

## Model danych

| Tabela | Opis |
|---|---|
| `users` | konta z hashowanym hasłem (PBKDF2) i flagą admina |
| `real_estate_records` | observacje cen m² (rok, kwartał, woj., miasto, rynek) |
| `data_refresh_log` | historia odświeżeń ze schedulera lub admin |
| `watched_cities` | watchlist miast per użytkownik (many-to-many) |
| `password_reset_tokens` | tokeny resetujące hasło, single-use |
| `api_keys` | klucze do REST API per użytkownik |
| `background_jobs` | status zadań async (`pending` / `running` / `done` / `error`) |

---

## Endpointy HTTP

### Publiczne / member
| Ścieżka | Dostęp | Opis |
|---|---|---|
| `/` | publiczny | strona główna z KPI |
| `/auth/login` | publiczny | logowanie |
| `/auth/register` | publiczny | rejestracja |
| `/auth/reset` | publiczny | wniosek o reset hasła |
| `/auth/reset/<token>` | publiczny | ustawienie nowego hasła |
| `/auth/logout` | zalogowany | wylogowanie |
| `/dashboard` | zalogowany | KPI + tabela + wykresy + watchlist |
| `/analytics` | zalogowany | wykresy trendów i porównań |
| `/map` | zalogowany | mapa choropleth Polski |
| `/compare` | zalogowany | porównywarka 2-5 miast |
| `/predictions` | zalogowany | predykcje cen na 3 lata |
| `/finance` | zalogowany | kursy NBP + kalkulator |
| `/watchlist` | zalogowany | moja watchlist |
| `/watchlist/toggle` (POST) | zalogowany | dodaj/usuń miasto z watchlist |
| `/export/cities.csv` | zalogowany | eksport CSV |
| `/export/cities.xlsx` | zalogowany | eksport XLSX |

### Admin
| Ścieżka | Opis |
|---|---|
| `/admin` | panel z dziennikiem, jobami, kluczami, użytkownikami |
| `/admin/refresh` (POST) | synchroniczny refresh ze źródeł |
| `/admin/refresh-async` (POST) | refresh w tle (APScheduler one-off) |
| `/admin/api-keys` (POST) | wygenerowanie klucza API |
| `/admin/api-keys/<id>/revoke` (POST) | odwołanie klucza |

### REST API v1 (Swagger UI: `/api/v1/docs`)
| Ścieżka | Auth | Opis |
|---|---|---|
| `/api/v1/data/records` | `X-API-Key` | dane RCN z filtrami |
| `/api/v1/data/cities` | `X-API-Key` | lista miast/województw |
| `/api/v1/data/status` | publiczny | status bazy |
| `/api/v1/finance/rates` | `X-API-Key` | kursy NBP |
| `/api/v1/finance/gold` | `X-API-Key` | cena złota |
| `/api/v1/stats/wages` | `X-API-Key` | wynagrodzenia GUS |
| `/api/v1/stats/affordability` | `X-API-Key` | wskaźnik dostępności |
| `/api/v1/predictions/forecast` | `X-API-Key` | prognoza |

### Lekkie JSON API (cookie auth)
`/api/cities`, `/api/trend`, `/api/voivodeship-prices`, `/api/search?q=`,
`/api/predict`, `/api/jobs/<id>`, `/api/status`

---

## Źródła danych

1. **dane.gov.pl** — Portal Otwartych Danych Publicznych
2. **RCN** — Rejestr Cen Nieruchomości (publikowany przez dane.gov.pl)
3. **NBP** — kursy walut + cena złota (https://api.nbp.pl)
4. **GUS BDL** — Bank Danych Lokalnych, wynagrodzenia (https://bdl.stat.gov.pl/api)

Wszystkie API są publiczne, bez wymogu rejestracji. Gdy API jest niedostępne,
używany jest realistyczny zestaw seed (NBP/RCN — stan 2024 Q4).

---

## Tryb deweloperski — testowanie mailem

Domyślnie `MAIL_SUPPRESS_SEND=true` — maile (np. reset hasła) trafiają do
logów aplikacji zamiast realnego serwera SMTP.

---

## Możliwości dalszego rozwoju

- Migracje bazy (Flask-Migrate / Alembic) zamiast `db.create_all()`
- PostgreSQL zamiast SQLite (dla wielu użytkowników)
- 2FA TOTP (pyotp + QR)
- Celery + Redis dla async w skali produkcyjnej
- Cache Redis dla wyników API i predykcji
- Alerty email gdy cena w mieście przekroczy próg
- Eksport raportu PDF (WeasyPrint)
- Internationalization (Flask-Babel) — PL/EN
- Progressive Web App (manifest + service worker)
- Pytest coverage + integration tests

## Bibliografia

1. dane.gov.pl — Portal Otwartych Danych Publicznych
2. GUGiK — Rejestr Cen Nieruchomości
3. NBP — API kursów walut i cen złota
4. GUS — Bank Danych Lokalnych (BDL)
5. Flask Documentation
6. Chart.js Documentation
7. Leaflet Documentation
8. APScheduler / Flask-Limiter / Flask-RESTX docs
