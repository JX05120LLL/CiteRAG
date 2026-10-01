"""Public synthetic weather responses; never contact QWeather or load real credentials."""

import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from app.services.errors import ServiceError

KEY = "synthetic-weather-key-do-not-use"
HOST = "synthetic.xy.qweatherapi.com"
ATTRIBUTION = "https://developer.qweather.com/attribution.html"


def configured():
    return Settings(qweather_enabled=True, qweather_api_host=HOST, qweather_api_key=SecretStr(KEY))


def current_response():
    return {
        "metadata": {"attributions": [ATTRIBUTION]},
        "condition": {"text": "少云", "code": "102"},
        "temperature": {"value": 25, "unit": "°C"},
        "feelsLike": {"value": 27, "unit": "°C"}, "humidity": 0.69,
        "wind": {"speed": {"value": 4.74, "unit": "m/s"},
                 "direction": {"compass": "sw"}, "scale": 3},
    }


@pytest.mark.parametrize("host", [
    "https://synthetic.xy.qweatherapi.com", "127.0.0.1", "localhost",
    "synthetic.xy.qweatherapi.com.evil.invalid", "user@synthetic.xy.qweatherapi.com",
    "synthetic.xy.qweatherapi.com:443", "synthetic.xy.qweatherapi.com/path",
    "synthetic.xy.qweatherapi.com?key=secret", "synthetic..qweatherapi.com",
])
def test_host_cannot_be_an_arbitrary_endpoint(host):
    with pytest.raises(ValidationError):
        Settings(qweather_api_host=host)


def test_credentials_are_hidden_and_registration_does_not_load_saved_records(monkeypatch):
    from app.tools.qweather import qweather_tools

    def forbidden(*_args, **_kwargs):
        pytest.fail("Weather configuration must not read saved model credentials")

    monkeypatch.setattr("app.credentials.load_credential", forbidden)
    settings = configured()
    assert KEY not in repr(settings) and HOST not in repr(settings)
    registry = qweather_tools(settings)
    assert KEY not in repr(registry) and HOST not in repr(registry)
    assert qweather_tools(Settings(qweather_enabled=True)) == {}


@pytest.mark.parametrize("tool,arguments", [
    ("weather.current", {"latitude": True, "longitude": 120}),
    ("weather.current", {"latitude": float("nan"), "longitude": 120}),
    ("weather.current", {"latitude": 91, "longitude": 120}),
    ("weather.current", {"latitude": 30, "longitude": 181}),
    ("weather.current", {"latitude": 30, "longitude": 120, "url": "https://evil.invalid"}),
    ("weather.current", {"latitude": "30", "longitude": 120}),
    ("weather.forecast", {"latitude": 30, "longitude": 120, "days": 8}),
    ("weather.forecast", {"latitude": 30, "longitude": 120, "days": True}),
    ("weather.city_search", {"location": "https://evil.invalid"}),
    ("weather.city_search", {"location": " "}),
    ("weather.city_search", {"location": "杭州", "api_key": "do-not-accept"}),
])
def test_invalid_arguments_do_not_reach_the_provider(tool, arguments):
    from app.tools.qweather import qweather_tools

    assert qweather_tools(configured())[tool].validate(arguments) is None


async def test_current_weather_uses_header_auth_and_latitude_first_without_raw_body(caplog):
    from app.tools.qweather import qweather_tools

    received = []

    async def respond(request):
        received.append(request)
        assert request.headers["X-QW-Api-Key"] == KEY
        assert KEY not in str(request.url)
        assert request.url.host == HOST
        assert request.url.path == "/weather/v1/current/30.25/120.13"
        assert dict(request.url.params) == {"lang": "zh", "localTime": "false"}
        return httpx.Response(200, json={**current_response(), "ignored": "private diagnostic"})

    spec = qweather_tools(configured(), transport=httpx.MockTransport(respond))["weather.current"]
    arguments = spec.validate({"latitude": 30.245, "longitude": 120.125})
    assert arguments == {"latitude": 30.25, "longitude": 120.13}
    result = await spec.run(None, None, arguments)
    assert len(received) == 1
    assert result["provider"] == "QWeather" and result["source_type"] == "tool"
    assert result["attributions"] == [ATTRIBUTION]
    assert result["current"]["humidity_percent"] == 69
    assert result["current"]["temperature"] == {"value": 25, "unit": "°C"}
    assert result["queried_at"] and result["location"] == arguments
    assert KEY not in json.dumps(result) and "private diagnostic" not in json.dumps(result)
    assert KEY not in caplog.text


async def test_city_search_preserves_ambiguous_candidates_and_empty_results_are_not_success():
    from app.tools.qweather import qweather_tools

    async def respond(request):
        assert request.url.path == "/geo/v2/city/lookup"
        assert dict(request.url.params) == {
            "location": "朝阳", "adm": "北京", "number": "5", "lang": "zh",
        }
        return httpx.Response(200, json={"code": "200", "location": [
            {"id": "synthetic-1", "name": "朝阳", "lat": "39.92", "lon": "116.44",
             "adm1": "北京市", "adm2": "北京", "country": "中国"},
            {"id": "synthetic-2", "name": "朝阳", "lat": "41.57", "lon": "120.45",
             "adm1": "辽宁省", "adm2": "朝阳", "country": "中国"},
        ], "refer": {"sources": [ATTRIBUTION], "license": ["QWeather Developers License"]}})

    spec = qweather_tools(configured(), transport=httpx.MockTransport(respond))[
        "weather.city_search"]
    result = await spec.run(None, None, {"location": "朝阳", "adm": "北京"})
    assert len(result["candidates"]) == 2 and result["requires_selection"] is True
    assert result["candidates"][0]["latitude"] == 39.92
    empty = qweather_tools(configured(), transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"code": "200", "location": []}),
    ))["weather.city_search"]
    with pytest.raises(ServiceError) as failure:
        await empty.run(None, None, {"location": "不存在"})
    assert failure.value.code == "weather_location_not_found"


