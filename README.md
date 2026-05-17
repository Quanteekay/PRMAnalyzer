# PRMAnalyzer

**Analizator polskiego rynku mieszkaniowego** — dashboard webowy z danymi cen
nieruchomości pobieranymi **live** z państwowych rejestrów: GUGiK RCN, GUS BDL i NBP.

Projekt zrealizowany w ramach **Języków Obiektowych I (Python)**.

---

## TL;DR

Aplikacja Flask z dashboardem dla 382 polskich powiatów. Pobiera transakcje
nieruchomości na żywo z WFS Geoportalu (najświeższe dane Q1 2026), gęstość
zaludnienia z BDL (historia 2002–2024) i kursy walut z NBP. Wszystko bez kluczy
API, bez płatnych subskrypcji.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py        # http://127.0.0.1:8080
```

Domyślny admin: `admin` / `admin123`.

---

## Co umie aplikacja

**Dla użytkownika**
- **Dashboard** — tabela 382 powiatów z gęstością zaludnienia, ceną m², filtrami i eksportem CSV/XLSX
- **Mapa choropleth** — 380 powiatów Polski (Leaflet + GeoJSON), kolor po gęstości, filtr per województwo z auto-zoom
- **Porównywarka** — wybór 2–5 powiatów side-by-side, wykres bar gęstość vs cena m²
- **Predykcje** — regresja liniowa gęstości zaludnienia (do 23 lat historii BDL), prognoza 5 lat z 95% CI
- **Watchlist** — przypinanie powiatów do szybkiego podglądu
- **Wyszukiwarka** — autocomplete po nazwie powiatu lub województwa
- **Finanse** — kursy walut NBP (USD/EUR/GBP/CHF) + cena złota + kalkulator zdolności kredytowej
- **Tryb jasny / ciemny** (localStorage)

**Dla administratora**
- Panel admina z dziennikiem odświeżeń, statusem zadań w tle, listą użytkowników
- Ręczne odświeżenie danych (synchroniczne lub async przez APScheduler)
- Generowanie kluczy API per użytkownik
- Audit log każdej operacji refresh

**Dla deweloperów**
- REST API v1 z dokumentacją Swagger UI pod `/api/v1/docs`
- Rate limiting (Flask-Limiter) dla loginu i refresh

---

## Stos technologiczny

| Warstwa | Technologia |
|---|---|
| Backend | Flask 3, SQLAlchemy, Flask-Login, Flask-WTF, Flask-Mail, Flask-Limiter, Flask-RESTX |
| Scheduler | APScheduler (BackgroundScheduler) |
| Baza danych | SQLite (`instance/rcn.db`) |
| Frontend | Jinja2, Chart.js 4, Leaflet 1.9, własny CSS (Geist + Geist Mono) |
| Geo | GeoJSON powiatów (~7 MB w `static/data/`) |
| HTTP | requests |
| ML | NumPy (`polyfit` regresja liniowa) |
| Export | openpyxl |

---

## Źródła danych

| Źródło | Endpoint | Co daje | Świeżość |
|---|---|---|---|
| **GUGiK RCN** | `mapy.geoportal.gov.pl/wss/service/rcn` (WFS) | Indywidualne transakcje lokali — TERYT, cena, powierzchnia, data, rynek | **Q1 2026** (kilka tygodni) |
| **GUS BDL** | `bdl.stat.gov.pl/api/v1` | Katalog 382 powiatów + multi-year dane (2002–2024) | Roczna, ~12 mies. opóźnienia |
| **dane.gov.pl** | `api.dane.gov.pl/1.4` | Katalog otwarych zbiorów (probe + seed fallback) | — |
| **NBP** | `api.nbp.pl` | Kursy walut tabeli A + ceny złota | Dzienna |

**Jak to się składa:**
- BDL daje **strukturę** (lista powiatów, TERYT, woj.) i **gęstość zaludnienia** dla każdego z 382 powiatów
- RCN-WFS dostarcza **rzeczywiste ceny m²** z transakcji ostatnich kwartałów
- `_powiats_payload` joinuje rekordy z obu źródeł po `teryt_code` (TERYT 4-cyfrowy)
- Seed `seed_data.py` to fallback offline (gdy API padają)

**Pokrycie cen m² przez RCN-WFS:** ~127 powiatów z 382 (32%). Niektóre miasta
(m.st. Warszawa, Wrocław, Łódź, Katowice, Szczecin) prowadzą **własne** biura
geodezji i **nie raportują** do centralnego RCN GUGiK — to ograniczenie strukturalne,
nie kodu.

---

## Uruchomienie (macOS)

### Opcja 1 — lokalnie

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py        # → http://127.0.0.1:8080
```

