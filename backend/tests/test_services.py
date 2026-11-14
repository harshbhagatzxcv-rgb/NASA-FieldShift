import httpx
import pytest

from app.models.schemas import DataStatus, FarmRequest
from app.services.environment import AdapterResult, NasaPowerAdapter, RetryingClient, SoilGridsAdapter, TTLCache, build_context, fallback_for
from app.services.soilgrids_wcs import SoilGridsWCSAdapter


@pytest.mark.asyncio
async def test_external_timeout_handling(monkeypatch):
    async def fail(*args, **kwargs):
        raise httpx.TimeoutException("timeout")
    monkeypatch.setattr(httpx.AsyncClient, "get", fail)
    with pytest.raises(RuntimeError, match="unavailable"):
        await RetryingClient(timeout=.01, attempts=1).get_json("https://example.invalid", {})


def test_missing_data_uses_visible_fallback():
    request = FarmRequest(latitude=14.1203, longitude=-16.0351, current_crop_id="groundnut", demo_profile_id="sahel")
    context, provenance = fallback_for(request)
    assert context.data_quality["climate"] == DataStatus.FALLBACK
    assert provenance[0].status == DataStatus.FALLBACK
    assert context.data_quality["soil"] == DataStatus.ESTIMATED


def test_custom_requests_cannot_select_a_nearest_demo_profile():
    request = FarmRequest(latitude=35.6762, longitude=139.6503, current_crop_id="groundnut")
    with pytest.raises(ValueError, match="valid demo profile"):
        fallback_for(request)


class MockSoilClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get_json(self, url, params):
        self.calls.append((url, params))
        return self.payload


def soilgrids_payload(**overrides):
    values = {"clay": 300, "sand": 400, "silt": 300, "phh2o": 65, "soc": 140}
    values.update(overrides)
    return {"properties": {"layers": [
        {"name": name, "depths": [{"label": "0-30cm", "values": {"mean": value}}]}
        for name, value in values.items()
    ]}}


@pytest.mark.asyncio
async def test_soilgrids_converts_valid_measurements_and_forwards_coordinates():
    client = MockSoilClient(soilgrids_payload())
    adapter = SoilGridsAdapter(client, TTLCache())
    result = await adapter.fetch(35.6762, 139.6503)
    assert client.calls[0][1][:2] == [("lon", "139.6503"), ("lat", "35.6762")]
    assert result.payload["soil_texture"] == "LOAM"
    assert result.payload["soil_ph"] == 6.5
    assert result.payload["soil_organic_matter_pct"] == pytest.approx(2.4136)
    assert result.status == DataStatus.LIVE


@pytest.mark.asyncio
async def test_soilgrids_empty_layers_are_rejected():
    adapter = SoilGridsAdapter(MockSoilClient({"properties": {"layers": []}}), TTLCache())
    with pytest.raises(ValueError, match="omitted required layers"):
        await adapter.fetch(35.6762, 139.6503)


@pytest.mark.asyncio
async def test_soilgrids_cache_key_distinguishes_locations():
    client = MockSoilClient(soilgrids_payload())
    adapter = SoilGridsAdapter(client, TTLCache())
    await adapter.fetch(35.67621, 139.65031)
    await adapter.fetch(35.67622, 139.65032)
    assert len(client.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"clay": 1200},
    {"silt": 100},
    {"phh2o": 150},
    {"soc": float("nan")},
])
async def test_soilgrids_rejects_invalid_measurements(overrides):
    adapter = SoilGridsAdapter(MockSoilClient(soilgrids_payload(**overrides)), TTLCache())
    with pytest.raises(ValueError):
        await adapter.fetch(35.6762, 139.6503)


