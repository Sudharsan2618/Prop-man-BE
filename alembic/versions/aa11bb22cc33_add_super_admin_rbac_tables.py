"""add_super_admin_rbac_tables

Revision ID: aa11bb22cc33
Revises: c7d8e9f0a1b2
Create Date: 2026-03-24 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "aa11bb22cc33"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ROLE_ENUM = sa.Enum(
    "tenant",
    "owner",
    "provider",
    "manager",
    "admin",
    "super_admin",
    name="role_enum",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'role_enum') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'role_enum' AND e.enumlabel = 'manager'
                ) THEN
                    ALTER TYPE role_enum ADD VALUE 'manager';
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'role_enum' AND e.enumlabel = 'super_admin'
                ) THEN
                    ALTER TYPE role_enum ADD VALUE 'super_admin';
                END IF;
            END IF;
        END $$;
        """
    )

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", ROLE_ENUM, nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("entity", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("code", name="uq_permissions_code"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("permission_id", sa.Integer(), sa.ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name="pk_role_permissions"),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=30), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("assigned_by", sa.String(length=30), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"], unique=False)

    op.execute(
        """
        INSERT INTO roles (name, description)
        VALUES
            ('super_admin', 'Platform super administrator with full access'),
            ('admin', 'Legacy admin role retained for backward compatibility'),
            ('manager', 'Property manager role'),
            ('owner', 'Property owner role'),
            ('tenant', 'Tenant role'),
            ('provider', 'Service provider role')
        ON CONFLICT (name) DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO permissions (code, description, entity, action)
        VALUES
            ('user.create', 'Create user accounts', 'user', 'create'),
            ('user.read', 'Read user accounts', 'user', 'read'),
            ('user.update', 'Update user accounts', 'user', 'update'),
            ('user.delete', 'Delete user accounts', 'user', 'delete'),
            ('property.create', 'Create properties', 'property', 'create'),
            ('property.read', 'Read properties', 'property', 'read'),
            ('property.update', 'Update properties', 'property', 'update'),
            ('property.delete', 'Delete properties', 'property', 'delete'),
            ('permission.create', 'Create permissions', 'permission', 'create'),
            ('permission.read', 'Read permissions', 'permission', 'read'),
            ('permission.update', 'Update permissions', 'permission', 'update'),
            ('permission.delete', 'Delete permissions', 'permission', 'delete'),
            ('role.create', 'Create roles', 'role', 'create'),
            ('role.read', 'Read roles', 'role', 'read'),
            ('role.update', 'Update roles', 'role', 'update'),
            ('role.delete', 'Delete roles', 'role', 'delete'),
            ('payment.read', 'Read payments', 'payment', 'read'),
            ('payment.update', 'Update payments', 'payment', 'update'),
            ('job.read', 'Read jobs', 'job', 'read'),
            ('job.update', 'Update jobs', 'job', 'update')
        ON CONFLICT (code) DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        CROSS JOIN permissions p
        WHERE r.name = 'super_admin'
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code IN (
            'user.read', 'user.update',
            'property.read', 'property.update',
            'payment.read', 'payment.update',
            'job.read', 'job.update'
        )
        WHERE r.name = 'manager'
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code IN (
            'property.read', 'payment.read', 'job.read'
        )
        WHERE r.name = 'owner'
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code IN (
            'property.read', 'job.read', 'payment.read'
        )
        WHERE r.name = 'tenant'
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id
        FROM roles r
        JOIN permissions p ON p.code IN (
            'job.read', 'job.update', 'payment.read'
        )
        WHERE r.name = 'provider'
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT u.id, r.id
        FROM users u
        JOIN roles r ON r.name::text = u.active_role::text
        ON CONFLICT DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO user_roles (user_id, role_id)
        SELECT DISTINCT u.id, r.id
        FROM users u
        CROSS JOIN LATERAL unnest(u.roles) AS role_name
        JOIN roles r ON r.name::text = role_name
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_user_roles_role_id", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_index("ix_roles_name", table_name="roles")
    op.drop_table("roles")

    # Enum value removals are intentionally non-reversible in PostgreSQL.
