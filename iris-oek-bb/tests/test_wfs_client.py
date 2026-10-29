"""WFS client tests against a local mock server (no internet access needed)."""
import dataclasses
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from oekokonto_harvester.wfs_client import ServiceInfo, WfsClient, WfsError

from conftest import FIXTURE

FEATURES = json.loads(FIXTURE.read_text(encoding="utf-8"))

CAPABILITIES = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">
  <ows:OperationsMetadata>
    <ows:Constraint name="ImplementsResultPaging"><ows:NoValues/><ows:DefaultValue>TRUE</ows:DefaultValue></ows:Constraint>
    <ows:Constraint name="CountDefault"><ows:NoValues/><ows:DefaultValue>1000</ows:DefaultValue></ows:Constraint>
  </ows:OperationsMetadata>
  <wfs:FeatureTypeList><wfs:FeatureType>
    <wfs:Name>ns:bb_flaechenpool</wfs:Name>
    <wfs:DefaultCRS>urn:ogc:def:crs:EPSG::25833</wfs:DefaultCRS>
    <wfs:OtherCRS>urn:ogc:def:crs:EPSG::25832</wfs:OtherCRS>
  </wfs:FeatureType></wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""

EXCEPTION = b"""<?xml version="1.0"?>
<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">
  <ows:Exception exceptionCode="InvalidParameterValue"><ows:ExceptionText>Unknown feature type</ows:ExceptionText></ows:Exception>
</ows:ExceptionReport>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        query = {k.lower(): v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/html":
            body, ctype = b"<!DOCTYPE html><html><body>INSPIRE-Zentrale</body></html>", "text/html; charset=utf-8"
        elif url.path == "/exception":
            body, ctype = EXCEPTION, "text/xml"
        elif query.get("request") == "GetCapabilities":
            body, ctype = CAPABILITIES, "text/xml"
        else:
            start, count = int(query["startindex"]), int(query["count"])
            page = dict(FEATURES, features=FEATURES["features"][start:start + count])
            body, ctype = json.dumps(page).encode(), "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def base_url():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def client(settings, base_url, path="/wfs", **overrides):
    return WfsClient(dataclasses.replace(settings, service_url=base_url + path, request_delay_s=0,
                                         page_size=4, **overrides))


def test_discovery_reads_types_crs_and_paging(settings, base_url):
    info = client(settings, base_url).discover()
    assert info.version == "2.0.0" and info.supports_paging is True and info.count_default == 1000
    assert info.feature_types["ns:bb_flaechenpool"]["default_crs"] == "urn:ogc:def:crs:EPSG::25833"


def test_layer_is_matched_with_or_without_prefix_and_missing_layer_fails(settings, base_url):
    c = client(settings, base_url)
    assert c.resolve_type_name(c.discover()) == "ns:bb_flaechenpool"
    with pytest.raises(WfsError, match="not advertised"):
        c.resolve_type_name(ServiceInfo(version="2.0.0", feature_types={"ns:other": {}}))


def test_pagination_walks_all_pages_without_overlap(settings, base_url):
    c = client(settings, base_url)
    pages = list(c.iter_pages("ns:bb_flaechenpool"))
    assert [(start, len(gdf)) for start, gdf, _ in pages] == [(0, 4), (4, 4), (8, 2)]
    ids = [i for _, gdf, _ in pages for i in gdf["id"].tolist()]
    assert len(ids) == 10 and ids.count("BB-001") == 2      # the fixture itself contains one duplicate


def test_max_pages_stops_and_warns(settings, base_url, caplog):
    with caplog.at_level(logging.WARNING):
        pages = list(client(settings, base_url, max_pages=1).iter_pages("ns:bb_flaechenpool"))
    assert len(pages) == 1 and "max_pages" in caplog.text


def test_html_response_is_reported_not_parsed(settings, base_url):
    with pytest.raises(WfsError, match="HTML instead of WFS"):
        client(settings, base_url, path="/html").discover()


def test_ogc_exception_report_is_surfaced(settings, base_url):
    with pytest.raises(WfsError, match="Unknown feature type"):
        list(client(settings, base_url, path="/exception").iter_pages("ns:bb_flaechenpool"))


def test_raw_responses_can_be_kept_as_provenance(settings, base_url, tmp_path):
    list(client(settings, base_url, raw_dir=str(tmp_path)).iter_pages("ns:bb_flaechenpool"))
    assert len(list(tmp_path.glob("*.raw"))) == 3
