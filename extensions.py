from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Zaloguj się, aby uzyskać dostęp do tej sekcji."
login_manager.login_message_category = "warning"

mail = Mail()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour"],
    storage_uri="memory://",
    strategy="fixed-window",
)
