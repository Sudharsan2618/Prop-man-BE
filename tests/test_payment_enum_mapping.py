import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.payment import Payment, PaymentStatus, PaymentType
from app.services.payment_service import _payment_to_dict


class PaymentEnumMappingTests(unittest.TestCase):
    def test_payment_status_column_uses_lowercase_values(self):
        status_enum = Payment.__table__.c.status.type
        self.assertIn("awaiting_verification", status_enum.enums)
        self.assertNotIn("AWAITING_VERIFICATION", status_enum.enums)

    def test_payment_type_column_uses_lowercase_values(self):
        type_enum = Payment.__table__.c.type.type
        self.assertIn("advance", type_enum.enums)
        self.assertNotIn("ADVANCE", type_enum.enums)

    def test_payment_dict_serializes_awaiting_verification(self):
        payment = SimpleNamespace(
            id="pay_test_1",
            type=PaymentType.RENT,
            label="Rent - March 2026",
            amount=45000,
            breakdown={},
            status=PaymentStatus.AWAITING_VERIFICATION,
            due_date=None,
            paid_date=None,
            screenshot_url=None,
            verified_by=None,
            verified_at=None,
            admin_notes=None,
            rejection_reason=None,
            property_id="prop_1",
            tenant_id="tenant_1",
            owner_id="owner_1",
            created_at=datetime.now(timezone.utc),
        )

        payload = _payment_to_dict(payment)
        self.assertEqual(payload["status"], "awaiting_verification")


if __name__ == "__main__":
    unittest.main()
