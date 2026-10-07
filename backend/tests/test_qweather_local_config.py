"""Explicit, ignored weather config is loaded on each API start without supplier calls."""

import pytest

import app.config as config
from app.main import create_app

HOST = "synthetic.weather.qweatherapi.com"
KEY = "synthetic-weather-key-do-not-use"
OVERRIDE_HOST = "override.weather.qweatherapi.com"
OVERRIDE_KEY = "synthetic-override-key-do-not-use"


def test_local_weather_config_path_is_under_backend():
    assert config.QWEATHER_LOCAL_CONFIG == config.PROJECT_ROOT / "backend" / ".env.weather"


@pytest.fixture
def local_weather_config(monkeypatch, tmp_path):
    path = tmp_path / ".env.weather"
    monkeypatch.setattr(config, "QWEATHER_LOCAL_CONFIG", path)
    for name in ("CITERAG_QWEATHER_ENABLED", "CITERAG_QWEATHER_API_HOST",
                 "CITERAG_QWEATHER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return path


def test_completed_local_file_registers_weather_after_next_settings_load(local_weather_config):
    local_weather_config.write_text(
        "CITERAG_QWEATHER_API_HOST=\nCITERAG_QWEATHER_API_KEY=\n", encoding="utf-8",
    )
    assert not config.Settings.from_env().qweather_enabled

    local_weather_config.write_text(
        f"CITERAG_QWEATHER_API_HOST={HOST}\nCITERAG_QWEATHER_API_KEY={KEY}\n",
        encoding="utf-8",
    )
    settings = config.Settings.from_env()
    assert settings.qweather_enabled
    assert settings.qweather_api_host == HOST
    tools = create_app(settings).state.tool_registry
    assert {name for name in tools if name.startswith("weather.")} == {
        "weather.city_search", "weather.current", "weather.forecast",
    }


def test_process_environment_overrides_local_file_as_a_pair(local_weather_config, monkeypatch):
    local_weather_config.write_text(
        f"CITERAG_QWEATHER_API_HOST={HOST}\nCITERAG_QWEATHER_API_KEY={KEY}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CITERAG_QWEATHER_API_HOST", OVERRIDE_HOST)
    monkeypatch.setenv("CITERAG_QWEATHER_API_KEY", OVERRIDE_KEY)
    monkeypatch.setenv("CITERAG_QWEATHER_ENABLED", "true")
    settings = config.Settings.from_env()
    assert settings.qweather_api_host == OVERRIDE_HOST
    assert settings.qweather_api_key.get_secret_value() == OVERRIDE_KEY
    assert settings.qweather_enabled

    monkeypatch.delenv("CITERAG_QWEATHER_API_KEY")
    incomplete = config.Settings.from_env()
    assert incomplete.qweather_api_host == OVERRIDE_HOST
    assert incomplete.qweather_api_key is None
    assert not any(name.startswith("weather.") for name in
                   create_app(incomplete).state.tool_registry)


def test_explicit_disable_overrides_completed_local_file(local_weather_config, monkeypatch):
    local_weather_config.write_text(
        f"CITERAG_QWEATHER_API_HOST={HOST}\nCITERAG_QWEATHER_API_KEY={KEY}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CITERAG_QWEATHER_ENABLED", "false")
    settings = config.Settings.from_env()
    assert not settings.qweather_enabled
    assert not any(name.startswith("weather.") for name in
                   create_app(settings).state.tool_registry)


def test_partial_local_file_reports_names_without_echoing_values(local_weather_config):
    local_weather_config.write_text(
        f"CITERAG_QWEATHER_API_HOST={HOST}\nCITERAG_QWEATHER_API_KEY=\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="both CITERAG_QWEATHER_API_HOST and") as error:
        config.Settings.from_env()
    assert HOST not in str(error.value)


def test_unknown_local_setting_is_rejected_without_loading_it(local_weather_config):
    local_weather_config.write_text(
        "CITERAG_QWEATHER_API_HOST=\nCITERAG_QWEATHER_API_KEY=\nOTHER_KEY=private\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported or duplicate") as error:
        config.Settings.from_env()
    assert "private" not in str(error.value)
