import pytest
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon, box

from oekokonto_harvester.geometry import crs_label, process_geometry

BOUNDS = (600000.0, 5650000.0, 950000.0, 5980000.0)
SRC, DST = "EPSG:25833", "EPSG:25832"


def square(x=368000.0, y=5807000.0, size=100.0):
    return box(x, y, x + size, y + size)


def test_reprojects_and_promotes_to_multipolygon():
    result = process_geometry(square(), SRC, DST, BOUNDS)
    assert result.ok and isinstance(result.geometry, MultiPolygon)
    assert not result.repaired
    assert result.area_geom_m2 == pytest.approx(10000, rel=0.01)
    assert BOUNDS[0] <= result.geometry.bounds[0] and result.geometry.bounds[2] <= BOUNDS[2]


def test_same_crs_is_not_reprojected():
    geom = square(700000.0, 5800000.0)
    result = process_geometry(geom, DST, DST, BOUNDS)
    assert result.geometry.equals(MultiPolygon([geom]))


def test_original_wkt_is_preserved():
    geom = square()
    assert process_geometry(geom, SRC, DST, BOUNDS).source_wkt == geom.wkt


def test_self_intersecting_polygon_is_repaired():
    bowtie = Polygon([(0, 0), (100, 100), (100, 0), (0, 100)])
    bowtie = Polygon([(x + 368000, y + 5807000) for x, y in bowtie.exterior.coords])
    assert not bowtie.is_valid
    result = process_geometry(bowtie, SRC, DST, BOUNDS)
    assert result.ok and result.repaired and result.geometry.is_valid


def test_z_coordinates_are_dropped():
    geom = Polygon([(368000, 5807000, 5), (368100, 5807000, 5), (368100, 5807100, 5), (368000, 5807100, 5)])
    result = process_geometry(geom, SRC, DST, BOUNDS)
    assert result.ok and not result.geometry.has_z


def test_collection_with_polygon_keeps_only_the_polygon():
    collection = GeometryCollection([square(), LineString([(368000, 5807000), (368500, 5807500)])])
    result = process_geometry(collection, SRC, DST, BOUNDS)
    assert result.ok and result.geometry.geom_type == "MultiPolygon"


@pytest.mark.parametrize("geom, crs, reason", [
    (None, SRC, "missing_geometry"),
    (Polygon(), SRC, "missing_geometry"),
    (float("nan"), SRC, "missing_geometry"),
    (square(), None, "missing_crs"),
    (LineString([(368000, 5807000), (368100, 5807050)]), SRC, "no_polygonal_geometry"),
    (square(700000.0, 5300000.0), SRC, "outside_plausible_bounds"),
])
def test_rejections(geom, crs, reason):
    result = process_geometry(geom, crs, DST, BOUNDS)
    assert not result.ok and result.rejection_reason == reason


def test_crs_label():
    assert crs_label(None) is None
    assert crs_label("EPSG:25832") == "EPSG:25832"
