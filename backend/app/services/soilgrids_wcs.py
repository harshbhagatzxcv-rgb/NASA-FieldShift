from __future__ import annotations

import asyncio
import math
from typing import Any, Protocol

from rasterio.crs import CRS
from rasterio.errors import RasterioError
from rasterio.io import MemoryFile
from rasterio.warp import transform

from app.models.schemas import DataStatus

WCS_URL = "https://maps.isric.org/mapserv"
OUTPUT_CRS = "http://www.opengis.net/def/crs/EPSG/0/3857"
PIXEL_SIZE_M = 250
DEPTHS = (("0-5cm", 5), ("5-15cm", 10), ("15-30cm", 15))
PROPERTIES = ("clay", "sand", "silt", "ocd", "bdod")
MAX_RASTER_BYTES = 1_000_000
MAX_RETRY_AFTER_SECONDS = 8


class CoverageCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...

    def set(self, key: str, value: dict[str, Any]) -> None: ...


class CoverageClient(Protocol):
    async def get_bytes(self, url: str, params: list[tuple[str, str]]) -> bytes: ...


class SoilGridsWCSAdapter:
    def __init__(self, client: CoverageClient, cache: CoverageCache):
        self.client, self.cache = client, cache

    async def fetch(self, latitude: float, longitude: float) -> tuple[dict[str, Any], DataStatus]:
        key = f"soilgrids-wcs:{latitude:.6f}:{longitude:.6f}"
        if cached := self.cache.get(key):
            return cached, DataStatus.CACHED

        x_values, y_values = transform("EPSG:4326", "EPSG:3857", [longitude], [latitude])
        x, y = x_values[0], y_values[0]
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("Coordinate is outside the SoilGrids WCS output CRS domain")

        depth_values: list[dict[str, float]] = []
        semaphore = asyncio.Semaphore(3)

        async def sample(property_name: str, depth: str) -> float:
            async with semaphore:
                return await self._sample_coverage(property_name, depth, x, y)

        for depth, _ in DEPTHS:
            ph_value = await sample("phh2o", depth)
            if not 30 <= ph_value <= 100:
                raise ValueError("SoilGrids WCS returned an invalid pH measurement")
            values = await asyncio.gather(*(sample(name, depth) for name in PROPERTIES))
            layer = dict(zip(PROPERTIES, values))
            clay, sand, silt = (layer[name] for name in ("clay", "sand", "silt"))
            if any(value < 0 or value > 1000 for value in (clay, sand, silt)) or not 900 <= clay + sand + silt <= 1100:
                raise ValueError("SoilGrids WCS returned invalid texture fractions")
            if not 0 <= layer["ocd"] <= 5000 or not 10 <= layer["bdod"] <= 265:
                raise ValueError("SoilGrids WCS returned invalid carbon-density or bulk-density measurements")
            layer["phh2o"] = ph_value
            depth_values.append(layer)

        depth_total = sum(weight for _, weight in DEPTHS)

        def weighted(property_name: str) -> float:
            return sum(
                values[property_name] * weight
                for values, (_, weight) in zip(depth_values, DEPTHS)
            ) / depth_total

        clay, sand, silt = (weighted(name) for name in ("clay", "sand", "silt"))
        if not 900 <= clay + sand + silt <= 1100:
            raise ValueError("SoilGrids WCS depth-weighted texture fractions are invalid")
        clay_pct, sand_pct = clay / 10, sand / 10
        texture = "CLAY" if clay_pct > 45 else "SANDY" if sand_pct > 70 else "CLAY_LOAM" if clay_pct > 32 else "SANDY_LOAM" if sand_pct > 52 else "LOAM"

        carbon_density = weighted("ocd") / 10
        bulk_density = weighted("bdod") / 100
        organic_carbon_pct = carbon_density / (bulk_density * 1000) * 100
        organic_matter_pct = organic_carbon_pct * 1.724
        if not math.isfinite(organic_matter_pct) or not 0 <= organic_carbon_pct <= 58 or organic_matter_pct > 100:
            raise ValueError("SoilGrids WCS depth-weighted organic matter is outside its valid range")

        result = {
            "soil_texture": texture,
            "soil_ph": weighted("phh2o") / 10,
            "soil_organic_carbon_pct": organic_carbon_pct,
            "soil_organic_matter_pct": organic_matter_pct,
        }
        self.cache.set(key, result)
        return result, DataStatus.LIVE

    async def _sample_coverage(self, property_name: str, depth: str, x: float, y: float) -> float:
        params = [
            ("map", f"/map/{property_name}.map"),
            ("SERVICE", "WCS"),
            ("VERSION", "2.0.1"),
            ("REQUEST", "GetCoverage"),
            ("COVERAGEID", f"{property_name}_{depth}_mean"),
            ("FORMAT", "image/tiff"),
            ("SUBSET", f"X({x - 500},{x + 500})"),
            ("SUBSET", f"Y({y - 500},{y + 500})"),
            ("SUBSETTINGCRS", OUTPUT_CRS),
            ("OUTPUTCRS", OUTPUT_CRS),
        ]
        content = await self.client.get_bytes(WCS_URL, params)
        if len(content) > MAX_RASTER_BYTES or content[:4] not in (b"II*\x00", b"MM\x00*"):
            raise ValueError(f"SoilGrids WCS returned an invalid GeoTIFF for {property_name}")

        try:
            with MemoryFile(content) as memory_file, memory_file.open() as dataset:
                if dataset.crs != CRS.from_epsg(3857) or dataset.count != 1:
                    raise ValueError("SoilGrids WCS GeoTIFF has an unexpected CRS or band count")
                if not 0 < dataset.width <= 16 or not 0 < dataset.height <= 16:
                    raise ValueError("SoilGrids WCS GeoTIFF subset has unexpected dimensions")
                if dataset.transform.b != 0 or dataset.transform.d != 0:
                    raise ValueError("SoilGrids WCS GeoTIFF has an unsupported rotated grid")
                x_resolution, y_resolution = abs(dataset.transform.a), abs(dataset.transform.e)
                if (
                    not 200 <= x_resolution <= 300
                    or not 200 <= y_resolution <= 300
                    or abs(x_resolution - y_resolution) > 1
                ):
                    raise ValueError("SoilGrids WCS GeoTIFF has an unexpected spatial resolution")
                bounds = dataset.bounds
                if not bounds.left <= x < bounds.right or not bounds.bottom < y <= bounds.top:
                    raise ValueError("Coordinate falls outside the returned SoilGrids WCS raster bounds")
                row, column = dataset.index(x, y)
                if not (0 <= row < dataset.height and 0 <= column < dataset.width):
                    raise ValueError("Coordinate could not be sampled from the SoilGrids WCS raster")
                window = ((row, row + 1), (column, column + 1))
                if dataset.read_masks(1, window=window)[0, 0] == 0:
                    raise ValueError("SoilGrids WCS pixel is NoData")
                raw_value = float(dataset.read(1, window=window)[0, 0])
                if not math.isfinite(raw_value) or raw_value in (-32768, 32767, 65535):
                    raise ValueError("SoilGrids WCS pixel contains an invalid NoData value")
                return raw_value
        except RasterioError as exc:
            raise ValueError(f"SoilGrids WCS returned an unreadable GeoTIFF for {property_name}") from exc
