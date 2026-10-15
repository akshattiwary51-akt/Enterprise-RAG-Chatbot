"""Flask extension singletons."""
from flask import request
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy


def _client_key() -> str:
    # With ProxyFix configured, remote_addr is the real client address.
    return request.remote_addr or "unknown"


db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()
cors = CORS()
limiter = Limiter(key_func=_client_key, default_limits=[])
