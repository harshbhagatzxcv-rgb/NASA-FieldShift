from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import DataStatus
from app.services import environment
from app.services.environment import AdapterResult
from app.services.soilgrids_wcs import SoilGridsWCSAdapter

client = TestClient(app)


def payload(profile="sahel"):
    values = {
        "sahel": [14.1203, -16.0351, "groundnut"],
        "iowa": [42.0308, -93.6319, "maize"],
        "punjab": [30.1575, 71.5249, "cotton"],
        "andes": [-13.5319, -71.9675, "potato"],
        "western_australia": [-31.48, 117.16, "barley"],
    }[profile]
    return {"latitude": values[0], "longitude": values[1], "current_crop_id": values[2], "is_irrigated": profile == "punjab", "farmer_priority": "balanced", "demo_profile_id": profile}


def test_health_and_crops():
    assert client.get("/api/v1/health").status_code == 200
    assert len(client.get("/api/v1/crops").json()["crops"]) >= 15


def test_all_demo_profiles():
    for profile in ("sahel", "iowa", "punjab", "andes", "western_australia"):
        response = client.post("/api/v1/farms/evaluate", json=payload(profile))
        assert response.status_code == 200, response.text
        assert len(response.json()["recommended_rotations"]) == 3


def test_simulation_reranks():
    original = client.post("/api/v1/farms/evaluate", json=payload()).json()
    response = client.post("/api/v1/farms/simulate", json={
        "evaluation_id": original["evaluation_id"], "rainfall_change_pct": -30,
        "temperature_change_c": 3, "irrigation_available": False,
    })
    assert response.status_code == 200
    assert response.json()["environmental_context"]["seasonal_rainfall_mm"] < original["environmental_context"]["seasonal_rainfall_mm"]


def test_custom_coordinates_reach_adapters_and_farmer_texture_is_labeled(monkeypatch):
    received = {"climate": [], "soil": []}

    async def climate_fetch(self, latitude, longitude):
        received["climate"].append((latitude, longitude))
        return AdapterResult({
            "annual_gdd": 1200, "seasonal_rainfall_mm": 500,
            "historical_rainfall_mm": 600, "heat_stress_days": 10,
        }, DataStatus.LIVE)

    async def soil_fetch(self, latitude, longitude):
        received["soil"].append((latitude, longitude))
        return AdapterResult({
            "soil_texture": "SANDY_LOAM", "soil_ph": 6.5,
            "soil_organic_carbon_pct": 1.39, "soil_organic_matter_pct": 2.4,
        }, DataStatus.LIVE)

    monkeypatch.setattr(environment.NasaPowerAdapter, "fetch", climate_fetch)
    monkeypatch.setattr(environment.SoilGridsAdapter, "fetch", soil_fetch)
    locations = [
        (35.6762, 139.6503, None, "SANDY_LOAM"),
        (28.9845, 77.7064, "CLAY", "CLAY"),
        (-1.2921, 36.8219, None, "SANDY_LOAM"),
        (30.3165, 78.0322, None, "SANDY_LOAM"),
        (36.7783, -119.4179, None, "SANDY_LOAM"),
    ]
    for latitude, longitude, override, expected_texture in locations:
        body = {
            "latitude": latitude, "longitude": longitude,
            "current_crop_id": "groundnut",
        }
        if override:
            body["soil_texture"] = override
        response = client.post("/api/v1/farms/evaluate", json=body)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["environmental_context"]["soil_texture"] == expected_texture
        assert result["environmental_context"]["soil_ph"] == 6.5
        assert result["environmental_context"]["soil_organic_carbon_pct"] == 1.39
        if override:
            texture_source = next(item for item in result["data_provenance"] if item["variable"] == "soil_texture")
            properties_source = next(item for item in result["data_provenance"] if item["variable"] == "soil_properties")
            assert texture_source["status"] == DataStatus.USER_PROVIDED
            assert texture_source["source"] == "Farmer-provided soil texture"
            assert properties_source["status"] == DataStatus.LIVE
        else:
            assert all(item["variable"] != "soil_texture" for item in result["data_provenance"])
    expected_coordinates = [(latitude, longitude) for latitude, longitude, _, _ in locations]
    assert received["climate"] == expected_coordinates
    assert received["soil"] == expected_coordinates