@pytest.mark.asyncio
async def test_custom_build_context_forwards_coordinates_and_keeps_soil_unavailable(monkeypatch):
    from app.services import environment

    captured = {"climate": [], "soil": []}

    async def climate_fetch(self, latitude, longitude):
        captured["climate"].append((latitude, longitude))
        return AdapterResult({
            "annual_gdd": 1200, "seasonal_rainfall_mm": 500,
            "historical_rainfall_mm": 600, "heat_stress_days": 10,
        }, DataStatus.LIVE)

    async def soil_fetch(self, latitude, longitude):
        captured["soil"].append((latitude, longitude))
        raise ValueError("mock SoilGrids response has no layers")

    async def wcs_fetch(self, latitude, longitude):
        raise ValueError("mock invalid WCS raster")

    monkeypatch.setattr(environment.NasaPowerAdapter, "fetch", climate_fetch)
    monkeypatch.setattr(environment.SoilGridsAdapter, "fetch", soil_fetch)
    monkeypatch.setattr(SoilGridsWCSAdapter, "fetch", wcs_fetch)
    expected = [(35.6762, 139.6503), (28.9845, 77.7064), (-1.2921, 36.8219)]
    for latitude, longitude in expected:
        request = FarmRequest(latitude=latitude, longitude=longitude, current_crop_id="groundnut")
        context, provenance = await build_context(request)
        assert context.soil_ph is None
        assert context.soil_texture is None
        assert context.data_quality["climate"] == DataStatus.LIVE
        assert context.data_quality["soil"] == DataStatus.UNAVAILABLE
        assert any(item.variable == "soil_properties" and item.status == DataStatus.UNAVAILABLE for item in provenance)
    assert captured["climate"] == expected
    assert captured["soil"] == expected


class MockNasaClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def get_json(self, url, params):
        self.calls.append((url, params))
        return self.payload


@pytest.mark.asyncio
async def test_nasa_power_uses_date_keys_and_validates_shuffled_daily_observations(monkeypatch):
    from datetime import date, timedelta
    from app.services import environment

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 12, 31)

    monkeypatch.setattr(environment, "date", FixedDate)
    end = FixedDate.today() - timedelta(days=30)
    start = end - timedelta(days=365 * 4)
    days = []
    current = start
    while current <= end:
        days.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    series = {
        "T2M_MAX": {day: 25 for day in reversed(days)},
        "T2M_MIN": {day: 15 for day in reversed(days)},
        "PRECTOTCORR": {day: 2 for day in reversed(days)},
        "RH2M": {day: 50 for day in reversed(days)},
        "ALLSKY_SFC_SW_DWN": {day: 20 for day in reversed(days)},
    }
    client = MockNasaClient({
        "geometry": {"coordinates": [139.65, 35.676]},
        "properties": {"parameter": series},
    })
    adapter = NasaPowerAdapter(client, TTLCache())
    result = await adapter.fetch(35.6762, 139.6503)

    assert result.status == DataStatus.LIVE
    assert result.payload["seasonal_rainfall_mm"] == 360
    assert result.payload["historical_rainfall_mm"] == 360
    assert result.payload["heat_stress_days"] == 0
    assert client.calls[0][1]["longitude"] == "139.6503"
    assert client.calls[0][1]["latitude"] == "35.6762"


@pytest.mark.asyncio
async def test_nasa_power_rejects_insufficient_or_mismatched_response(monkeypatch):
    from datetime import date, timedelta
    from app.services import environment

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 12, 31)

    monkeypatch.setattr(environment, "date", FixedDate)
    day = (FixedDate.today() - timedelta(days=30)).strftime("%Y%m%d")
    series = {name: {day: value} for name, value in {
        "T2M_MAX": 25, "T2M_MIN": 15, "PRECTOTCORR": 2, "RH2M": 50,
        "ALLSKY_SFC_SW_DWN": 20,
    }.items()}
    payload = {"geometry": {"coordinates": [0, 0]}, "properties": {"parameter": series}}
    with pytest.raises(ValueError, match="coordinates did not match"):
        await NasaPowerAdapter(MockNasaClient(payload), TTLCache()).fetch(35.6762, 139.6503)


@pytest.mark.asyncio
async def test_custom_build_context_fails_instead_of_substituting_demo_climate(monkeypatch):
    from app.services import environment

    async def unavailable(*args, **kwargs):
        raise RuntimeError("mock NASA POWER unavailable")

    monkeypatch.setattr(environment.NasaPowerAdapter, "fetch", unavailable)
    with pytest.raises(RuntimeError, match="no demo climate was substituted"):
        await build_context(FarmRequest(latitude=35.6762, longitude=139.6503, current_crop_id="groundnut"))