async def test_forecast_keeps_period_units_and_supplier_attribution():
    from app.tools.qweather import qweather_tools

    async def respond(request):
        assert request.url.path == "/weather/v1/daily/30.25/120.13"
        assert request.url.params["days"] == "3"
        return httpx.Response(200, json={"metadata": {"attributions": [ATTRIBUTION]}, "days": [{
            "forecastStartTime": "2026-10-01T22:00Z", "forecastEndTime": "2026-10-02T22:00Z",
            "temperatureMax": {"value": 28, "unit": "°C"},
            "temperatureMin": {"value": 20, "unit": "°C"},
            "daytime": {"condition": {"text": "小雨"}, "precipitation": {"probability": 0.64}},
            "nighttime": {"condition": {"text": "阴"}, "precipitation": {"probability": 0.1}},
        }]})

    spec = qweather_tools(configured(), transport=httpx.MockTransport(respond))["weather.forecast"]
    result = await spec.run(None, None, {"latitude": 30.25, "longitude": 120.13})
    assert result["days"][0]["daytime"]["precipitation_probability_percent"] == 64
    assert result["days"][0]["forecast_start_time"] == "2026-10-01T22:00Z"
    assert result["attributions"] == [ATTRIBUTION]


@pytest.mark.parametrize("status,code", [
    (400, "weather_parameters_rejected"), (401, "weather_auth_failed"),
    (403, "weather_access_denied"), (404, "weather_endpoint_unavailable"),
    (429, "weather_rate_limited"), (503, "weather_unavailable"),
    (302, "weather_redirect_forbidden"),
])
async def test_provider_errors_are_safe_and_never_automatically_retried(status, code):
    from app.tools.qweather import qweather_tools

    calls = []

    async def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"detail": KEY}},
                              headers={"Location": "https://evil.invalid"})

    spec = qweather_tools(configured(), transport=httpx.MockTransport(respond))["weather.current"]
    with pytest.raises(ServiceError) as failure:
        await spec.run(None, None, {"latitude": 30, "longitude": 120})
    assert failure.value.code == code and KEY not in str(failure.value)
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [
    {}, {**current_response(), "condition": {"text": KEY}},
    {**current_response(), "humidity": 69},
    {**current_response(), "temperature": {"value": float("nan"), "unit": "°C"}},
    {**current_response(), "metadata": {"attributions": []}},
])
async def test_invalid_or_credential_echoing_response_fails_closed(payload):
    from app.tools.qweather import qweather_tools

    transport = httpx.MockTransport(lambda _request: httpx.Response(
        200, content=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
    ))
    spec = qweather_tools(configured(), transport=transport)["weather.current"]
    with pytest.raises(ServiceError) as failure:
        await spec.run(None, None, {"latitude": 30, "longitude": 120})
    assert failure.value.code == "weather_result_invalid"


async def test_response_limit_and_timeout_are_safe():
    from app.tools.qweather import qweather_tools

    spec = qweather_tools(configured(), transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, content=b"x" * 65537),
    ))["weather.current"]
    with pytest.raises(ServiceError) as failure:
        await spec.run(None, None, {"latitude": 30, "longitude": 120})
    assert failure.value.code == "weather_result_invalid"

    async def timed_out(request):
        raise httpx.ReadTimeout(KEY, request=request)

    spec = qweather_tools(configured(), transport=httpx.MockTransport(timed_out))["weather.current"]
    with pytest.raises(ServiceError) as failure:
        await spec.run(None, None, {"latitude": 30, "longitude": 120})
    assert failure.value.code == "weather_timeout" and KEY not in str(failure.value)


async def test_cancellation_reaches_actual_http_work():
    from app.tools.qweather import qweather_tools

    started, cancelled = asyncio.Event(), asyncio.Event()

    async def waiting(_request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    spec = qweather_tools(configured(), transport=httpx.MockTransport(waiting))["weather.current"]
    task = asyncio.create_task(spec.run(None, None, {"latitude": 30, "longitude": 120}))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


def test_weather_is_opt_in_and_only_registered_with_complete_configuration(monkeypatch):
    for name in ("CITERAG_QWEATHER_ENABLED", "CITERAG_QWEATHER_API_HOST",
                 "CITERAG_QWEATHER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert not any(key.startswith("weather.") for key in create_app(Settings()).state.tool_registry)
    monkeypatch.setenv("CITERAG_QWEATHER_API_HOST", "synthetic.xy.qweatherapi.com")
    monkeypatch.setenv("CITERAG_QWEATHER_API_KEY", "synthetic-weather-key-do-not-use")
    assert not any(key.startswith("weather.") for key in
                   create_app(Settings.from_env()).state.tool_registry)
    monkeypatch.setenv("CITERAG_QWEATHER_ENABLED", "true")
    registry = create_app(Settings.from_env()).state.tool_registry
    assert {key for key in registry if key.startswith("weather.")} == {
        "weather.city_search", "weather.current", "weather.forecast",
    }
    assert all(registry[key].scope == "any" and registry[key].approval_required
               for key in registry if key.startswith("weather."))
    monkeypatch.delenv("CITERAG_QWEATHER_API_KEY")
    assert not any(key.startswith("weather.") for key in
                   create_app(Settings.from_env()).state.tool_registry)
