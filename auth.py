from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db, limiter
from models import User, PasswordResetToken
from forms import (
    LoginForm, RegisterForm,
    PasswordResetRequestForm, PasswordResetConfirmForm,
)
from email_utils import send_password_reset_email

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user is None or not user.check_password(form.password.data):
            flash("Nieprawidłowa nazwa użytkownika lub hasło.", "danger")
            return redirect(url_for("auth.login"))

        login_user(user, remember=form.remember.data)
        flash(f"Witaj z powrotem, {user.username}!", "success")

        next_page = request.args.get("next")
        if not next_page or urlparse(next_page).netloc != "":
            next_page = url_for("main.dashboard")
        return redirect(next_page)

    return render_template("login.html", form=form)


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data, is_admin=False)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash("Konto utworzone — zaloguj się, aby kontynuować.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Zostałeś wylogowany.", "info")
    return redirect(url_for("main.index"))


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

@auth_bp.route("/reset", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def password_reset_request():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        if user:
            token_row = PasswordResetToken.create_for(user)
            db.session.commit()
            send_password_reset_email(user, token_row.token)
        # Always show the same response to avoid email enumeration
        flash(
            "Jeśli konto z tym adresem istnieje, wysłaliśmy link resetujący.",
            "info",
        )
        return redirect(url_for("auth.login"))
    return render_template("reset_request.html", form=form)


@auth_bp.route("/reset/<token>", methods=["GET", "POST"])
@limiter.limit("20 per hour", methods=["POST"])
def password_reset_confirm(token):
    entry = PasswordResetToken.query.filter_by(token=token).first()
    if not entry or not entry.is_valid:
        flash("Link resetujący wygasł lub jest nieprawidłowy.", "danger")
        return redirect(url_for("auth.password_reset_request"))

    form = PasswordResetConfirmForm()
    if form.validate_on_submit():
        entry.user.set_password(form.password.data)
        entry.used_at = datetime.utcnow()
        db.session.commit()
        flash("Hasło zmienione — zaloguj się nowym hasłem.", "success")
        return redirect(url_for("auth.login"))
    return render_template("reset_confirm.html", form=form, token=token)
