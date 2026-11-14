import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from app.models.schemas import DataStatus
from app.services.environment import TTLCache
from app.services.soilgrids_wcs import SoilGridsWCSAdapter


class MockCoverageClient:
    def __init__(self, *, zero_ph=False, nodata=False, wrong_crs=False, wrong_resolution=False):
        self.calls = []
        self.zero_ph = zero_ph
        self.nodata = nodata
        self.wrong_crs = wrong_crs
        self.wrong_resolution = wrong_resolution

    async def get_bytes(self, url, params):
        self.calls.append((url, params))
        values = {("0-5cm", "phh2o"): 60, ("5-15cm", "phh2o"): 70, ("15-30cm", "phh2o"): 80}
        layer_values = {
            "0-5cm": {"clay": 300, "sand": 400, "silt": 300, "ocd": 100, "bdod": 100},
            "5-15cm": {"clay": 400, "sand": 300, "silt": 300, "ocd": 200, "bdod": 200},
            "15-30cm": {"clay": 200, "sand": 500, "silt": 300, "ocd": 300, "bdod": 150},
        }
        coverage = next(value for name, value in params if name == "COVERAGEID")
        property_name, depth, _ = coverage.rsplit("_", 2)
        value = values[(depth, property_name)] if property_name == "phh2o" else layer_values[depth][property_name]
        if self.zero_ph and property_name == "phh2o":
            value = 0
        if self.nodata:
            value = -9999
        pixel_size = 500 if self.wrong_resolution else 250
        transform = from_origin(500, 2500, pixel_size, pixel_size)
        profile = {
            "driver": "GTiff",
            "height": 4,
            "width": 4,
            "count": 1,
            "dtype": "int16",
            "crs": "EPSG:4326" if self.wrong_crs else "EPSG:3857",
            "transform": transform,
            "nodata": -9999,
        }
        with MemoryFile() as memory_file:
            with memory_file.open(**profile) as dataset:
                dataset.write(np.full((4, 4), value, dtype=np.int16), 1)
            return memory_file.read()


@pytest.mark.asyncio
async def test_wcs_validates_and_depth_weights_projected_measurements(monkeypatch):
    from app.services import soilgrids_wcs

    calls = []

    def mock_transform(source, destination, longitudes, latitudes):
        calls.append((source, destination, longitudes, latitudes))
        return [1000], [2000]

    monkeypatch.setattr(soilgrids_wcs, "transform", mock_transform)
    client, cache = MockCoverageClient(), TTLCache()
    adapter = SoilGridsWCSAdapter(client, cache)
    result, status = await adapter.fetch(35.6762, 139.6503)

    assert status == DataStatus.LIVE
    assert result["soil_ph"] == pytest.approx(7.333333)
    assert result["soil_texture"] == "LOAM"
    assert result["soil_organic_carbon_pct"] == pytest.approx(1.473684)
    assert result["soil_organic_matter_pct"] == pytest.approx(2.540632)
    assert len(client.calls) == 18
    assert calls == [("EPSG:4326", "EPSG:3857", [139.6503], [35.6762])]
    assert all(("OUTPUTCRS", "http://www.opengis.net/def/crs/EPSG/0/3857") in params for _, params in client.calls)

    cached_result, cached_status = await adapter.fetch(35.6762, 139.6503)
    assert cached_status == DataStatus.CACHED
    assert cached_result == result
    assert len(client.calls) == 18
    await adapter.fetch(35.6763, 139.6504)
    assert len(client.calls) == 36


@pytest.mark.asyncio
@pytest.mark.parametrize("client", [
    MockCoverageClient(zero_ph=True),
    MockCoverageClient(nodata=True),
    MockCoverageClient(wrong_crs=True),
    MockCoverageClient(wrong_resolution=True),
])
async def test_wcs_rejects_zero_nodata_and_wrong_crs(client):
    adapter = SoilGridsWCSAdapter(client, TTLCache())
    with pytest.raises(ValueError):
        await adapter.fetch(35.6762, 139.6503)
