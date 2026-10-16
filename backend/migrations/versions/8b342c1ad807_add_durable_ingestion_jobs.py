"""add durable ingestion jobs

Revision ID: 8b342c1ad807
Revises: cfda2163cf45
Create Date: 2026-10-16

"""
from alembic import op
import sqlalchemy as sa


revision = "8b342c1ad807"
down_revision = "cfda2163cf45"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ingestion_jobs",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_token", sa.String(length=36), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id"),
    )
    with op.batch_alter_table("ingestion_jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ingestion_jobs_status"), ["status"], unique=False)


def downgrade():
    with op.batch_alter_table("ingestion_jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ingestion_jobs_status"))
    op.drop_table("ingestion_jobs")
