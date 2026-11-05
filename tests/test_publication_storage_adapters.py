"""Offline controls only; these do not qualify PostgreSQL publication."""
import unittest

from tests.publication_storage_support import PublicationStorageCases


class PublicationStorageAdapterTest(PublicationStorageCases, unittest.TestCase):
    database_url = None


if __name__ == "__main__":
    unittest.main()
