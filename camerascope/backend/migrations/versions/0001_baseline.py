"""Baseline: schema of CameraScope 1.0 (public + private cameras).

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False),
        sa.Column("locked_until", TZ),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "provider_syncs",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("last_run_at", TZ),
        sa.Column("last_ok", sa.Boolean(), nullable=False),
        sa.Column("imported", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text()),
    )
    op.create_table(
        "public_cameras",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("camera_type", sa.String(20), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("stream_type", sa.String(20), nullable=False),
        sa.Column("stream_url", sa.Text()),
        sa.Column("snapshot_url", sa.Text()),
        sa.Column("page_url", sa.Text()),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text()),
        sa.Column("license", sa.String(200)),
        sa.Column("snapshot_allowed", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_checked_at", TZ),
        sa.Column("updated_at", TZ, nullable=False),
        sa.UniqueConstraint("provider", "external_id", name="uq_public_camera_provider_ext"),
    )
    op.create_index("ix_public_cameras_lat_lon", "public_cameras", ["latitude", "longitude"])
    op.create_table(
        "private_cameras",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("protocol", sa.String(20), nullable=False),
        sa.Column("vendor", sa.String(40), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer()),
        sa.Column("use_tls", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("credentials_ciphertext", sa.LargeBinary()),
        sa.Column("credentials_nonce", sa.LargeBinary()),
        sa.Column("credentials_key_id", sa.String(40)),
        sa.Column("authorization_attested_at", TZ, nullable=False),
        sa.Column("authorization_basis", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_checked_at", TZ),
        sa.Column("auth_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("updated_at", TZ, nullable=False),
    )
    op.create_index("ix_private_cameras_owner_id", "private_cameras", ["owner_id"])
    op.create_table(
        "saved_cameras",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("public_camera_id", sa.Uuid(), sa.ForeignKey("public_cameras.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_table(
        "view_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("camera_kind", sa.String(10), nullable=False),
        sa.Column("camera_id", sa.Uuid(), nullable=False),
        sa.Column("viewed_at", TZ, nullable=False),
    )
    op.create_index("ix_view_history_user_time", "view_history", ["user_id", "viewed_at"])
    op.create_table(
        "diagnostic_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("camera_kind", sa.String(10), nullable=False),
        sa.Column("camera_id", sa.Uuid(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_diagnostic_runs_user_id", "diagnostic_runs", ["user_id"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("target", sa.String(200)),
        sa.Column("ip", sa.String(64)),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])


def downgrade() -> None:
    for t in ("audit_log", "diagnostic_runs", "view_history", "saved_cameras", "private_cameras",
              "public_cameras", "provider_syncs", "users"):
        op.drop_table(t)