def test_custom_request_reports_missing_soil_without_demo_values(monkeypatch):
    async def climate_fetch(self, latitude, longitude):
        return AdapterResult({
            "annual_gdd": 1200, "seasonal_rainfall_mm": 500,
            "historical_rainfall_mm": 600, "heat_stress_days": 10,
        }, DataStatus.LIVE)

    async def unavailable_soil(self, latitude, longitude):
        raise RuntimeError("mock SoilGrids service unavailable")

    monkeypatch.setattr(environment.NasaPowerAdapter, "fetch", climate_fetch)
    monkeypatch.setattr(environment.SoilGridsAdapter, "fetch", unavailable_soil)
    async def unavailable_wcs(self, latitude, longitude):
        raise ValueError("mock invalid WCS raster")

    monkeypatch.setattr(SoilGridsWCSAdapter, "fetch", unavailable_wcs)
    response = client.post("/api/v1/farms/evaluate", json={
        "latitude": 35.6762, "longitude": 139.6503, "current_crop_id": "groundnut",
    })
    assert response.status_code == 200, response.text
    result = response.json()
    context = result["environmental_context"]
    assert context["soil_texture"] is None
    assert context["soil_ph"] is None
    assert context["soil_organic_matter_pct"] is None
    assert context["data_quality"]["soil"] == DataStatus.UNAVAILABLE
    soil_source = next(item for item in result["data_provenance"] if item["variable"] == "soil_properties")
    assert soil_source["status"] == DataStatus.UNAVAILABLE
    assert soil_source["source"] == "Soil observations unavailable for this location."
    assert any("Soil observations are unavailable" in warning for warning in result["recommended_rotations"][0]["warnings"])


def test_rest_failure_falls_back_to_validated_wcs_and_preserves_coordinates(monkeypatch):
    received = []

    async def climate_fetch(self, latitude, longitude):
        return AdapterResult({
            "annual_gdd": 1200, "seasonal_rainfall_mm": 500,
            "historical_rainfall_mm": 600, "heat_stress_days": 10,
        }, DataStatus.LIVE)

    async def unavailable_rest(self, latitude, longitude):
        raise RuntimeError("mock paused SoilGrids REST")

    async def wcs_fetch(self, latitude, longitude):
        received.append((latitude, longitude))
        return {
            "soil_texture": "LOAM", "soil_ph": 6.5,
            "soil_organic_carbon_pct": 1.2, "soil_organic_matter_pct": 2.0688,
        }, DataStatus.LIVE

    monkeypatch.setattr(environment.NasaPowerAdapter, "fetch", climate_fetch)
    monkeypatch.setattr(environment.SoilGridsAdapter, "fetch", unavailable_rest)
    monkeypatch.setattr(SoilGridsWCSAdapter, "fetch", wcs_fetch)
    response = client.post("/api/v1/farms/evaluate", json={
        "latitude": 35.6762, "longitude": 139.6503, "current_crop_id": "groundnut",
    })

    assert response.status_code == 200, response.text
    result = response.json()
    assert received == [(35.6762, 139.6503)]
    assert result["environmental_context"]["data_quality"]["soil"] == DataStatus.LIVE
    soil_source = next(item for item in result["data_provenance"] if item["variable"] == "soil_properties")
    assert soil_source["source"] == "ISRIC SoilGrids WCS modeled estimates (250 m; depth-weighted 0-30 cm)"


def test_nasa_power_failure_is_http_503_not_demo_fallback(monkeypatch):
    async def unavailable(self, latitude, longitude):
        raise RuntimeError("NASA POWER data is unavailable for this location; no demo climate was substituted")

    monkeypatch.setattr(environment.NasaPowerAdapter, "fetch", unavailable)
    response = client.post("/api/v1/farms/evaluate", json={
        "latitude": 35.6762, "longitude": 139.6503, "current_crop_id": "groundnut",
    })

    assert response.status_code == 503
    assert "no demo climate was substituted" in response.json()["detail"]
