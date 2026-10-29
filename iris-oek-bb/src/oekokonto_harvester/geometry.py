"""Geometry validation, repair, promotion to MultiPolygon and reprojection."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry


@dataclass
class GeometryResult:
    geometry: MultiPolygon | None       # working geometry in the target CRS
    source_wkt: str | None              # original geometry, untouched, in the source CRS
    repaired: bool
    area_geom_m2: float | None
    rejection_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.geometry is not None


def crs_label(crs) -> str | None:
    """Return 'EPSG:xxxx' when the CRS has an EPSG code, otherwise its WKT string."""
    if crs is None:
        return None
    crs = CRS.from_user_input(crs)
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.to_string()


@lru_cache(maxsize=32)
def _transformer(source: str, target: str) -> Transformer:
    return Transformer.from_crs(CRS.from_user_input(source), CRS.from_user_input(target), always_xy=True)


def _reproject(geom: BaseGeometry, source: str, target: str) -> BaseGeometry:
    transformer = _transformer(source, target)

    def convert(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom, convert)


def _polygons(geom: BaseGeometry | None) -> list[Polygon]:
    """Collect all polygons from any geometry, recursing into collections."""
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if hasattr(geom, "geoms"):
        found: list[Polygon] = []
        for part in geom.geoms:
            found.extend(_polygons(part))
        return found
    return []


def _to_multipolygon(geom: BaseGeometry | None) -> MultiPolygon | None:
    polygons = [p for p in _polygons(geom) if p.area > 0]
    return MultiPolygon(polygons) if polygons else None


def process_geometry(
    geom: BaseGeometry | None,
    source_crs: str | None,
    target_crs: str,
    bounds: tuple[float, float, float, float],
    area_crs: str = "EPSG:25833",
) -> GeometryResult:
    """Validate, repair where defensible, promote to MultiPolygon and reproject.

    Rejection reasons: missing_geometry, missing_crs, no_polygonal_geometry,
    invalid_after_reprojection, outside_plausible_bounds.
    """
    if not isinstance(geom, BaseGeometry) or geom.is_empty:
        return GeometryResult(None, None, False, None, "missing_geometry")

    source_wkt = geom.wkt  # preserved exactly as received
    if source_crs is None:
        return GeometryResult(None, source_wkt, False, None, "missing_crs")

    repaired = False
    geom = shapely.force_2d(geom)
    if not geom.is_valid:
        geom = shapely.make_valid(geom)
        repaired = True

    multi = _to_multipolygon(geom)
    if multi is None:
        return GeometryResult(None, source_wkt, repaired, None, "no_polygonal_geometry")

    if source_crs != target_crs:
        multi = _reproject(multi, source_crs, target_crs)

    if not multi.is_valid:
        multi = _to_multipolygon(shapely.make_valid(multi))
        repaired = True
        if multi is None:
            return GeometryResult(None, source_wkt, repaired, None, "invalid_after_reprojection")

    minx, miny, maxx, maxy = multi.bounds
    if minx < bounds[0] or miny < bounds[1] or maxx > bounds[2] or maxy > bounds[3]:
        return GeometryResult(None, source_wkt, repaired, None, "outside_plausible_bounds")

    area = multi.area if target_crs == area_crs else _reproject(multi, target_crs, area_crs).area
    return GeometryResult(multi, source_wkt, repaired, float(area))
