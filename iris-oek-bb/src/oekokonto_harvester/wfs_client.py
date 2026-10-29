"""Controlled OGC WFS 2.0 client: capability discovery and paginated GetFeature."""
from __future__ import annotations

import io
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterator

import geopandas as gpd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings, resolve_path

log = logging.getLogger(__name__)


class WfsError(RuntimeError):
    """Raised when the service does not behave like a WFS."""


@dataclass
class ServiceInfo:
    version: str | None
    feature_types: dict[str, dict] = field(default_factory=dict)   # name -> {default_crs, other_crs}
    supports_paging: bool | None = None
    count_default: int | None = None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class WfsClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.user_agent
        retry = Retry(
            total=settings.max_retries,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))

    # -- HTTP -----------------------------------------------------------------
    def _get(self, params: dict) -> requests.Response:
        response = self.session.get(self.settings.service_url, params=params, timeout=self.settings.timeout_s)
        response.raise_for_status()
        self._check_response(response)
        time.sleep(self.settings.request_delay_s)
        return response

    @staticmethod
    def _check_response(response: requests.Response) -> None:
        """Fail loudly on HTML pages or OGC exception reports instead of parsing them."""
        content_type = response.headers.get("Content-Type", "").lower()
        head = response.content.lstrip()[:1000].lower()
        if "html" in content_type or head.startswith((b"<!doctype html", b"<html")):
            redirects = [h.url for h in response.history]
            raise WfsError(
                f"Server returned HTML instead of WFS content (final URL: {response.url}; redirects: {redirects})."
            )
        if b"exceptionreport" in head:
            root = ET.fromstring(response.content)
            texts = [e.text.strip() for e in root.iter() if _local(e.tag) == "ExceptionText" and e.text]
            raise WfsError("WFS exception report: " + "; ".join(texts))

    # -- Discovery ------------------------------------------------------------
    def discover(self) -> ServiceInfo:
        """GetCapabilities: advertised feature types, CRS and paging options."""
        response = self._get({"service": "WFS", "request": "GetCapabilities"})
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise WfsError(f"GetCapabilities is not well-formed XML: {exc}") from exc

        info = ServiceInfo(version=root.get("version"))
        for element in root.iter():
            name = _local(element.tag)
            if name == "FeatureType":
                children = {_local(c.tag): c for c in element if _local(c.tag) in ("Name", "DefaultCRS")}
                type_name = (children["Name"].text or "").strip() if "Name" in children else None
                if type_name:
                    info.feature_types[type_name] = {
                        "default_crs": (children["DefaultCRS"].text or "").strip() if "DefaultCRS" in children else None,
                        "other_crs": [(c.text or "").strip() for c in element if _local(c.tag) == "OtherCRS"],
                    }
            elif name == "Constraint":
                value = next((v.text for v in element.iter() if _local(v.tag) == "DefaultValue"), None)
                if element.get("name") == "ImplementsResultPaging":
                    info.supports_paging = (value or "").strip().upper() == "TRUE"
                elif element.get("name") == "CountDefault" and value and value.strip().isdigit():
                    info.count_default = int(value)
        return info

    def resolve_type_name(self, info: ServiceInfo) -> str:
        """Match the configured layer against the advertised names (with or without prefix)."""
        wanted = self.settings.layer
        for advertised in info.feature_types:
            if advertised == wanted or advertised.split(":")[-1] == wanted:
                return advertised
        raise WfsError(f"Layer '{wanted}' not advertised. Available: {sorted(info.feature_types)}")

    # -- Harvest --------------------------------------------------------------
    def iter_pages(self, type_name: str) -> Iterator[tuple[int, gpd.GeoDataFrame, str]]:
        """Yield (start_index, features, request_url), one controlled request per page."""
        s = self.settings
        start = 0
        for page in range(s.max_pages):
            params = {
                "service": "WFS",
                "version": s.wfs_version,
                "request": "GetFeature",
                "typeNames": type_name,
                "count": s.page_size,
                "startIndex": start,
            }
            if s.output_format:
                params["outputFormat"] = s.output_format
            if s.sort_by:
                params["sortBy"] = s.sort_by
            response = self._get(params)
            self._save_raw(page, response.content)
            gdf = gpd.read_file(io.BytesIO(response.content))
            if gdf.empty:
                return
            yield start, gdf, response.url
            if len(gdf) < s.page_size:
                return
            start += len(gdf)
        log.warning("max_pages (%s) reached; the harvest may be incomplete", s.max_pages)

    def _save_raw(self, page: int, content: bytes) -> None:
        if not self.settings.raw_dir:
            return
        folder = resolve_path(self.settings.raw_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{self.settings.layer}_page_{page:04d}.raw").write_bytes(content)
