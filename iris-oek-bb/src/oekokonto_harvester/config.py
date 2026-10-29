"""Configuration loading for the harvester (settings and field mapping)."""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

# <project>/src/oekokonto_harvester/config.py -> <project>
PROJECT_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FIELD_KEYS = ("site_id", "site_name", "municipality", "cadastral_district", "status", "area_value")


def resolve_path(path: str | Path) -> Path:
    """Resolve a relative path against the project root, not the working directory."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class Settings:
    service_url: str
    layer: str
    wfs_version: str = "2.0.0"
    page_size: int = 500
    max_pages: int = 200
    timeout_s: float = 60.0
    request_delay_s: float = 0.5
    max_retries: int = 3
    user_agent: str = "iris-oek-bb-harvester/0.1 (technical assessment)"
    output_format: str | None = None
    sort_by: str | None = None
    target_crs: str = "EPSG:25832"
    area_crs: str = "EPSG:25833"
    source_crs_fallback: str | None = None
    plausible_bounds: tuple[float, float, float, float] = (600000.0, 5650000.0, 950000.0, 5980000.0)
    area_mismatch_tolerance: float = 0.10
    raw_dir: str | None = None
    report_dir: str = "reports"


@dataclass(frozen=True)
class Mapping:
    state_code: str
    fields: dict[str, str | None]
    area_unit_field: str | None
    default_area_unit: str | None
    unit_factors: dict[str, float]
    status_map: dict[str, str]
    eco_credit_fields: list[str]


def _read_yaml(path: str | Path) -> dict[str, Any]:
    with open(resolve_path(path), encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    return data


def load_settings(path: str | Path) -> Settings:
    data = _read_yaml(path)
    known = {f.name for f in fields(Settings)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"Unknown settings keys: {sorted(unknown)}")
    if "service_url" not in data or "layer" not in data:
        raise ValueError("Settings must define 'service_url' and 'layer'")
    if "plausible_bounds" in data:
        data["plausible_bounds"] = tuple(float(v) for v in data["plausible_bounds"])
    settings = Settings(**data)
    if settings.target_crs.upper() != "EPSG:25832":
        raise ValueError("target_crs must be EPSG:25832 (the geometry column is named geom_25832)")
    return settings


def load_mapping(path: str | Path) -> Mapping:
    data = _read_yaml(path)
    src_fields = data.get("fields") or {}
    missing = [k for k in REQUIRED_FIELD_KEYS if k not in src_fields]
    if missing:
        raise ValueError(f"mapping 'fields' is missing keys: {missing}")
    area = data.get("area") or {}
    return Mapping(
        state_code=str(data["state_code"]).strip().upper(),
        fields=dict(src_fields),
        area_unit_field=area.get("unit_field"),
        default_area_unit=area.get("default_unit"),
        unit_factors={str(k).lower(): float(v) for k, v in (area.get("unit_factors") or {}).items()},
        status_map={str(k).strip().lower(): str(v) for k, v in (data.get("status_map") or {}).items()},
        eco_credit_fields=list(data.get("eco_credit_fields") or []),
    )
