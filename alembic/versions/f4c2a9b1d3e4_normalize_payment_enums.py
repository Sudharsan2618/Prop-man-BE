"""normalize_payment_enums

Revision ID: f4c2a9b1d3e4
Revises: c1fc379a59f4
Create Date: 2026-03-18 15:15:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f4c2a9b1d3e4"
down_revision: Union[str, None] = "c1fc379a59f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Normalize payment_type_enum labels to lowercase and ensure ADVANCE exists.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_type_enum') THEN
                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_type_enum' AND e.enumlabel = 'RENT'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_type_enum' AND e.enumlabel = 'rent'
                ) THEN
                    ALTER TYPE payment_type_enum RENAME VALUE 'RENT' TO 'rent';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_type_enum' AND e.enumlabel = 'SERVICE'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_type_enum' AND e.enumlabel = 'service'
                ) THEN
                    ALTER TYPE payment_type_enum RENAME VALUE 'SERVICE' TO 'service';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_type_enum' AND e.enumlabel = 'SECURITY_DEPOSIT'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_type_enum' AND e.enumlabel = 'security_deposit'
                ) THEN
                    ALTER TYPE payment_type_enum RENAME VALUE 'SECURITY_DEPOSIT' TO 'security_deposit';
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_type_enum' AND e.enumlabel = 'advance'
                ) THEN
                    ALTER TYPE payment_type_enum ADD VALUE 'advance';
                END IF;
            END IF;
        END $$;
        """
    )

    # Normalize payment_status_enum labels to lowercase and ensure new statuses exist.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_status_enum') THEN
                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'PENDING'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'pending'
                ) THEN
                    ALTER TYPE payment_status_enum RENAME VALUE 'PENDING' TO 'pending';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'OVERDUE'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'overdue'
                ) THEN
                    ALTER TYPE payment_status_enum RENAME VALUE 'OVERDUE' TO 'overdue';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'PAID'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'paid'
                ) THEN
                    ALTER TYPE payment_status_enum RENAME VALUE 'PAID' TO 'paid';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'ESCROWED'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'escrowed'
                ) THEN
                    ALTER TYPE payment_status_enum RENAME VALUE 'ESCROWED' TO 'escrowed';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'REFUNDED'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'refunded'
                ) THEN
                    ALTER TYPE payment_status_enum RENAME VALUE 'REFUNDED' TO 'refunded';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'FAILED'
                ) AND NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'failed'
                ) THEN
                    ALTER TYPE payment_status_enum RENAME VALUE 'FAILED' TO 'failed';
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'awaiting_verification'
                ) THEN
                    ALTER TYPE payment_status_enum ADD VALUE 'awaiting_verification';
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'payment_status_enum' AND e.enumlabel = 'rejected'
                ) THEN
                    ALTER TYPE payment_status_enum ADD VALUE 'rejected';
                END IF;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Enum value normalization is intentionally non-reversible.
    pass
