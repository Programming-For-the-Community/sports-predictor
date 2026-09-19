"""
Unit tests for get_account_id -- AWS_ACCOUNT_ID env var is the only source;
a missing/empty value raises rather than falling back to an STS call.
"""
import pytest

import library.aws.account as account_module
from library.aws.account import get_account_id


def _reset_cache():
    account_module._account_id = None


class TestGetAccountId:
    def test_uses_the_env_var(self, monkeypatch):
        _reset_cache()
        monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
        assert get_account_id() == "123456789012"

    def test_caches_across_calls_so_a_second_call_never_rereads_the_env_var(self, monkeypatch):
        _reset_cache()
        monkeypatch.setenv("AWS_ACCOUNT_ID", "111122223333")
        first = get_account_id()
        monkeypatch.delenv("AWS_ACCOUNT_ID", raising=False)
        second = get_account_id()
        assert first == second == "111122223333"

    def test_raises_when_the_env_var_is_unset(self, monkeypatch):
        _reset_cache()
        monkeypatch.delenv("AWS_ACCOUNT_ID", raising=False)
        with pytest.raises(RuntimeError, match="AWS_ACCOUNT_ID"):
            get_account_id()

    def test_raises_when_the_env_var_is_an_empty_string(self, monkeypatch):
        _reset_cache()
        monkeypatch.setenv("AWS_ACCOUNT_ID", "")
        with pytest.raises(RuntimeError, match="AWS_ACCOUNT_ID"):
            get_account_id()
