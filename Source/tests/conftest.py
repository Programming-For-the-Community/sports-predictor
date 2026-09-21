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


@pytest.fixture
def reset_singletons(monkeypatch):
    """Returns a callable that resets one or more of a Lambda handler
    module's own module-level singletons (FeatureStorage/S3Manager/
    DynamoDBTable, reused across warm invocations -- see
    library.aws.lambda_singletons.get_or_create) for the duration of one
    test.

    Each Source/tests/aws-lambdas/<sport>/conftest.py registers every
    handler module for that sport up front (predict/normalize/
    live-scores/...) regardless of which test file is actually running,
    so those modules' own singleton caches need resetting before every
    test or a mock left over from one test's own storage/table leaks into
    the next. Previously each sport hand-rolled this as its own
    before/after `yield` fixture, duplicated per module per sport;
    monkeypatch.setattr does the same job in one line, since it restores
    each attribute's PRIOR value automatically at teardown -- even if the
    test itself fails partway through -- so a single call here covers
    both "start this test clean" and "don't leak into the next test."

    `module` may be None -- a handler whose own dependencies aren't
    installed in this CI job never got registered by conftest.py's own
    _load_handler, in which case this is a harmless no-op. attr_values
    maps attribute name -> the value to reset it to (usually None, but
    e.g. shared_predict_read's own _predict_invokers resets to {}, not
    None -- see tests/aws-lambdas/shared/conftest.py)."""
    def _reset(module, **attr_values):
        if module is None:
            return
        for name, value in attr_values.items():
            monkeypatch.setattr(module, name, value, raising=False)

    return _reset
