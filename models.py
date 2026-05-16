import secrets
from datetime import datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        # pbkdf2 is always available; scrypt requires modern OpenSSL (not LibreSSL on macOS)
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:
        return f"<User {self.username} admin={self.is_admin}>"


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, int(user_id))


class RealEstateRecord(db.Model):
    """Single observation: average property price for a region/period/type."""

    __tablename__ = "real_estate_records"

    id = db.Column(db.Integer, primary_key=True)
    voivodeship = db.Column(db.String(64), nullable=False, index=True)
    powiat = db.Column(db.String(128), nullable=True, index=True)
    teryt_code = db.Column(db.String(16), nullable=True, index=True)  # BDL unit-id (12 chars) lub TERYT
    city = db.Column(db.String(128), nullable=True, index=True)
    property_type = db.Column(db.String(32), nullable=False)  # apartment / house / plot
    market = db.Column(db.String(32), nullable=False)  # primary / secondary / mixed
    year = db.Column(db.Integer, nullable=False, index=True)
    quarter = db.Column(db.Integer, nullable=True)
    avg_price_per_m2 = db.Column(db.Float, nullable=True)
    transactions = db.Column(db.Integer, nullable=True)
    source = db.Column(db.String(32), nullable=False)  # RCN / dane.gov.pl / GUS-BDL
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "voivodeship": self.voivodeship,
            "powiat": self.powiat,
            "teryt_code": self.teryt_code,
            "city": self.city,
            "property_type": self.property_type,
            "market": self.market,
            "year": self.year,
            "quarter": self.quarter,
            "avg_price_per_m2": self.avg_price_per_m2,
            "transactions": self.transactions,
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


class DataRefreshLog(db.Model):
    """Log of every refresh operation (scheduled or manual)."""

    __tablename__ = "data_refresh_log"

    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(32), nullable=False)
    records_added = db.Column(db.Integer, default=0)
    records_updated = db.Column(db.Integer, default=0)
    success = db.Column(db.Boolean, default=False)
    triggered_by = db.Column(db.String(64), default="scheduler")  # scheduler / username
    message = db.Column(db.Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "source": self.source,
            "records_added": self.records_added,
            "records_updated": self.records_updated,
            "success": self.success,
            "triggered_by": self.triggered_by,
            "message": self.message,
        }


def get_latest_fetched_at():
    """Return the most recent fetched_at timestamp across all data, or None."""
    record = (
        db.session.query(RealEstateRecord)
        .order_by(RealEstateRecord.fetched_at.desc())
        .first()
    )
    return record.fetched_at if record else None


# ---------------------------------------------------------------------------
# Watchlist & related user features
# ---------------------------------------------------------------------------

class WatchedCity(db.Model):
    """User's pinned cities for quick access in dashboard."""

    __tablename__ = "watched_cities"
    __table_args__ = (db.UniqueConstraint("user_id", "city", name="uq_user_city"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    city = db.Column(db.String(128), nullable=False)
    added_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("watched_cities", lazy="dynamic"))


class PasswordResetToken(db.Model):
    """Single-use token for password reset emails."""

    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref="reset_tokens")

    @classmethod
    def create_for(cls, user, ttl_minutes: int = 60):
        token = secrets.token_urlsafe(32)
        entry = cls(
            user_id=user.id,
            token=token,
            expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
        )
        db.session.add(entry)
        return entry

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > datetime.utcnow()


class ApiKey(db.Model):
    """API key for REST API consumers (issued per user)."""

    __tablename__ = "api_keys"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    label = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked = db.Column(db.Boolean, default=False, nullable=False)

    user = db.relationship("User", backref="api_keys")

    @classmethod
    def issue(cls, user, label: str = "default"):
        key = secrets.token_urlsafe(32)
        entry = cls(user_id=user.id, key=key, label=label)
        db.session.add(entry)
        return entry


class BackgroundJob(db.Model):
    """Status of asynchronous jobs (e.g. async refresh queued from admin)."""

    __tablename__ = "background_jobs"

    id = db.Column(db.Integer, primary_key=True)
    job_type = db.Column(db.String(32), nullable=False)
    status = db.Column(db.String(16), default="pending", nullable=False)  # pending/running/done/error
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    triggered_by = db.Column(db.String(64), nullable=True)
    result = db.Column(db.Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_type": self.job_type,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "triggered_by": self.triggered_by,
            "result": self.result,
        }
