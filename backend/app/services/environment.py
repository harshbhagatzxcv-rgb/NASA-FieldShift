from __future__ import annotations

import asyncio
import email.utils
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.calculations import calculate_gdd, precipitation_anomaly, rainfall_status
from app.models.schemas import DataStatus, EnvironmentalContext, FarmRequest, Provenance
from app.services.soilgrids_wcs import SoilGridsWCSAdapter

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_ENVIRONMENT_CACHE: TTLCache | None = None


@dataclass
class AdapterResult:
    payload: dict[str, Any]
    status: DataStatus


class TTLCache:
    def __init__(self, ttl_seconds: int = 86400):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> dict | None:
        item = self._items.get(key)
        if item and time.time() - item[0] < self.ttl_seconds:
            return item[1]
        return None

    def set(self, key: str, value: dict) -> None:
        self._items[key] = (time.time(), value)


class RetryingClient:
    def __init__(self, timeout: float = 8.0, attempts: int = 3):
        self.timeout = timeout
        self.attempts = attempts
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> RetryingClient:
        self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self

    async def __aexit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, url: str, params: list[tuple[str, str]] | dict[str, str]) -> httpx.Response:
        if self._client is not None:
            return await self._request(self._client, url, params)
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            return await self._request(client, url, params)

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: list[tuple[str, str]] | dict[str, str],
    ) -> httpx.Response:
        last_error: httpx.HTTPError | None = None
        for attempt in range(self.attempts):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.attempts - 1:
                    break
                retry_after = None
                if isinstance(exc, httpx.HTTPStatusError):
                    if exc.response.status_code not in (429, 500, 502, 503, 504):
                        break
                    retry_after = exc.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        try:
                            retry_date = email.utils.parsedate_to_datetime(retry_after)
                            if retry_date.tzinfo is None:
                                retry_date = retry_date.replace(tzinfo=timezone.utc)
                            delay = (retry_date - datetime.now(timezone.utc)).total_seconds()
                        except (TypeError, ValueError, OverflowError):
                            delay = min(.5 * (2**attempt), 4.0)
                    if delay < 0:
                        delay = 0
                    if delay > 8:
                        break
                else:
                    delay = min(.5 * (2**attempt), 4.0)
                await asyncio.sleep(delay)
        raise RuntimeError("Environmental data service unavailable") from last_error

    async def get_json(self, url: str, params: list[tuple[str, str]] | dict[str, str]) -> dict:
        payload = (await self._get(url, params)).json()
        if not isinstance(payload, dict):
            raise ValueError("External response was not a JSON object")
        return payload

    async def get_bytes(self, url: str, params: list[tuple[str, str]] | dict[str, str]) -> bytes:
        return (await self._get(url, params)).content


