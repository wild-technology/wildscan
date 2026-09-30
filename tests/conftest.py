"""Keep unit-test prompt defaults separate from the user's settings."""
import pytest

from module_base import settings_store


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_store, "DEFAULT_SETTINGS_PATH",
                        str(tmp_path / "rs_settings.json"))
