"""The database-mode CI server applies exactly the migrations live has.

tests/disposable_database_mode.py mirrors live's migration ledger by hand
(LIVE_MIGRATION_HEAD). When a migration is applied on live (step 8 of
docs/internal-uat-coolify-deploy.md), move LIVE_MIGRATION_HEAD and this test's
expected head in the same change, so CI keeps testing live's schema.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.disposable_database_mode import LIVE_MIGRATION_HEAD, live_migrations  # noqa: E402

# Live's ledger on 20 Nov 2026: 001 to 011 applied (release-25068d9 rehearsal record).
EXPECTED_LIVE_HEAD = "011_quote_ai_spend_postgres.sql"


class LiveMigrationHeadTests(unittest.TestCase):
    def test_database_mode_applies_live_migrations_and_no_later_one(self) -> None:
        self.assertEqual(LIVE_MIGRATION_HEAD, EXPECTED_LIVE_HEAD)
        ids = [item.migration_id for item in live_migrations()]
        self.assertEqual(ids[-1], EXPECTED_LIVE_HEAD)
        self.assertFalse([item for item in ids if item.startswith(("012_", "013_"))], "a migration live has not applied is in the CI database")
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