class NasaPowerAdapter:
    url = "https://power.larc.nasa.gov/api/temporal/daily/point"

    def __init__(self, client: RetryingClient, cache: TTLCache):
        self.client, self.cache = client, cache

    async def fetch(self, latitude: float, longitude: float) -> AdapterResult:
        key = f"nasa:{latitude:.6f}:{longitude:.6f}"
        if cached := self.cache.get(key):
            return AdapterResult(cached, DataStatus.CACHED)
        end = date.today() - timedelta(days=30)
        start = end - timedelta(days=365 * 4)
        payload = await self.client.get_json(self.url, {
            "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR,RH2M,ALLSKY_SFC_SW_DWN",
            "community": "AG", "longitude": str(longitude), "latitude": str(latitude),
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON",
        })
        parameter = payload.get("properties", {}).get("parameter", {})
        parameter_names = ("T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "ALLSKY_SFC_SW_DWN")
        if not all(name in parameter for name in parameter_names):
            raise ValueError("NASA POWER response omitted required variables")
        geometry_object = payload.get("geometry")
        geometry = geometry_object.get("coordinates", []) if isinstance(geometry_object, dict) else []
        if (
            not isinstance(geometry, list) or len(geometry) < 2
            or abs(float(geometry[0]) - longitude) > .51
            or abs(float(geometry[1]) - latitude) > .51
        ):
            raise ValueError("NASA POWER response coordinates did not match the request")

        valid_ranges = {
            "T2M_MAX": (-90, 65),
            "T2M_MIN": (-100, 60),
            "PRECTOTCORR": (0, 2000),
            "RH2M": (0, 100),
            "ALLSKY_SFC_SW_DWN": (0, 100),
        }
        series: dict[str, dict[date, float]] = {}
        for variable in parameter_names:
            observations = parameter[variable]
            if not isinstance(observations, dict):
                raise ValueError(f"NASA POWER response contained an invalid {variable} series")
            series[variable] = {}
            for day, raw_value in observations.items():
                if not isinstance(day, str) or len(day) != 8 or not day.isdigit():
                    raise ValueError(f"NASA POWER response contained an invalid date in {variable}")
                try:
                    observation_date = date.fromisoformat(f"{day[:4]}-{day[4:6]}-{day[6:]}")
                    if isinstance(raw_value, bool):
                        raise ValueError
                    value = float(raw_value)
                except (ValueError, TypeError) as exc:
                    raise ValueError(f"NASA POWER response contained an invalid value in {variable}") from exc
                if value <= -900:
                    continue
                low, high = valid_ranges[variable]
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"NASA POWER response contained an out-of-range {variable} observation")
                series[variable][observation_date] = value

        def shifted_year(day: date, years: int) -> date:
            try:
                return day.replace(year=day.year + years)
            except ValueError:
                return day.replace(year=day.year + years, day=28)

        def window_for(window_end: date) -> list[date]:
            return [window_end - timedelta(days=offset) for offset in range(179, -1, -1)]

        recent_days = window_for(end)

        def coverage(variable: str, days: list[date]) -> list[float]:
            observations = [series[variable][day] for day in days if day in series[variable]]
            if len(observations) < math.ceil(len(days) * .8):
                raise ValueError(f"NASA POWER response has insufficient {variable} observations")
            return observations

        rain_values = coverage("PRECTOTCORR", recent_days)
        coverage("T2M_MAX", recent_days)
        coverage("T2M_MIN", recent_days)
        coverage("RH2M", recent_days)
        coverage("ALLSKY_SFC_SW_DWN", recent_days)
        paired_temperatures = [
            (series["T2M_MAX"][day], series["T2M_MIN"][day])
            for day in recent_days
            if day in series["T2M_MAX"] and day in series["T2M_MIN"]
        ]
        if len(paired_temperatures) < math.ceil(len(recent_days) * .8):
            raise ValueError("NASA POWER response has insufficient paired temperature observations")
        if any(maximum < minimum for maximum, minimum in paired_temperatures):
            raise ValueError("NASA POWER response contains maximum temperatures below minimum temperatures")

        historical_windows = []
        for years_ago in range(1, 4):
            previous_end = shifted_year(end, -years_ago)
            previous_days = window_for(previous_end)
            try:
                previous_rain = coverage("PRECTOTCORR", previous_days)
            except ValueError:
                continue
            historical_windows.append(sum(previous_rain))
        if len(historical_windows) < 2:
            raise ValueError("NASA POWER response lacks two valid historical seasonal rainfall windows")
        result = {
            "annual_gdd": calculate_gdd(
                [maximum for maximum, _ in paired_temperatures],
                [minimum for _, minimum in paired_temperatures],
                10,
                35,
            ),
            "seasonal_rainfall_mm": sum(rain_values),
            "historical_rainfall_mm": sum(historical_windows) / len(historical_windows),
            "heat_stress_days": sum(maximum > 35 for maximum, _ in paired_temperatures),
        }
        self.cache.set(key, result)
        return AdapterResult(result, DataStatus.LIVE)


