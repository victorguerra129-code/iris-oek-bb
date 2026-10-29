import requests
import xml.etree.ElementTree as ET
from pathlib import Path
import geopandas as gpd

BASE = "https://inspire.brandenburg.de/services/oekokonto_wfs"
OUT = Path("exploration")
OUT.mkdir(exist_ok=True)
NS = {"wfs": "http://www.opengis.net/wfs/2.0", "ows": "http://www.opengis.net/ows/1.1"}


def get(params, fname):
    r = requests.get(BASE, params=params, timeout=60)
    ctype = r.headers.get("Content-Type", "")
    print(r.status_code, ctype, "| URL final:", r.url, "| redirecciones:", [h.url for h in r.history])
    (OUT / fname).write_bytes(r.content)  # evidencia, se guarde lo que se guarde
    r.raise_for_status()
    if "html" in ctype.lower() or r.content.lstrip()[:5].lower() in (b"<!doc", b"<html"):
        raise SystemExit(f"El servidor devolvio HTML, no XML de WFS. Evidencia en exploration/{fname}")
    return r.content


caps = get({"service": "WFS", "request": "GetCapabilities"}, "capabilities.xml")
root = ET.fromstring(caps)
print("Raiz del XML:", root.tag)

types = root.findall(".//wfs:FeatureType", NS)
for ft in types:
    print("-", ft.findtext("wfs:Name", namespaces=NS), "|", ft.findtext("wfs:DefaultCRS", namespaces=NS),
          "| otros CRS:", len(ft.findall("wfs:OtherCRS", NS)))

for c in root.findall(".//ows:Constraint", NS):
    if c.get("name") in ("CountDefault", "ImplementsResultPaging"):
        print(c.get("name"), "=", c.findtext(".//ows:DefaultValue", namespaces=NS))

TYPE = next((ft.findtext("wfs:Name", namespaces=NS) for ft in types
             if (ft.findtext("wfs:Name", namespaces=NS) or "").endswith("bb_flaechenpool")), None)
print("Capa encontrada:", TYPE)

if TYPE:
    get({"service": "WFS", "version": "2.0.0", "request": "DescribeFeatureType", "typeNames": TYPE}, "describe.xsd")
    get({"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": TYPE, "count": 5}, "sample.gml")
    gdf = gpd.read_file(OUT / "sample.gml")
    print("CRS:", gdf.crs)
    print("Columnas:", list(gdf.columns))
    print(gdf.head().to_string())