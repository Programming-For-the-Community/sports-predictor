"""
Repo-wide test fixtures -- applies to every test under Source/tests/ via
pytest's conftest.py discovery, with no per-file import needed.
"""
import socket
from unittest.mock import patch

import pytest

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class RealNetworkAccessError(Exception):
    """Not an OSError, so HTTP clients (requests/urllib3/botocore) don't
    retry it or wrap it as an ordinary connection failure."""


def _is_loopback(host) -> bool:
    return host is None or str(host) in _LOOPBACK_HOSTS


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch):
    """Unit tests never reach a real network service (ESPN, CFBD, AWS...) --
    every external call is mocked. Any attempted DNS lookup or connection to
    a non-loopback host raises, and fails the test even if the code under
    test catches the exception."""
    attempts = []
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guard(target):
        attempts.append(target)
        raise RealNetworkAccessError(f"Unit test attempted real network access to {target!r} -- mock it instead")

    def _getaddrinfo(host, *args, **kwargs):
        if _is_loopback(host):
            return real_getaddrinfo(host, *args, **kwargs)
        return _guard(host)

    def _connect(sock, address):
        if sock.family == getattr(socket, "AF_UNIX", None) or _is_loopback(address[0] if isinstance(address, tuple) else address):
            return real_connect(sock, address)
        return _guard(address)

    def _connect_ex(sock, address):
        if sock.family == getattr(socket, "AF_UNIX", None) or _is_loopback(address[0] if isinstance(address, tuple) else address):
            return real_connect_ex(sock, address)
        return _guard(address)

    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    yield
    if attempts:
        pytest.fail(f"Real network access attempted (mock these instead): {attempts}")


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


@pytest.fixture(autouse=True)
def _stub_xray_client():
    """library.aws.xray sends every segment straight to the X-Ray API --
    any test that runs library.ml.backtest.run_backtest for real would
    otherwise emit one."""
    with patch("library.aws.xray._xray"):
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
