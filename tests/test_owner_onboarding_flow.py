import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.security import generate_temporary_password
from app.models.user import OnboardingStatus, Role, UserStatus
from app.schemas.user import user_to_response


class OwnerOnboardingFlowTests(unittest.TestCase):
    def test_generate_temporary_password_has_minimum_length(self):
        temp = generate_temporary_password(8)
        self.assertGreaterEqual(len(temp), 10)

    def test_user_response_includes_onboarding_fields(self):
        now = datetime.now(timezone.utc)
        user = SimpleNamespace(
            id="usr_1",
            name="NRI Owner",
            email="owner@example.com",
            phone=None,
            password_hash="hashed",
            initials="NO",
            roles=[Role.OWNER.value],
            active_role=Role.OWNER,
            status=UserStatus.PENDING,
            kyc_progress=0,
            onboarding_status=OnboardingStatus.CREATED,
            must_reset_password=True,
            invited_by="admin_1",
            invited_at=now,
            enrolled_at=None,
            created_at=now,
            specialization=None,
            rating=None,
            total_jobs=None,
            portfolio_value=None,
            avatar=None,
            location=None,
        )

        payload = user_to_response(user)
        self.assertEqual(payload["onboarding_status"], "created")
        self.assertTrue(payload["must_reset_password"])
        self.assertEqual(payload["invited_by"], "admin_1")


if __name__ == "__main__":
    unittest.main()
