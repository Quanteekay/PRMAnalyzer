import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "rcn-analytics-dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'rcn.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Default admin (created at first run)
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@rcn.local")

    # Data refresh schedule (in hours)
    REFRESH_INTERVAL_HOURS = int(os.environ.get("REFRESH_INTERVAL_HOURS", "6"))

    # External APIs
    DANE_GOV_PL_API = "https://api.dane.gov.pl/1.4"
    GUS_BDL_API = "https://bdl.stat.gov.pl/api/v1"
    NBP_API = "https://api.nbp.pl/api"

    # HTTP timeouts (seconds)
    HTTP_TIMEOUT = 15

    # Email (Flask-Mail)
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "localhost")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "1025"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME") or None
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD") or None
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "false").lower() == "true"
    MAIL_USE_SSL = os.environ.get("MAIL_USE_SSL", "false").lower() == "true"
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "PRMAnalyzer <noreply@rcn.local>")
    # When True, emails are logged to console instead of being sent
    MAIL_SUPPRESS_SEND = os.environ.get("MAIL_SUPPRESS_SEND", "true").lower() == "true"

    # Rate limiting
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "300 per hour")
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
