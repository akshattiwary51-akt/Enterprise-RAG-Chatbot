"""Operational CLI: `flask --app wsgi <command>`.

Initial administrator bootstrap is an explicit, one-time, operator action -- there is no
default account and no public registration.
"""
from __future__ import annotations

import getpass
import os
from datetime import timedelta
from pathlib import Path

import click
from flask import Flask

from .core.security import hash_password, validate_password_strength
from .extensions import db
from .models import AuditEvent, Conversation, Document, DocStatus, RevokedToken, Role, User, utcnow
from .services.permissions.policy import seed_roles_and_permissions


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-roles")
    def seed_roles():
        """Create the built-in roles and capability permissions (idempotent)."""
        seed_roles_and_permissions()
        click.echo("Roles and permissions are in place.")

    @app.cli.command("create-admin")
    @click.option("--email", required=True)
    @click.option("--name", "full_name", default="Administrator", show_default=True)
    def create_admin(email: str, full_name: str):
        """Create an administrator. The password is read from $ADMIN_PASSWORD or prompted (never an argument)."""
        seed_roles_and_permissions()
        email = email.strip().lower()
        if User.query.filter_by(email=email).first():
            raise click.ClickException("A user with that email already exists")
        password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
        problem = validate_password_strength(password, app.config["MIN_PASSWORD_LENGTH"])
        if problem:
            raise click.ClickException(problem)
        admin_role = Role.query.filter_by(name="admin").one()
        db.session.add(User(email=email, full_name=full_name, password_hash=hash_password(password),
                            roles=[admin_role]))
        db.session.commit()
        click.echo(f"Administrator {email} created.")

    @app.cli.command("purge-expired")
    def purge_expired():
        """Apply retention: delete old conversations, audit events and expired revoked-token rows."""
        from .services.maintenance import purge_expired as run

        r = run()
        click.echo(f"Purged {r['conversations']} conversations, {r['audit_events']} audit events, "
                   f"{r['revoked_tokens']} expired token records.")

    @app.cli.command("reindex-all")
    def reindex_all():
        """Re-embed every non-deleting document (e.g. after changing EMBEDDING_MODEL: reset the Chroma dir first)."""
        from .services.ingestion.pipeline import ingest_document

        for doc in Document.query.filter(Document.status != DocStatus.DELETING.value).all():
            result = ingest_document(doc.id)
            click.echo(f"{doc.id} {result.status}")

    @app.cli.command("seed-demo")
    @click.option("--password", envvar="DEMO_PASSWORD", required=True,
                  help="Password for all demo users (>= MIN_PASSWORD_LENGTH). DEVELOPMENT ONLY.")
    def seed_demo(password: str):
        """Load clearly-labelled synthetic demo users and documents (development only)."""
        if app.config["APP_ENV"] == "production":
            raise click.ClickException("seed-demo is disabled in production")
        from .demo import load_demo

        load_demo(password, Path(app.config["DATA_DIR"]) / "sample")
