"""
PRMAnalyzer — Flask web application for analysing Polish residential
property prices using data fetched live from dane.gov.pl, RCN, NBP and GUS BDL.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, render_template
from sqlalchemy import text

from config import Config
from extensions import db, login_manager, mail, limiter
from models import User
from auth import auth_bp
from routes import main_bp
from api import api_bp
from data_fetcher import refresh_all, ensure_initial_data


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("rcn")


def _scheduled_refresh(app: Flask) -> None:
    with app.app_context():
        log.info("Scheduled refresh starting")
        refresh_all(triggered_by="scheduler")


def _migrate_schema(app: Flask) -> None:
    """SQLite-friendly schema migration: ADD COLUMN for new fields when missing.

    Allows the model to evolve without dropping the user's existing rcn.db.
    Each new column is added only if PRAGMA table_info() shows it missing.
    """
    if not app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        return  # PostgreSQL/MySQL caller is on their own — use Flask-Migrate
    new_columns = {
        "real_estate_records": [
            ("powiat", "VARCHAR(128)"),
            ("teryt_code", "VARCHAR(16)"),
        ],
    }
    with db.engine.connect() as conn:
        for table, cols in new_columns.items():
            existing = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            for col_name, col_type in cols:
                if col_name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"))
                log.info("migrate: added %s.%s (%s)", table, col_name, col_type)
            conn.commit()


def _seed_admin(app: Flask) -> None:
    with app.app_context():
        if not User.query.filter_by(is_admin=True).first():
            admin = User(
                username=app.config["ADMIN_USERNAME"],
                email=app.config["ADMIN_EMAIL"],
                is_admin=True,
            )
            admin.set_password(app.config["ADMIN_PASSWORD"])
            db.session.add(admin)
            db.session.commit()
            log.info(
                "Created default admin user '%s' (change ADMIN_PASSWORD env var!)",
                app.config["ADMIN_USERNAME"],
            )


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)

    # ---- Jinja filters / globals ----
    @app.template_filter("datetime")
    def fmt_datetime(value):
        if not value:
            return "—"
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError:
                return value
        return value.strftime("%Y-%m-%d %H:%M")

    @app.template_filter("price")
    def fmt_price(value):
        try:
            if value is None:
                return "—"
            return f"{float(value):,.0f}".replace(",", " ") + " zł"
        except (TypeError, ValueError):
            return "—"

    @app.template_filter("number")
    def fmt_number(value):
        try:
            if value is None:
                return "—"
            return f"{float(value):,.0f}".replace(",", " ")
        except (TypeError, ValueError):
            return "—"

    @app.template_filter("months")
    def fmt_months(value):
        try:
            if value is None:
                return "—"
            return f"{float(value):,.1f}".replace(",", " ") + " mies."
        except (TypeError, ValueError):
            return "—"

    @app.context_processor
    def inject_globals():
        return {"current_year": datetime.utcnow().year}

    @app.errorhandler(403)
    def err_403(_):
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def err_404(_):
        return render_template("404.html"), 404

    @app.errorhandler(429)
    def err_429(_):
        return render_template("429.html"), 429

    with app.app_context():
        db.create_all()
        _migrate_schema(app)
        _seed_admin(app)
        ensure_initial_data()

    # ---- Background scheduler ----
    if not app.config.get("TESTING"):
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            _scheduled_refresh,
            "interval",
            hours=app.config["REFRESH_INTERVAL_HOURS"],
            args=[app],
            id="refresh-all",
            replace_existing=True,
            next_run_time=None,
        )
        scheduler.start()
        app.config["SCHEDULER"] = scheduler
        log.info(
            "Scheduler started — refresh every %d h",
            app.config["REFRESH_INTERVAL_HOURS"],
        )

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="127.0.0.1", port=port, debug=True, use_reloader=False)
