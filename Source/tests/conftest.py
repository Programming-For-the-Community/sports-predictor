"""
Repo-wide test fixtures -- applies to every test under Source/tests/ via
pytest's conftest.py discovery, with no per-file import needed.
"""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _stub_aws_account_id():
    """library.aws.account.get_account_id() calls sts:GetCallerIdentity on
    first use and caches the result at module level. Pre-seeding that
    cache here means no test anywhere in the suite ever makes a real,
    unmocked AWS call just by exercising a code path that happens to
    touch S3 -- regardless of whether that test explicitly mocks
    get_account_id itself."""
    with patch("library.aws.account._account_id", "123456789012"):
        yield
