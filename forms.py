from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError

from models import User


class LoginForm(FlaskForm):
    username = StringField("Nazwa użytkownika", validators=[DataRequired(), Length(2, 64)])
    password = PasswordField("Hasło", validators=[DataRequired()])
    remember = BooleanField("Zapamiętaj mnie")
    submit = SubmitField("Zaloguj się")


class RegisterForm(FlaskForm):
    username = StringField("Nazwa użytkownika", validators=[DataRequired(), Length(2, 64)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    password = PasswordField("Hasło", validators=[DataRequired(), Length(min=6, max=128)])
    confirm = PasswordField(
        "Powtórz hasło",
        validators=[DataRequired(), EqualTo("password", message="Hasła muszą być identyczne.")],
    )
    submit = SubmitField("Utwórz konto")

    def validate_username(self, field):
        if User.query.filter_by(username=field.data).first():
            raise ValidationError("Ta nazwa użytkownika jest już zajęta.")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError("Konto z tym adresem email już istnieje.")


class PasswordResetRequestForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=120)])
    submit = SubmitField("Wyślij link resetujący")


class PasswordResetConfirmForm(FlaskForm):
    password = PasswordField("Nowe hasło", validators=[DataRequired(), Length(min=6, max=128)])
    confirm = PasswordField(
        "Powtórz nowe hasło",
        validators=[DataRequired(), EqualTo("password", message="Hasła muszą być identyczne.")],
    )
    submit = SubmitField("Ustaw nowe hasło")
