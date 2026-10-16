"""Configuration loading and startup validation.

Secrets are read from the environment only. Validation errors never print
secret values -- only the *names* of offending settings.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent

MIN_SECRET_LENGTH = 32
PLACEHOLDER_MARKERS = ("change-me", "changeme", "placeholder", "your-", "example")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unsafe."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Setting {name} must be an integer") from exc


def _float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"Setting {name} must be a number") from exc


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def build_config(env_name: str | None = None) -> dict:
    """Build the Flask config dict from environment variables."""
    env = (env_name or _env("APP_ENV", "development") or "development").lower()
    if env not in {"development", "production", "testing"}:
        raise ConfigError("APP_ENV must be one of: development, production, testing")

    data_dir = Path(_env("DATA_DIR", str(REPO_DIR / "data")))

    cfg: dict = {
        "APP_ENV": env,
        "TESTING": env == "testing",
        "SECRET_KEY": _env("SECRET_KEY"),
        "JWT_SECRET_KEY": _env("JWT_SECRET_KEY"),
        "SQLALCHEMY_DATABASE_URI": _env("DATABASE_URL"),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        # --- auth ---
        "JWT_ACCESS_TOKEN_EXPIRES": timedelta(minutes=_int("JWT_ACCESS_MINUTES", 15)),
        "JWT_REFRESH_TOKEN_EXPIRES": timedelta(days=_int("JWT_REFRESH_DAYS", 7)),
        "JWT_ALGORITHM": "HS256",
        "JWT_ENCODE_ISSUER": "enterprise-rag-chatbot",
        "JWT_DECODE_ISSUER": "enterprise-rag-chatbot",
        "JWT_ENCODE_AUDIENCE": "enterprise-rag-api",
        "JWT_DECODE_AUDIENCE": "enterprise-rag-api",
        "JWT_TOKEN_LOCATION": ["headers"],
        "MIN_PASSWORD_LENGTH": _int("MIN_PASSWORD_LENGTH", 12),
        # --- web ---
        "CORS_ORIGINS": [o.strip() for o in (_env("CORS_ORIGINS", "http://localhost:5173") or "").split(",") if o.strip()],
        "MAX_CONTENT_LENGTH": _int("MAX_UPLOAD_MB", 25) * 1024 * 1024,
        "MAX_JSON_BYTES": _int("MAX_JSON_BYTES", 64 * 1024),
        "TRUSTED_PROXY_COUNT": _int("TRUSTED_PROXY_COUNT", 0),
        "RATELIMIT_ENABLED": _bool("RATELIMIT_ENABLED", True),
        "RATELIMIT_STORAGE_URI": _env("RATELIMIT_STORAGE_URI", "memory://"),
        "LOGIN_RATE_LIMIT": _env("LOGIN_RATE_LIMIT", "10 per minute"),
        "CHAT_RATE_LIMIT": _env("CHAT_RATE_LIMIT", "30 per minute"),
        "UPLOAD_RATE_LIMIT": _env("UPLOAD_RATE_LIMIT", "30 per hour"),
        "INGESTION_INLINE": env == "testing",
        "INGESTION_POLL_SECONDS": _float("INGESTION_POLL_SECONDS", 1.0),
        "INGESTION_LEASE_SECONDS": _int("INGESTION_LEASE_SECONDS", 120),
        "INGESTION_MAX_ATTEMPTS": _int("INGESTION_MAX_ATTEMPTS", 3),
        # --- storage ---
        "DATA_DIR": str(data_dir),
        "UPLOAD_DIR": _env("UPLOAD_DIR", str(data_dir / "uploads")),
        "CHROMA_DIR": _env("CHROMA_DIR", str(data_dir / "chroma")),
        "AUDIT_LOG_FILE": _env("AUDIT_LOG_FILE", str(data_dir / "logs" / "audit.log")),
        "MAX_ZIP_UNCOMPRESSED_MB": _int("MAX_ZIP_UNCOMPRESSED_MB", 200),
        "MAX_SPREADSHEET_ROWS": _int("MAX_SPREADSHEET_ROWS", 200_000),
        # --- vector store ---
        "VECTOR_BACKEND": (_env("VECTOR_BACKEND", "chroma") or "chroma").lower(),
        "CHROMA_COLLECTION": _env("CHROMA_COLLECTION", "enterprise_docs"),
        "PINECONE_API_KEY": _env("PINECONE_API_KEY"),
        "PINECONE_INDEX": _env("PINECONE_INDEX"),
        "PINECONE_NAMESPACE": _env("PINECONE_NAMESPACE", "enterprise-docs"),
        # --- models ---
        "EMBEDDINGS_PROVIDER": (_env("EMBEDDINGS_PROVIDER", "openai") or "openai").lower(),
        "EMBEDDING_MODEL": _env("EMBEDDING_MODEL", "text-embedding-3-small"),
        "LLM_PROVIDER": (_env("LLM_PROVIDER", "openai") or "openai").lower(),
        "CHAT_MODEL": _env("CHAT_MODEL", "gpt-4o-mini"),
        "OPENAI_API_KEY": _env("OPENAI_API_KEY"),
        "OPENAI_BASE_URL": _env("OPENAI_BASE_URL"),
        "MODEL_TIMEOUT_SECONDS": _int("MODEL_TIMEOUT_SECONDS", 60),
        # --- RAG tuning ---
        "CHUNK_SIZE": _int("CHUNK_SIZE", 800),
        "CHUNK_OVERLAP": _int("CHUNK_OVERLAP", 120),
        "PARENT_CHUNK_SIZE": _int("PARENT_CHUNK_SIZE", 3000),
        "TOP_K": _int("TOP_K", 6),
        "CANDIDATE_MULTIPLIER": _int("CANDIDATE_MULTIPLIER", 3),
        "SIMILARITY_THRESHOLD": _float("SIMILARITY_THRESHOLD", 0.25),
        "MAX_CONTEXT_CHARS": _int("MAX_CONTEXT_CHARS", 12_000),
        "MAX_QUESTION_CHARS": _int("MAX_QUESTION_CHARS", 4000),
        # --- retention ---
        "CHAT_RETENTION_DAYS": _int("CHAT_RETENTION_DAYS", 90),
        "AUDIT_RETENTION_DAYS": _int("AUDIT_RETENTION_DAYS", 365),
    }

    if cfg["SQLALCHEMY_DATABASE_URI"] is None:
        if env == "production":
            raise ConfigError("DATABASE_URL must be set in production")
        db_path = data_dir / "app.db"
        cfg["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    return cfg


def validate_config(cfg: dict) -> None:
    """Fail fast on missing/unsafe settings. Never echoes secret values."""
    problems: list[str] = []
    env = cfg["APP_ENV"]

    for name in ("SECRET_KEY", "JWT_SECRET_KEY"):
        value = cfg.get(name)
        if not value:
            problems.append(f"{name} is not set")
        elif len(value) < MIN_SECRET_LENGTH:
            problems.append(f"{name} must be at least {MIN_SECRET_LENGTH} characters")
        elif env == "production" and _looks_like_placeholder(value):
            problems.append(f"{name} still looks like a placeholder value")

    if cfg["SECRET_KEY"] and cfg["SECRET_KEY"] == cfg["JWT_SECRET_KEY"]:
        problems.append("SECRET_KEY and JWT_SECRET_KEY must differ")

    if env == "production":
        if cfg["EMBEDDINGS_PROVIDER"] == "fake" or cfg["LLM_PROVIDER"] == "fake":
            problems.append("fake model providers are not allowed in production")
        if cfg["INGESTION_INLINE"]:
            problems.append("INGESTION_INLINE is not allowed in production")
        if cfg["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            problems.append("SQLite is not supported in production; use PostgreSQL")
        if not cfg["CORS_ORIGINS"] or "*" in cfg["CORS_ORIGINS"]:
            problems.append("CORS_ORIGINS must be an explicit allowlist (no wildcard)")

    if cfg["VECTOR_BACKEND"] not in {"chroma", "pinecone"}:
        problems.append("VECTOR_BACKEND must be 'chroma' or 'pinecone'")
    if cfg["VECTOR_BACKEND"] == "pinecone" and not (cfg["PINECONE_API_KEY"] and cfg["PINECONE_INDEX"]):
        problems.append("PINECONE_API_KEY and PINECONE_INDEX are required for the pinecone backend")
    if cfg["EMBEDDINGS_PROVIDER"] not in {"openai", "fake"}:
        problems.append("EMBEDDINGS_PROVIDER must be 'openai' or 'fake'")
    if cfg["LLM_PROVIDER"] not in {"openai", "fake"}:
        problems.append("LLM_PROVIDER must be 'openai' or 'fake'")
    if cfg["CHUNK_OVERLAP"] >= cfg["CHUNK_SIZE"]:
        problems.append("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
    if cfg["CHUNK_SIZE"] > cfg["PARENT_CHUNK_SIZE"]:
        problems.append("PARENT_CHUNK_SIZE must be >= CHUNK_SIZE")
    if cfg["INGESTION_POLL_SECONDS"] <= 0:
        problems.append("INGESTION_POLL_SECONDS must be greater than zero")
    if cfg["INGESTION_LEASE_SECONDS"] <= 0:
        problems.append("INGESTION_LEASE_SECONDS must be greater than zero")
    if cfg["INGESTION_MAX_ATTEMPTS"] < 1:
        problems.append("INGESTION_MAX_ATTEMPTS must be at least one")

    if problems:
        raise ConfigError("Invalid configuration: " + "; ".join(problems))


def model_config_problems(cfg: dict) -> list[str]:
    """Non-fatal: reasons the AI integration cannot be used right now."""
    problems = []
    needs_key = cfg["EMBEDDINGS_PROVIDER"] == "openai" or cfg["LLM_PROVIDER"] == "openai"
    if needs_key and not cfg.get("OPENAI_API_KEY"):
        problems.append(
            "OPENAI_API_KEY is not set. Set it in backend/.env (see README: 'Model configuration') "
            "or use EMBEDDINGS_PROVIDER=fake / LLM_PROVIDER=fake for local development only."
        )
    return problems
