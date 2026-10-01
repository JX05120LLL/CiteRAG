"""Reviewed QWeather HTTP tools, executed only after the existing gateway admits a call."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.config import Settings
from app.services.errors import ServiceError
from app.tools.gateway import ToolDefinition

MAX_RESPONSE_BYTES = 65_536


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False,
                              hide_input_in_errors=True)


class CitySearch(Arguments):
    location: str = Field(min_length=1, max_length=80, title="城市名或和风 LocationID",
                          description="只提交用户明确的城市，不包含聊天正文。")
    adm: str = Field(default="", max_length=80, title="上级行政区（可选）")

    @field_validator("location", "adm")
    @classmethod
    def city_text(cls, value: str, info) -> str:
        if (any(ord(char) < 32 for char in value) or
            any(char in value for char in "/\\@<>?=&") or
            info.field_name == "location" and not value.strip()):
            raise ValueError("Only a bounded city name or LocationID is supported")
        return value.strip()


class Coordinates(Arguments):
    latitude: float = Field(ge=-90, le=90, title="纬度",
                            description="用户确认或城市搜索返回的纬度，不能猜测地点。")
    longitude: float = Field(ge=-180, le=180, title="经度",
                             description="用户确认或城市搜索返回的经度，不能猜测地点。")

    @field_validator("latitude", "longitude")
    @classmethod
    def precision(cls, value: float) -> float:
        return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class Forecast(Coordinates):
    days: int = Field(default=3, ge=1, le=7, title="预报天数（1—7）")


class ProviderData(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, allow_inf_nan=False,
                              hide_input_in_errors=True)


Text = Annotated[str, Field(min_length=1, max_length=120)]
Attribution = Annotated[str, Field(min_length=1, max_length=500)]


class Metadata(ProviderData):
    attributions: list[Attribution] = Field(min_length=1, max_length=5)


class Measure(ProviderData):
    value: float
    unit: str = Field(min_length=1, max_length=12)


class Condition(ProviderData):
    text: Text


class Direction(ProviderData):
    compass: str = Field(min_length=1, max_length=8)


class Wind(ProviderData):
    speed: Measure
    direction: Direction


class Current(ProviderData):
    metadata: Metadata
    condition: Condition
    temperature: Measure
    feelsLike: Measure | None = None
    humidity: float | None = Field(default=None, ge=0, le=1)
    wind: Wind | None = None


class Precipitation(ProviderData):
    probability: float = Field(ge=0, le=1)


class Period(ProviderData):
    condition: Condition
    precipitation: Precipitation | None = None


class Daily(ProviderData):
    forecastStartTime: str = Field(min_length=1, max_length=40)
    forecastEndTime: str = Field(min_length=1, max_length=40)
    temperatureMax: Measure
    temperatureMin: Measure
    daytime: Period
    nighttime: Period

    @field_validator("forecastStartTime", "forecastEndTime")
    @classmethod
    def utc_period(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError("Forecast periods must identify UTC")
        return value


class DailyResponse(ProviderData):
    metadata: Metadata
    days: list[Daily] = Field(min_length=1, max_length=10)


class City(ProviderData):
    id: Text
    name: Text
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    adm1: str = Field(default="", max_length=120)
    adm2: str = Field(default="", max_length=120)
    country: str = Field(default="", max_length=120)

    @field_validator("lat", "lon", mode="before")
    @classmethod
    def numeric_coordinate(cls, value):
        # Geo v2 uses numeric strings; Weather v1 uses numbers.
        return float(value) if isinstance(value, str) else value


class GeoSources(ProviderData):
    sources: list[Attribution] = Field(default_factory=list, max_length=5)
    license: list[Attribution] = Field(default_factory=list, max_length=5)


class Cities(ProviderData):
    location: list[City] = Field(min_length=1, max_length=5)
    refer: GeoSources = Field(default_factory=GeoSources)


def _rejected(status: int) -> None:
    codes = {
        400: "weather_parameters_rejected", 401: "weather_auth_failed",
        403: "weather_access_denied", 404: "weather_endpoint_unavailable",
        429: "weather_rate_limited",
    }
    code = "weather_redirect_forbidden" if 300 <= status < 400 else codes.get(
        status, "weather_unavailable")
    raise ServiceError(503, code, "天气查询失败，请检查天气服务配置与额度")


class QWeatherClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings, self.transport = settings, transport

    async def get(self, path: str, params: dict) -> dict:
        settings = self.settings
        if not settings.qweather_enabled or not settings.qweather_api_host or not (
            settings.qweather_api_key
        ):
            raise ServiceError(503, "weather_not_configured", "请先启用并配置和风天气")
        secret = settings.qweather_api_key.get_secret_value()
        try:
            async with httpx.AsyncClient(
                timeout=10, follow_redirects=False, trust_env=False, transport=self.transport,
            ) as client:
                async with client.stream(
                    "GET", f"https://{settings.qweather_api_host}{path}", params=params,
                    headers={"X-QW-Api-Key": secret, "Accept": "application/json"},
                ) as response:
                    if response.status_code != 200:
                        _rejected(response.status_code)
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError("Weather response exceeds its budget")
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError("Weather response must be an object")
            # Reject credential echoes in nested/ignored fields; never persist raw responses.
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            if secret in encoded or settings.qweather_api_host in encoded:
                raise ValueError("Weather response contains private configuration")
            code = payload.get("code", "200")
            if code == "204":
                raise ServiceError(404, "weather_location_not_found",
                                   "未找到地点，请补充城市或行政区")
            if code != "200":
                _rejected(int(code) if str(code).isdigit() else 503)
            return payload
        except httpx.TimeoutException:
            raise ServiceError(503, "weather_timeout", "天气服务超时，请稍后主动重试") from None
        except httpx.HTTPError:
            raise ServiceError(503, "weather_unavailable",
                               "天气连接失败，请检查网络与 API Host") from None
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError(503, "weather_result_invalid",
                               "天气服务返回格式不符合已接入协议") from None

    @staticmethod
    def result(kind: str, attributions: list[str], **data) -> dict:
        return {"provider": "QWeather", "source_type": "tool", "kind": kind,
                "queried_at": datetime.now(UTC).isoformat(), "attributions": attributions, **data}

    async def city_search(self, arguments: dict) -> dict:
        args = CitySearch.model_validate(arguments)
        params = {"location": args.location, "number": "5", "lang": "zh"}
        if args.adm:
            params["adm"] = args.adm
        payload = await self.get("/geo/v2/city/lookup", params)
        if payload.get("location") == []:
            raise ServiceError(404, "weather_location_not_found", "未找到地点，请补充城市或行政区")
        parsed = Cities.model_validate(payload)
        candidates = [{"id": city.id, "name": city.name, "latitude": city.lat,
                       "longitude": city.lon, "adm1": city.adm1, "adm2": city.adm2,
                       "country": city.country} for city in parsed.location]
        return self.result("city_search", parsed.refer.sources, candidates=candidates,
                           licenses=parsed.refer.license, requires_selection=len(candidates) > 1)

    async def current(self, arguments: dict) -> dict:
        args = Coordinates.model_validate(arguments)
        location = args.model_dump()
        parsed = Current.model_validate(await self.get(
            f"/weather/v1/current/{args.latitude:.2f}/{args.longitude:.2f}",
            {"lang": "zh", "localTime": "false"},
        ))
        current = {"condition": parsed.condition.text,
                   "temperature": parsed.temperature.model_dump()}
        if parsed.feelsLike is not None:
            current["feels_like"] = parsed.feelsLike.model_dump()
        if parsed.humidity is not None:
            current["humidity_percent"] = round(parsed.humidity * 100, 2)
        if parsed.wind is not None:
            current["wind_speed"] = parsed.wind.speed.model_dump()
            current["wind_direction"] = parsed.wind.direction.compass
        return self.result("current", parsed.metadata.attributions,
                           location=location, current=current)

    async def forecast(self, arguments: dict) -> dict:
        args = Forecast.model_validate(arguments)
        parsed = DailyResponse.model_validate(await self.get(
            f"/weather/v1/daily/{args.latitude:.2f}/{args.longitude:.2f}",
            {"days": str(args.days), "lang": "zh", "localTime": "false"},
        ))
        if len(parsed.days) > args.days:
            raise ValueError("Weather forecast exceeds the requested days")

        def period(item: Period) -> dict:
            result = {"condition": item.condition.text}
            if item.precipitation is not None:
                result["precipitation_probability_percent"] = round(
                    item.precipitation.probability * 100, 2)
            return result

        days = [{"forecast_start_time": day.forecastStartTime,
                 "forecast_end_time": day.forecastEndTime,
                 "temperature_max": day.temperatureMax.model_dump(),
                 "temperature_min": day.temperatureMin.model_dump(),
                 "daytime": period(day.daytime), "nighttime": period(day.nighttime)}
                for day in parsed.days]
        return self.result("forecast", parsed.metadata.attributions,
                           location={"latitude": args.latitude, "longitude": args.longitude},
                           days=days)


def qweather_tools(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None,
                   ) -> dict[str, ToolDefinition]:
    """No network, discovery, credential files or database access during registration."""
    if not settings.qweather_enabled or not settings.qweather_api_host or not (
        settings.qweather_api_key
    ):
        return {}
    client = QWeatherClient(settings, transport=transport)
    # A changed destination invalidates paused approval without exposing the dedicated API Host.
    host_version = hashlib.sha256(settings.qweather_api_host.encode()).hexdigest()[:16]
    registry = {}
    for name, title, model, method, description in (
        ("city_search", "和风城市搜索", CitySearch, client.city_search,
         "返回真实城市候选和坐标；重名或指代不明须询问用户，不自动选择首项。"),
        ("current", "和风实时天气", Coordinates, client.current,
         "使用用户明确或城市搜索确认的坐标，查询实时天气；不得猜测用户所在地点。"),
        ("forecast", "和风每日预报", Forecast, client.forecast,
         "使用已确认的坐标查询未来 1—7 天预报；预报时间为 UTC，需按用户地点解释日期。"),
    ):
        def validate(arguments, argument_model=model):
            try:
                return argument_model.model_validate(arguments).model_dump()
            except ValidationError:
                return None

        async def run(_session, _context, arguments, operation=method):
            try:
                return await operation(arguments)
            except (ValueError, TypeError):
                raise ServiceError(503, "weather_result_invalid",
                                   "天气服务返回格式不符合已接入协议") from None

        spec = ToolDefinition(
            f"weather.{name}", title, "any", True,
            f"{description} 将所示城市名或坐标发往和风天气，每次最多 1 次供应商请求，"
            "可能按量计费；不读取聊天正文或知识库。", validate, run,
            version=f"qweather-geo2-weather1-{host_version}",
            input_schema=model.model_json_schema(), timeout_seconds=12,
            destination="QWeather（后端配置的专属 API Host）",
        )
        registry[spec.id] = spec
    return registry