class SoilGridsAdapter:
    url = "https://rest.isric.org/soilgrids/v2.0/properties/query"

    def __init__(self, client: RetryingClient, cache: TTLCache):
        self.client, self.cache = client, cache

    async def fetch(self, latitude: float, longitude: float) -> AdapterResult:
        key = f"soil:{latitude:.6f}:{longitude:.6f}"
        if cached := self.cache.get(key):
            return AdapterResult(cached, DataStatus.CACHED)
        params = [("lon", str(longitude)), ("lat", str(latitude)), ("depth", "0-30cm"), ("value", "mean")]
        params += [("property", item) for item in ("clay", "sand", "silt", "phh2o", "soc")]
        payload = await self.client.get_json(self.url, params)
        layers = payload.get("properties", {}).get("layers", [])
        means = {}
        layer_items = layers if isinstance(layers, list) else []
        for layer in layer_items:
            if not isinstance(layer, dict) or layer.get("name") not in {"clay", "sand", "silt", "phh2o", "soc"}:
                continue
            depths = layer.get("depths", [])
            depth = next((d for d in depths if isinstance(d, dict) and d.get("label") == "0-30cm"), None)
            if depth:
                means[layer["name"]] = depth.get("values", {}).get("mean")
        if not {"clay", "sand", "silt", "phh2o", "soc"}.issubset(means):
            raise ValueError("SoilGrids response omitted required layers")
        if any(isinstance(means[name], bool) for name in ("clay", "sand", "silt", "phh2o", "soc")):
            raise ValueError("SoilGrids response contained a non-numeric measurement")
        try:
            values = {name: float(means[name]) for name in ("clay", "sand", "silt", "phh2o", "soc")}
        except (TypeError, ValueError) as exc:
            raise ValueError("SoilGrids response contained a non-numeric measurement") from exc
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("SoilGrids response contained a non-finite measurement")
        clay, sand, silt = (values[name] for name in ("clay", "sand", "silt"))
        if any(value < 0 or value > 1000 for value in (clay, sand, silt)) or not 900 <= clay + sand + silt <= 1100:
            raise ValueError("SoilGrids response contained invalid texture fractions")
        ph = values["phh2o"] / 10
        if not 3 <= ph <= 10:
            raise ValueError("SoilGrids response contained an invalid soil pH")
        if values["soc"] < 0 or values["soc"] > 10000:
            raise ValueError("SoilGrids response contained invalid organic carbon")
        organic_matter = values["soc"] / 100 * 1.724
        if organic_matter > 100:
            raise ValueError("SoilGrids response produced invalid organic matter")
        clay_pct, sand_pct = clay / 10, sand / 10
        texture = "CLAY" if clay_pct > 45 else "SANDY" if sand_pct > 70 else "CLAY_LOAM" if clay_pct > 32 else "SANDY_LOAM" if sand_pct > 52 else "LOAM"
        organic_carbon_pct = values["soc"] / 100
        result = {
            "soil_texture": texture,
            "soil_ph": ph,
            "soil_organic_carbon_pct": organic_carbon_pct,
            "soil_organic_matter_pct": organic_matter,
        }
        self.cache.set(key, result)
        return AdapterResult(result, DataStatus.LIVE)


def demo_profiles() -> list[dict]:
    return json.loads((DATA_DIR / "demo_profiles.json").read_text())


def fallback_for(request: FarmRequest) -> tuple[EnvironmentalContext, list[Provenance]]:
    profiles = demo_profiles()
    selected = next((p for p in profiles if p["id"] == request.demo_profile_id), None)
    if selected is None:
        raise ValueError("A valid demo profile must be selected to use demonstration estimates")
    values = selected["context"]
    rainfall = values["seasonal_rainfall_mm"]
    historical = values["historical_rainfall_mm"]
    anomaly = precipitation_anomaly(rainfall, historical)
    soil_status = DataStatus.ESTIMATED
    soil_texture = request.soil_texture or selected["soil_texture"]
    context = EnvironmentalContext(
        annual_gdd=values["annual_gdd"], seasonal_rainfall_mm=rainfall, historical_rainfall_mm=historical,
        precipitation_anomaly_pct=anomaly, rainfall_status=rainfall_status(anomaly),
        heat_stress_days=values["heat_stress_days"], soil_moisture_state="UNAVAILABLE",
        soil_texture=soil_texture.upper(),
        soil_ph=values["soil_ph"],
        soil_organic_carbon_pct=None,
        soil_organic_matter_pct=values["soil_organic_matter_pct"],
        data_quality={"climate": DataStatus.FALLBACK, "soil": soil_status, "soil_moisture": DataStatus.UNAVAILABLE},
    )
    provenance = [
        Provenance(variable="temperature_and_rainfall", source="Demonstration regional profile", status=DataStatus.FALLBACK),
        Provenance(
            variable="soil_properties",
            source="Demonstration soil estimate" if selected else "Soil observations unavailable for this location.",
            status=soil_status,
        ),
        Provenance(
            variable="root_zone_moisture",
            source="SMAP L4 SPL4SMGP requires NASA Earthdata Login; adapter not configured.",
            status=DataStatus.UNAVAILABLE,
        ),
    ]
    if request.soil_texture:
        provenance.append(Provenance(variable="soil_texture", source="Farmer-provided soil texture", status=DataStatus.USER_PROVIDED))
    return context, provenance


