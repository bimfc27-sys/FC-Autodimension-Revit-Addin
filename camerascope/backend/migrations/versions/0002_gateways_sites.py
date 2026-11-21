"""Local Camera Gateways, DVR/NVR devices, sites (buildings), groups, favorites.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "gateways",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("token_hash", sa.String(64)),
        sa.Column("enrollment_code_hash", sa.String(64)),
        sa.Column("enrollment_expires_at", TZ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_seen_at", TZ),
        sa.Column("agent_version", sa.String(40)),
        sa.Column("allowed_subnets", sa.JSON(), nullable=False),
        sa.Column("remote_address", sa.String(64)),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_gateways_owner_id", "gateways", ["owner_id"])
    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(300)),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_table(
        "site_members",
        sa.Column("site_id", sa.Uuid(), sa.ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("added_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_site_members_user_id", "site_members", ["user_id"])
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("gateway_id", sa.Uuid(), sa.ForeignKey("gateways.id", ondelete="RESTRICT")),
        sa.Column("site_id", sa.Uuid(), sa.ForeignKey("sites.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("protocol", sa.String(20), nullable=False),
        sa.Column("vendor", sa.String(40), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer()),
        sa.Column("rtsp_port", sa.Integer()),
        sa.Column("use_tls", sa.Boolean(), nullable=False),
        sa.Column("credentials_ciphertext", sa.LargeBinary()),
        sa.Column("credentials_nonce", sa.LargeBinary()),
        sa.Column("credentials_key_id", sa.String(40)),
        sa.Column("authorization_attested_at", TZ, nullable=False),
        sa.Column("authorization_basis", sa.String(40), nullable=False),
        sa.Column("model", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_discovery_at", TZ),
        sa.Column("auth_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_devices_owner_id", "devices", ["owner_id"])
    op.create_index("ix_devices_gateway_id", "devices", ["gateway_id"])

    with op.batch_alter_table("private_cameras") as b:
        b.add_column(sa.Column("gateway_id", sa.Uuid(), sa.ForeignKey("gateways.id", ondelete="RESTRICT")))
        b.add_column(sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id", ondelete="CASCADE")))
        b.add_column(sa.Column("site_id", sa.Uuid(), sa.ForeignKey("sites.id", ondelete="SET NULL")))
        b.add_column(sa.Column("reachable", sa.Boolean()))
        b.add_column(sa.Column("reachable_checked_at", TZ))
    op.create_index("ix_private_cameras_gateway_id", "private_cameras", ["gateway_id"])
    op.create_index("ix_private_cameras_device_id", "private_cameras", ["device_id"])
    op.create_index("ix_private_cameras_site_id", "private_cameras", ["site_id"])

    op.create_table(
        "camera_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.create_index("ix_camera_groups_owner_id", "camera_groups", ["owner_id"])
    op.create_table(
        "camera_group_items",
        sa.Column("group_id", sa.Uuid(), sa.ForeignKey("camera_groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("camera_kind", sa.String(10), primary_key=True),
        sa.Column("camera_id", sa.Uuid(), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
    )
    op.create_table(
        "favorites",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("camera_kind", sa.String(10), primary_key=True),
        sa.Column("camera_id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", TZ, nullable=False),
    )
    # Saved public cameras become favorites.
    op.execute(
        "INSERT INTO favorites (user_id, camera_kind, camera_id, created_at) "
        "SELECT user_id, 'public', public_camera_id, created_at FROM saved_cameras"
    )
    op.drop_table("saved_cameras")


def downgrade() -> None:
    op.create_table(
        "saved_cameras",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("public_camera_id", sa.Uuid(), sa.ForeignKey("public_cameras.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", TZ, nullable=False),
    )
    op.execute(
        "INSERT INTO saved_cameras SELECT user_id, camera_id, created_at FROM favorites "
        "WHERE camera_kind = 'public' AND camera_id IN (SELECT id FROM public_cameras)"
    )
    for t in ("favorites", "camera_group_items", "camera_groups"):
        op.drop_table(t)
    for i in ("ix_private_cameras_site_id", "ix_private_cameras_device_id", "ix_private_cameras_gateway_id"):
        op.drop_index(i, table_name="private_cameras")
    with op.batch_alter_table("private_cameras") as b:
        for c in ("reachable_checked_at", "reachable", "site_id", "device_id", "gateway_id"):
            b.drop_column(c)
    op.drop_table("devices")
    op.drop_table("site_members")
    op.drop_table("sites")
    op.drop_table("gateways")