Na macOS port 5000 jest zajęty przez AirPlay, dlatego domyślnie 8080.

### Opcja 2 — launcher

```bash
./run.sh             # tworzy venv, instaluje, uruchamia
```

**Przy pierwszym uruchomieniu** tworzona jest baza `instance/rcn.db`, konto admina
(`admin/admin123`) i wykonywany pierwszy refresh ze wszystkich źródeł
(~2–3 minuty, większość to pobieranie RCN WFS).

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
| `DATABASE_URL` | URL bazy | `sqlite:///instance/rcn.db` |
| `MAIL_SERVER` / `MAIL_PORT` | SMTP | `localhost:1025` |
| `MAIL_SUPPRESS_SEND` | maile do konsoli zamiast wysyłki | `true` |
| `RATELIMIT_DEFAULT` | globalny limit żądań | `300 per hour` |
| `PORT` | port HTTP | `8080` |
| `BDL_API_KEY` | klucz BDL dla wyższego limitu (5000+/dzień) | brak (limit 1000/12h) |

Klucz BDL można uzyskać za darmo na [bdl.stat.gov.pl/bdl/pomoc/api](https://bdl.stat.gov.pl/bdl/pomoc/api).

---

## Struktura projektu

```
PythonProject/
├── app.py               # factory + scheduler + migracja SQLite + Jinja filtry
├── config.py            # ustawienia + env vars
├── extensions.py        # db, login, mail, limiter
├── models.py            # User, RealEstateRecord, DataRefreshLog,
│                        # WatchedCity, PasswordResetToken, ApiKey, BackgroundJob
├── auth.py              # blueprint /auth (login, register, password reset)
├── routes.py            # główny blueprint (dashboard, analytics, mapa, predykcje)
├── api.py               # REST API v1 + Swagger UI
├── forms.py             # Flask-WTF
│
├── data_fetcher.py      # orkiestracja refresh_all + upsert (5 źródeł)
├── bdl_powiats.py       # GUS BDL: katalog powiatów + multi-year indicators
├── rcn_wfs.py           # GUGiK RCN WFS: live transakcje, parse GML, agregacja
├── rcn_dump.py          # RCN dump z dane.gov.pl (skeleton — RCN nie jest tam)
├── external_apis.py     # NBP + GUS BDL (wynagrodzenia per województwo)
│
├── predictions.py       # regresja liniowa gęstości zaludnienia per powiat
├── email_utils.py       # password reset email
├── seed_data.py         # fallback offline (16 stolic woj. + extrapolation 2025 Q4)
│
├── static/
│   ├── css/style.css    # ciemny + jasny motyw, glassmorphism
│   ├── js/main.js       # wykresy, mapa Leaflet, theme toggle, table tools
│   └── data/
│       ├── powiaty.geojson         # 380 polygonów (~7 MB)
│       └── voivodeships.geojson    # 16 województw (~144 KB)
├── templates/           # base + dashboard, mapa, compare, predictions, watchlist, ...
└── instance/rcn.db      # SQLite (tworzona automatycznie)
```

---

## Model danych

| Tabela | Klucz | Opis |
|---|---|---|
| `users` | `id` | konta z hashem hasła (PBKDF2-SHA256) i flagą admina |
| `real_estate_records` | `(voivodeship, powiat, city, market, year, quarter, source)` | rekord agregacyjny ceny m² / wskaźnika BDL per powiat-kwartał |
| `data_refresh_log` | `id` | historia każdego refresh (źródło, success, added/updated, message) |
| `watched_cities` | `(user_id, city)` | watchlist powiatów per użytkownik |
| `password_reset_tokens` | `token` | single-use tokeny resetu hasła |
| `api_keys` | `key` | klucze REST API per użytkownik |
| `background_jobs` | `id` | status zadań async (pending/running/done/error) |

`real_estate_records` ma kolumnę `teryt_code` (4-cyfrowy TERYT powiatu) jako
unifikujący klucz między źródłami — RCN-WFS i BDL łączą się przez nią
w `_powiats_payload`.

---

## Endpointy HTTP

**Publiczne**
- `GET /` — strona główna z KPI
- `GET /auth/login`, `/auth/register`, `/auth/reset/<token>`
- `GET /api/status` — status bazy

**Zalogowany**
- `GET /dashboard` — tabela 382 powiatów + KPI agregowane
- `GET /analytics` — wykresy trendów per województwo (historia 2020–2025)
- `GET /map` — choropleth powiatów (Leaflet)
- `GET /compare?powiat=...` — porównywarka 2–5 powiatów
- `GET /predictions?teryt=NNNN` — predykcja gęstości na 5 lat
- `GET /finance` — kursy NBP + kalkulator
- `GET /watchlist` — watchlist
- `POST /watchlist/toggle` — dodaj/usuń powiat z watchlisty
- `GET /export/powiats.csv` / `.xlsx` — eksport tabeli

**Admin**
- `GET /admin` — panel z dziennikiem, jobami, kluczami, użytkownikami
- `POST /admin/refresh` — synchroniczny refresh ze źródeł
- `POST /admin/refresh-async` — refresh w tle (APScheduler one-off)
- `POST /admin/api-keys` / `POST /admin/api-keys/<id>/revoke`

**Lekkie JSON API (cookie auth)** — używane przez frontend:
- `/api/powiats` — pełen payload dashboardu
- `/api/cities` — legacy, miasta z seedu
- `/api/trend` — historia per województwo
- `/api/voivodeship-prices` — średnia cena per woj. (do mapy v1)
- `/api/search?q=` — autocomplete
- `/api/predict?teryt=` — predykcja
- `/api/jobs/<id>` — status zadania async

**REST API v1** (auth: nagłówek `X-API-Key`, Swagger: `/api/v1/docs`)
- `/api/v1/data/records` — surowe rekordy z filtrami
- `/api/v1/data/cities` — distinct nazwy
- `/api/v1/data/status` — status bazy
- `/api/v1/finance/rates` / `/gold` — NBP
- `/api/v1/stats/wages` / `/affordability` — GUS BDL + wskaźnik dostępności
- `/api/v1/predictions/forecast?teryt=` — prognoza per powiat

---

## Ograniczenia

- **Centralny RCN nie obejmuje wszystkich powiatów.** m.st. Warszawa, Wrocław,
  Łódź, Katowice, Szczecin, Lublin, Bydgoszcz prowadzą własne biura geodezji
  i ich transakcje nie trafiają do GUGiK Geoportalu. Dashboard pokaże dla nich
  gęstość zaludnienia (BDL), ale bez ceny m².
- **BDL ma opóźnienie statystyczne ~12–18 mies.** Najnowsze opublikowane dane
  to typowo rok N-1 lub N-2 w zależności od zmiennej.
- **RCN WFS jest "prymitywny"** — nie obsługuje sortBy, CQL filter, ani
  resultType=hits. Paginujemy naiwnie po `startIndex`, pobierając 100k transakcji
  per refresh (~2–3 minuty HTTP). Niektóre powiaty z mniejszym wolumenem mogą
  mieć małą próbkę (n=1–5) — cena m² może wahać.
- **Predykcje** to prosta regresja liniowa (`numpy.polyfit`) — nie uwzględnia
  zmian makroekonomicznych, demograficznych, migracji. Wynik to wskaźnik,
  nie poradę inwestycyjną.

---

## Możliwości rozwoju

- Migracje bazy (Flask-Migrate / Alembic) zamiast `db.create_all()` + ręcznych ALTER
- PostgreSQL zamiast SQLite (dla wielu użytkowników)
- 2FA TOTP (pyotp + QR)
- Celery + Redis dla async w skali produkcyjnej
- Cache Redis dla wyników API i predykcji
- Alerty email gdy cena w powiecie przekroczy próg
- Eksport raportu PDF (WeasyPrint)
- Internationalization (Flask-Babel) — PL/EN
- Progressive Web App (manifest + service worker)
- Pytest coverage + integration tests
- Dodatkowe zmienne BDL po uzyskaniu klucza API (mieszkania oddane, pozwolenia,
  średnie ceny lokali — wymagają znalezienia poprawnych ID w katalogu BDL)

---

## Bibliografia

1. [GUGiK Geoportal — Rejestr Cen Nieruchomości](https://www.geoportal.gov.pl/pl/dane/rejestr-cen-nieruchomosci/)
2. [GUS BDL — Bank Danych Lokalnych](https://bdl.stat.gov.pl)
3. [NBP — API kursów walut i cen złota](https://api.nbp.pl)
4. [dane.gov.pl — Portal Otwartych Danych Publicznych](https://dane.gov.pl)
5. [Flask Documentation](https://flask.palletsprojects.com/)
6. [Chart.js Documentation](https://www.chartjs.org/docs/latest/)
7. [Leaflet Documentation](https://leafletjs.com/reference.html)
8. [polska-geojson](https://github.com/ppatrzyk/polska-geojson) — granice powiatów