async def build_context(request: FarmRequest) -> tuple[EnvironmentalContext, list[Provenance]]:
    if request.demo_profile_id:
        return fallback_for(request)
    global _ENVIRONMENT_CACHE
    if _ENVIRONMENT_CACHE is None:
        _ENVIRONMENT_CACHE = TTLCache()
    cache = _ENVIRONMENT_CACHE
    async with RetryingClient() as client:
        climate_adapter = NasaPowerAdapter(client, cache)
        soil_adapter = SoilGridsAdapter(client, cache)
        climate_result, soil_rest_result = await asyncio.gather(
            climate_adapter.fetch(request.latitude, request.longitude),
            soil_adapter.fetch(request.latitude, request.longitude),
            return_exceptions=True,
        )
        if not isinstance(climate_result, AdapterResult):
            logger.warning("climate_adapter_unavailable service=NASA_POWER error=%s", type(climate_result).__name__)
            raise RuntimeError("NASA POWER data is unavailable for this location; no demo climate was substituted") from climate_result

        soil_result = soil_rest_result
        if not isinstance(soil_rest_result, AdapterResult):
            logger.info("soilgrids_rest_unavailable fallback=WCS error=%s", soil_rest_result)
            try:
                wcs_payload, wcs_status = await SoilGridsWCSAdapter(client, cache).fetch(
                    request.latitude, request.longitude,
                )
                soil_result = AdapterResult(wcs_payload, wcs_status)
            except (RuntimeError, ValueError) as exc:
                logger.warning("soilgrids_wcs_unavailable error=%s", exc)
                soil_result = None

    climate = climate_result.payload
    soil = soil_result.payload if isinstance(soil_result, AdapterResult) else {}
    climate_status = climate_result.status
    soil_status = soil_result.status if isinstance(soil_result, AdapterResult) else DataStatus.UNAVAILABLE
    anomaly = precipitation_anomaly(climate["seasonal_rainfall_mm"], climate["historical_rainfall_mm"])
    soil_texture = request.soil_texture or soil.get("soil_texture")
    context = EnvironmentalContext(
        annual_gdd=climate["annual_gdd"], seasonal_rainfall_mm=climate["seasonal_rainfall_mm"],
        historical_rainfall_mm=climate["historical_rainfall_mm"], precipitation_anomaly_pct=anomaly,
        rainfall_status=rainfall_status(anomaly), heat_stress_days=climate["heat_stress_days"], soil_moisture_state="UNAVAILABLE",
        soil_texture=soil_texture.upper() if soil_texture else None,
        soil_ph=soil.get("soil_ph"),
        soil_organic_carbon_pct=soil.get("soil_organic_carbon_pct"),
        soil_organic_matter_pct=soil.get("soil_organic_matter_pct"),
        data_quality={"climate": climate_status, "soil": soil_status, "soil_moisture": DataStatus.UNAVAILABLE},
    )
    provenance = [
        Provenance(variable="temperature_and_rainfall", source="NASA POWER daily observations", status=climate_status),
        Provenance(
            variable="soil_properties",
            source=(
                "ISRIC SoilGrids REST modeled estimates (0-30 cm)"
                if isinstance(soil_result, AdapterResult) and soil_rest_result is soil_result
                else "ISRIC SoilGrids WCS modeled estimates (250 m; depth-weighted 0-30 cm)"
                if isinstance(soil_result, AdapterResult)
                else "Soil observations unavailable for this location."
            ),
            status=soil_status,
        ),
        Provenance(
            variable="root_zone_moisture",
            source="SMAP L4 SPL4SMGP (9 km, 3-hourly) requires NASA Earthdata Login; adapter not configured.",
            status=DataStatus.UNAVAILABLE,
        ),
    ]
    if request.soil_texture:
        provenance.append(Provenance(variable="soil_texture", source="Farmer-provided soil texture", status=DataStatus.USER_PROVIDED))
    return context, provenance
