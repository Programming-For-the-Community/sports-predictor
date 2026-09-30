"""
Unit tests for library.ml.sklearn_acceleration -- applies sklearnex only
when it's installed.
"""
import sys
import types
from unittest.mock import MagicMock

from library.ml import sklearn_acceleration


def test_applies_the_patch_when_sklearnex_is_installed(monkeypatch):
    fake = types.ModuleType("sklearnex")
    fake.patch_sklearn = MagicMock()
    monkeypatch.setitem(sys.modules, "sklearnex", fake)

    assert sklearn_acceleration.patch_sklearn_if_available() is True
    fake.patch_sklearn.assert_called_once_with()


def test_does_nothing_when_sklearnex_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "sklearnex", None)

    assert sklearn_acceleration.patch_sklearn_if_available() is False
