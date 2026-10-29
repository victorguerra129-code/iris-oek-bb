"""Attribute normalization and record building (no network, no database)."""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

import geopandas as gpd
import pandas as pd

from .config import Mapping, Settings
from .geometry import crs_label, process_geometry
from .quality import Outcome, QualityReport, RecordResult

_WHITESPACE = re.compile(r"\s+")


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _raw_json(props: dict[str, Any], source_wkt: str | None) -> str:
    """Source attributes as strict JSON (NaN/inf -> null) plus the original geometry WKT."""
    def safe(value: Any) -> Any:
        if _is_missing(value):
            return None
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value

    clean = {key: safe(value) for key, value in props.items()}
    clean["_source_geom_wkt"] = source_wkt
    return json.dumps(clean, default=str, ensure_ascii=False)


def clean_text(value: Any) -> str | None:
    """Unicode-normalize (NFC), collapse whitespace; empty or missing -> None."""
    if _is_missing(value):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = unicodedata.normalize("NFC", str(value))
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def parse_number(value: Any) -> float | None:
    """Parse numbers, accepting German notation ('1.234,5' or '2,5').

    Known limitation: a lone '1.234' is read as 1.234 (decimal point), not as
    1234; the source format is not guessed beyond the cases above.
    """
    if _is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = clean_text(value)
    if text is None:
        return None
    text = text.replace(" ", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def normalize_status(raw: str | None, status_map: dict[str, str]) -> str | None:
    """Map via the configured table only; unknown values become 'unmapped'."""
    if raw is None:
        return None
    return status_map.get(raw.lower(), "unmapped")


def normalize_area(props: dict[str, Any], mapping: Mapping) -> tuple[float | None, str | None, float | None, list[str]]:
    """Return (declared value, unit label, area in m2, warnings). Never guesses a unit."""
    warnings: list[str] = []
    value_field = mapping.fields.get("area_value")
    raw = props.get(value_field) if value_field else None
    value = parse_number(raw)
    if value is None:
        if clean_text(raw) is not None:
            warnings.append("unparseable_area")
        return None, None, None, warnings
    if value < 0:
        return None, None, None, ["negative_area"]

    unit = clean_text(props.get(mapping.area_unit_field)) if mapping.area_unit_field else None
    unit = unit or mapping.default_area_unit
    if unit is None:
        return value, None, None, ["area_unit_missing"]
    key = unit.lower().replace(" ", "").replace("²", "2")
    factor = mapping.unit_factors.get(key)
    if factor is None:
        return value, unit, None, ["area_unit_unknown"]
    return value, unit, value * factor, warnings


def collect_eco_credits(props: dict[str, Any], names: list[str]) -> dict[str, Any] | None:
    """Copy available ecological-credit attributes; nothing is estimated."""
    collected: dict[str, Any] = {}
    for name in names:
        raw = props.get(name)
        text = clean_text(raw)
        if text is None:
            continue
        number = parse_number(raw)
        collected[name] = number if number is not None else text
    return collected or None


def compute_row_hash(attributes: dict[str, Any], source_wkt: str | None) -> str:
    """Hash of the normalized attributes plus the original geometry (change detection)."""
    payload = json.dumps({"attributes": attributes, "geometry": source_wkt}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_record(
    props: dict[str, Any],
    geom: Any,
    source_crs: str | None,
    mapping: Mapping,
    settings: Settings,
    source_url: str,
    retrieved_at: str,
) -> RecordResult:
    def source(key: str) -> Any:
        name = mapping.fields.get(key)
        return props.get(name) if name else None

    site_id = clean_text(source("site_id"))
    geo = process_geometry(geom, source_crs, settings.target_crs, settings.plausible_bounds, settings.area_crs)

    reasons: list[str] = []
    if site_id is None:
        reasons.append("missing_site_id")
    if not geo.ok:
        reasons.append(geo.rejection_reason or "invalid_geometry")
    if reasons:
        raw = _raw_json(props, geo.source_wkt)
        return RecordResult(Outcome.REJECTED, site_id, reasons=reasons, raw_properties=raw)

    status_raw = clean_text(source("status"))
    status = normalize_status(status_raw, mapping.status_map)
    area_value, area_unit, area_m2, warnings = normalize_area(props, mapping)
    if status == "unmapped":
        warnings.append("unmapped_status")
    if area_m2 and geo.area_geom_m2:
        if abs(area_m2 - geo.area_geom_m2) / geo.area_geom_m2 > settings.area_mismatch_tolerance:
            warnings.append("area_mismatch")

    attributes = {
        "site_name": clean_text(source("site_name")),
        "municipality": clean_text(source("municipality")),
        "cadastral_district": clean_text(source("cadastral_district")),
        "compensation_status_raw": status_raw,
        "compensation_status": status,
        "area_source_value": area_value,
        "area_source_unit": area_unit,
        "area_m2": area_m2,
        "eco_credit_fields": collect_eco_credits(props, mapping.eco_credit_fields),
    }
    record = {
        "state_code": mapping.state_code,
        "site_id": site_id,
        **attributes,
        "area_geom_m2": geo.area_geom_m2,
        "geom_25832": geo.geometry,
        "source_crs": source_crs,
        "source_geom_wkt": geo.source_wkt,
        "geometry_repaired": geo.repaired,
        "source_url": source_url,
        "source_layer": settings.layer,
        "retrieved_at": retrieved_at,
        "row_hash": compute_row_hash(attributes, geo.source_wkt),
    }
    outcome = Outcome.REPAIRED if geo.repaired else Outcome.OK
    return RecordResult(outcome, site_id, warnings=warnings, record=record)


def process_frame(
    gdf: gpd.GeoDataFrame,
    mapping: Mapping,
    settings: Settings,
    source_url: str,
    report: QualityReport,
    retrieved_at: str | None = None,
    row_offset: int = 0,
) -> list[dict[str, Any]]:
    """Normalize one GeoDataFrame (e.g. one WFS page); return the accepted records.

    The same QualityReport can be reused across pages so duplicate site_id
    values are caught over the whole harvest.
    """
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    source_crs = crs_label(gdf.crs) if gdf.crs is not None else settings.source_crs_fallback
    geom_column = gdf.geometry.name
    accepted: list[dict[str, Any]] = []

    attribute_rows = gdf.drop(columns=[geom_column]).to_dict("records")
    for position, (props, geom) in enumerate(zip(attribute_rows, gdf.geometry)):
        result = normalize_record(props, geom, source_crs, mapping, settings, source_url, retrieved_at)
        result.source_row = row_offset + position

        if result.record is not None and result.site_id in report.seen_site_ids:
            result = RecordResult(
                Outcome.REJECTED,
                result.site_id,
                source_row=result.source_row,
                reasons=["duplicate_site_id"],
                raw_properties=_raw_json(props, result.record["source_geom_wkt"]),
            )
        if result.record is not None:
            report.seen_site_ids.add(result.site_id)
            accepted.append(result.record)
        report.add(result)
    return accepted
