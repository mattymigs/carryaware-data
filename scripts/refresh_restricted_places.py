#!/usr/bin/env python3
"""Build CarryAwareNJ's backwards-compatible v2 county datasets.

The legacy manifest and data/ files are intentionally untouched. Released app
versions only understand the original place-type enum and continue to use v1.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = uuid.UUID("f4563f0b-219a-4d46-b415-8ea62b97f005")
COUNTIES = [
    "Atlantic", "Bergen", "Burlington", "Camden", "Cape May", "Cumberland",
    "Essex", "Gloucester", "Hudson", "Hunterdon", "Mercer", "Middlesex",
    "Monmouth", "Morris", "Ocean", "Passaic", "Salem", "Somerset", "Sussex",
    "Union", "Warren",
]
NJ_BOUNDS = (-75.60, 38.85, -73.85, 41.40)

COUNTY_GEOJSON_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "State_County/MapServer/1/query?"
    + urlencode({
        "where": "STATE='34'",
        "outFields": "NAME,GEOID",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    })
)
DCF_CHILD_CARE_URL = "https://data.nj.gov/resource/cru5-4rmm.json?$limit=50000"
DOH_ACUTE_URL = "https://data.nj.gov/resource/mrzk-zrvp.json?$limit=50000"
DOH_LTC_URL = "https://healthapps.nj.gov/facilities/documents2/All_LTC.xlsx"
CRC_MAP_ID = "8bed33fa-9b8c-4c51-bb33-74cd0d98628a"
CRC_MARKERS_URL = f"https://api.atlist.com/v1/map/{CRC_MAP_ID}/markers"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

LEGAL_NOTES = {
    "School": "New Jersey law restricts carry at schools and educational institutions.",
    "College/University": "New Jersey law restricts carry at colleges, universities and other educational institutions.",
    "Preschool": "New Jersey law restricts carry at nursery schools and preschools.",
    "Child Care Center": "New Jersey law restricts carry at licensed child care centers.",
    "Courthouse": "New Jersey law restricts carry in courthouses and places used for court proceedings.",
    "Government Building": "New Jersey law restricts carry in government administrative buildings.",
    "Correctional Facility": "New Jersey law restricts carry in correctional and juvenile detention facilities.",
    "Halfway House": "New Jersey law restricts carry in State-contracted halfway houses.",
    "Polling Place": "Restriction applies while this location is used as a polling place or ballot-storage facility.",
    "Zoo": "New Jersey law restricts carry at zoos.",
    "Summer Camp": "New Jersey law restricts carry at summer camps.",
    "Park": "Restriction applies only when this government-owned or controlled location is designated as a gun-free zone. Verify signs.",
    "Beach": "Restriction applies only when this government-owned or controlled location is designated as a gun-free zone. Verify signs.",
    "Recreation Facility": "Restriction applies only when this government-owned or controlled location is designated as a gun-free zone. Verify signs.",
    "Playground": "Restriction applies only when this government-owned or controlled location is designated as a gun-free zone. Verify signs.",
    "Youth Sports Venue": "Restriction applies during, immediately before and immediately after a youth sporting event.",
    "Library": "New Jersey law restricts carry in publicly owned or leased libraries. Verify ownership.",
    "Museum": "New Jersey law restricts carry in publicly owned or leased museums. Verify ownership.",
    "Shelter": "New Jersey law restricts carry in specified licensed shelters.",
    "Community Residence": "New Jersey law restricts carry in specified licensed community residences.",
    "Alcohol-Serving Location": "New Jersey law restricts carry where alcohol is served for on-premises consumption.",
    "Cannabis Retailer": "New Jersey law restricts carry at licensed cannabis retailers and medical cannabis dispensaries.",
    "Entertainment Facility": "New Jersey law restricts carry at entertainment facilities.",
    "Movie Theater": "New Jersey law restricts carry at movie theaters as entertainment facilities.",
    "Stadium/Arena": "New Jersey law restricts carry at stadiums and arenas.",
    "Racetrack": "New Jersey law restricts carry at racetracks.",
    "Casino": "New Jersey law restricts carry in casinos and related casino property.",
    "Energy Facility": "New Jersey law restricts carry at facilities producing, converting, distributing or storing energy.",
    "Airport": "New Jersey and federal restrictions may apply at airports, especially sterile and secure areas.",
    "Transit Hub": "New Jersey law restricts carry in public transportation hubs.",
    "Hospital": "New Jersey law restricts carry in hospitals.",
    "Health Care Facility": "New Jersey law restricts carry in a broad range of health care facilities.",
    "Mental Health/Addiction Facility": "New Jersey law restricts carry in specified mental-health and addiction-treatment facilities.",
    "Federal Facility": "Federal law or site-specific rules may prohibit firearms. Verify signs and current rules.",
    "Post Office": "Federal law and postal regulations may prohibit firearms. Verify property boundaries and current rules.",
    "Military Facility": "Federal law and site-specific rules may prohibit firearms. Verify signs and current rules.",
}

TYPE_GROUP = {
    "School": "education", "College/University": "education", "Preschool": "education",
    "Child Care Center": "childcare",
    "Hospital": "health", "Health Care Facility": "health",
    "Mental Health/Addiction Facility": "health",
    "Entertainment Facility": "entertainment", "Movie Theater": "entertainment",
    "Stadium/Arena": "entertainment", "Racetrack": "entertainment",
    "Casino": "casino",
}


def fetch_bytes(url: str, *, headers: dict[str, str] | None = None, timeout: int = 180) -> bytes:
    request_headers = {"User-Agent": "CarryAwareNJ dataset builder/2.0"}
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as error:  # pragma: no cover - retry is network-dependent
            last_error = error
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed to download {url}: {last_error}")


def fetch_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    return json.loads(fetch_bytes(url, headers=headers).decode("utf-8"))


def clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def county_name(value: Any) -> str | None:
    text = clean(value).replace(" County", "").title()
    return text if text in COUNTIES else None


def valid_coordinate(lat: float, lon: float) -> bool:
    west, south, east, north = NJ_BOUNDS
    return south <= lat <= north and west <= lon <= east


def deterministic_id(source: str, source_id: str, place_type: str, lat: float, lon: float) -> str:
    key = f"{source}|{source_id}|{place_type}|{lat:.7f}|{lon:.7f}"
    return str(uuid.uuid5(NAMESPACE, key))


def make_place(
    *,
    name: str,
    place_type: str,
    latitude: float,
    longitude: float,
    county: str,
    source: str,
    source_id: str,
    address: str = "",
    confidence: str = "medium",
    notes: str = "",
    priority: int = 50,
) -> dict[str, Any]:
    place = {
        "id": deterministic_id(source, source_id, place_type, latitude, longitude),
        "name": clean(name) or f"Unnamed {place_type}",
        "type": place_type,
        "address": clean(address),
        "county": county,
        "source": source,
        "notes": clean(notes),
        "legalNote": LEGAL_NOTES[place_type],
        "confidence": confidence,
        "latitude": round(float(latitude), 7),
        "longitude": round(float(longitude), 7),
        "_priority": priority,
    }
    return place


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous[0], previous[1]
        x2, y2 = current[0], current[1]
        if (y1 > lat) != (y2 > lat):
            crossing = (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-15) + x1
            if lon < crossing:
                inside = not inside
        previous = current
    return inside


def polygon_contains(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    return bool(polygon) and point_in_ring(lon, lat, polygon[0]) and not any(
        point_in_ring(lon, lat, hole) for hole in polygon[1:]
    )


class CountyLookup:
    def __init__(self, geojson: dict[str, Any]):
        self.records: list[tuple[str, tuple[float, float, float, float], list[Any]]] = []
        for feature in geojson["features"]:
            name = county_name(feature["properties"].get("NAME"))
            if not name:
                continue
            geometry = feature["geometry"]
            polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
            points = [point for polygon in polygons for ring in polygon for point in ring]
            bounds = (
                min(point[0] for point in points), min(point[1] for point in points),
                max(point[0] for point in points), max(point[1] for point in points),
            )
            self.records.append((name, bounds, polygons))

    def find(self, lat: float, lon: float) -> str | None:
        for name, (west, south, east, north), polygons in self.records:
            if west <= lon <= east and south <= lat <= north:
                if any(polygon_contains(lon, lat, polygon) for polygon in polygons):
                    return name
        return None


def osm_coordinate(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def osm_address(tags: dict[str, Any]) -> str:
    street = " ".join(filter(None, [clean(tags.get("addr:housenumber")), clean(tags.get("addr:street"))]))
    locality = ", ".join(filter(None, [
        clean(tags.get("addr:city")),
        clean(tags.get("addr:state")) or "NJ",
        clean(tags.get("addr:postcode")),
    ]))
    return ", ".join(filter(None, [street, locality]))


def classify_osm(tags: dict[str, Any]) -> str | None:
    amenity = tags.get("amenity")
    tourism = tags.get("tourism")
    leisure = tags.get("leisure")
    social = tags.get("social_facility")
    healthcare = tags.get("healthcare")

    if amenity == "school": return "School"
    if amenity in {"college", "university"}: return "College/University"
    if amenity == "kindergarten": return "Preschool"
    if amenity == "childcare" or social == "day_care": return "Child Care Center"
    if amenity == "courthouse": return "Courthouse"
    if amenity == "prison": return "Correctional Facility"
    if amenity == "polling_station": return "Polling Place"
    if amenity == "police" or amenity == "townhall" or tags.get("office") == "government":
        government = clean(tags.get("government")).lower()
        if government in {"federal", "us", "national"}: return "Federal Facility"
        return "Government Building"
    if amenity == "post_office": return "Post Office"
    if amenity == "library": return "Library"
    if amenity == "hospital": return "Hospital"
    if amenity in {"clinic", "doctors", "dentist", "nursing_home"} or healthcare:
        if healthcare == "veterinary":
            return None
        health_text = " ".join(clean(tags.get(key)).lower() for key in (
            "healthcare", "healthcare:speciality", "description", "name"
        ))
        if any(term in health_text for term in ("mental", "psychi", "addiction", "substance")):
            return "Mental Health/Addiction Facility"
        return "Health Care Facility"
    if social:
        if social in {"nursing_home", "assisted_living"}: return "Health Care Facility"
        if social == "group_home": return "Community Residence"
        if social == "shelter": return "Shelter"
        if social == "summer_camp": return "Summer Camp"
        return None
    if amenity in {"bar", "pub", "nightclub", "biergarten"}:
        return "Alcohol-Serving Location"
    if amenity == "casino": return "Casino"
    if amenity == "cinema": return "Movie Theater"
    if amenity in {"theatre", "arts_centre"}: return "Entertainment Facility"
    if amenity in {"bus_station", "ferry_terminal"}: return "Transit Hub"
    if tourism == "museum": return "Museum"
    if tourism == "zoo": return "Zoo"
    if tourism == "theme_park": return "Entertainment Facility"
    if leisure == "stadium": return "Stadium/Arena"
    if tags.get("sport") in {"horse_racing", "motor", "motocross", "karting"}:
        return "Racetrack"
    if leisure in {"sports_centre", "track"}: return "Youth Sports Venue"
    if leisure == "park": return "Park"
    if leisure == "playground": return "Playground"
    if leisure == "recreation_ground": return "Recreation Facility"
    if tags.get("natural") == "beach": return "Beach"
    if tags.get("aeroway") in {"aerodrome", "terminal"}: return "Airport"
    if tags.get("railway") == "station" or tags.get("public_transport") == "station":
        return "Transit Hub"
    if tags.get("shop") == "cannabis": return "Cannabis Retailer"
    if tags.get("power") in {"plant", "substation"}: return "Energy Facility"
    if tags.get("military") or tags.get("landuse") == "military": return "Military Facility"
    return None


def load_osm(path: Path, county_lookup: CountyLookup) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text())
    places: list[dict[str, Any]] = []
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        place_type = classify_osm(tags)
        coordinate = osm_coordinate(element)
        if not place_type or not coordinate:
            continue
        lat, lon = coordinate
        if not valid_coordinate(lat, lon):
            continue
        county = county_lookup.find(lat, lon)
        if not county:
            continue
        address = osm_address(tags)
        name = clean(tags.get("name") or tags.get("brand") or tags.get("operator"))
        if not name and address:
            name = f"{place_type} — {address.split(',')[0]}"
        notes = ""
        source_id = f"{element.get('type', 'element')}/{element.get('id')}"
        places.append(make_place(
            name=name,
            place_type=place_type,
            latitude=lat,
            longitude=lon,
            county=county,
            source="OpenStreetMap contributors",
            source_id=source_id,
            address=address,
            confidence="medium",
            notes=notes,
            priority=40,
        ))
    return places


def geocode_dcf(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float, str]]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    keyed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = str(index)
        keyed[key] = row
        writer.writerow([key, clean(row.get("addr1")), clean(row.get("city")), "NJ", clean(row.get("zip"))])

    boundary = f"----CarryAwareNJ{uuid.uuid4().hex}"
    parts: list[bytes] = []

    def add_part(disposition: str, content: bytes, content_type: str | None = None) -> None:
        headers = [f"--{boundary}", f"Content-Disposition: form-data; {disposition}"]
        if content_type:
            headers.append(f"Content-Type: {content_type}")
        parts.append(("\r\n".join(headers) + "\r\n\r\n").encode() + content + b"\r\n")

    add_part(
        'name="addressFile"; filename="child-care.csv"',
        buffer.getvalue().encode(),
        "text/csv",
    )
    add_part('name="benchmark"', b"Public_AR_Current")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    request = Request(
        "https://geocoding.geo.census.gov/geocoder/locations/addressbatch",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "CarryAwareNJ dataset builder/2.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=300) as response:
        result_rows = list(csv.reader(io.StringIO(response.read().decode("utf-8"))))

    matches: dict[str, tuple[float, float, str]] = {}
    for result in result_rows:
        if len(result) < 6 or result[2].lower() != "match":
            continue
        try:
            lon_text, lat_text = result[5].split(",", 1)
            lat, lon = float(lat_text), float(lon_text)
        except (ValueError, TypeError):
            continue
        if valid_coordinate(lat, lon):
            matches[result[0]] = (lat, lon, clean(result[4]))
    return matches


def load_dcf_child_care() -> list[dict[str, Any]]:
    rows = fetch_json(DCF_CHILD_CARE_URL)
    matches = geocode_dcf(rows)
    places: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        match = matches.get(str(index))
        county = county_name(row.get("county"))
        if not match or not county:
            continue
        lat, lon, matched_address = match
        reference = clean((row.get("inspections") or {}).get("url")) or f"{row.get('center')}|{row.get('addr1')}"
        places.append(make_place(
            name=clean(row.get("center")),
            place_type="Child Care Center",
            latitude=lat,
            longitude=lon,
            county=county,
            source="NJ DCF Licensed Child Care Centers",
            source_id=reference,
            address=matched_address or f"{clean(row.get('addr1'))}, {clean(row.get('city'))}, NJ {clean(row.get('zip'))}",
            confidence="high",
            notes="Licensed center published by the New Jersey Department of Children and Families.",
            priority=100,
        ))
    return places


def load_doh_acute() -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = []
    for row in fetch_json(DOH_ACUTE_URL):
        point = row.get("geocoded_column") or {}
        coordinates = point.get("coordinates") or []
        county = county_name(row.get("county"))
        if len(coordinates) < 2 or not county:
            continue
        lon, lat = float(coordinates[0]), float(coordinates[1])
        if not valid_coordinate(lat, lon):
            continue
        facility_type = clean(row.get("facility_type"))
        type_text = facility_type.lower()
        if any(term in type_text for term in ("mental", "psychi", "addiction", "substance")):
            place_type = "Mental Health/Addiction Facility"
        elif "hospital" in type_text:
            place_type = "Hospital"
        else:
            place_type = "Health Care Facility"
        places.append(make_place(
            name=re.sub(r"\s*\(NJ[^)]*\)\s*$", "", clean(row.get("licensed_name")), flags=re.I),
            place_type=place_type,
            latitude=lat,
            longitude=lon,
            county=county,
            source="NJ DOH Acute Care Facilities",
            source_id=clean(row.get("facid") or row.get("lic")),
            address=clean(row.get("address")),
            confidence="high",
            notes=f"NJ Department of Health facility type: {facility_type}.",
            priority=100,
        ))
    return places


def load_doh_ltc(path: Path) -> list[dict[str, Any]]:
    try:
        import openpyxl
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("openpyxl is required to read the NJ DOH long-term-care workbook") from error

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook["All_LTC"]
    rows = sheet.iter_rows(values_only=True)
    headers = [clean(value) for value in next(rows)]
    places: list[dict[str, Any]] = []
    for values in rows:
        row = dict(zip(headers, values))
        county = county_name(row.get("COUNTY"))
        try:
            lat, lon = float(row.get("LAT")), float(row.get("LNG"))
        except (TypeError, ValueError):
            continue
        if not county or not valid_coordinate(lat, lon):
            continue
        facility_type = clean(row.get("FACILITY_TYPE"))
        places.append(make_place(
            name=re.sub(r"\s*\([^)]*\)\s*$", "", clean(row.get("LICENSED_NAME"))),
            place_type="Health Care Facility",
            latitude=lat,
            longitude=lon,
            county=county,
            source="NJ DOH Long-Term Care Facilities",
            source_id=clean(row.get("FacID") or row.get("LIC#")),
            address=clean(row.get("ADDRESS")),
            confidence="high",
            notes=f"NJ Department of Health long-term-care facility type: {facility_type}.",
            priority=100,
        ))
    workbook.close()
    return places


def load_crc_dispensaries(county_lookup: CountyLookup) -> list[dict[str, Any]]:
    publisher_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    headers = {
        "Authorization": "Bearer public",
        "X-Publisher-Id": publisher_id,
        "X-Session-Id": session_id,
    }
    markers: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        url = CRC_MARKERS_URL
        if token:
            url += "?" + urlencode({"nextToken": token})
        payload = fetch_json(url, headers=headers)
        markers.extend(payload.get("markers", []))
        token = payload.get("nextToken")
        if not token:
            break

    places: list[dict[str, Any]] = []
    for marker in markers:
        try:
            lat, lon = float(marker["lat"]), float(marker["long"])
        except (KeyError, TypeError, ValueError):
            continue
        county = county_lookup.find(lat, lon)
        if not county:
            continue
        tags = ", ".join(clean(tag) for tag in marker.get("tags", []) if clean(tag))
        places.append(make_place(
            name=clean(marker.get("name")),
            place_type="Cannabis Retailer",
            latitude=lat,
            longitude=lon,
            county=county,
            source="NJ Cannabis Regulatory Commission",
            source_id=clean(marker.get("id")),
            address=clean(marker.get("formattedAddress")),
            confidence="high",
            notes=f"Listed on the NJ Cannabis Regulatory Commission dispensary map{': ' + tags if tags else ''}.",
            priority=100,
        ))
    return places


def load_legacy() -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = []
    for county in COUNTIES:
        path = ROOT / "data" / f"{county.lower().replace(' ', '_')}.json"
        for item in json.loads(path.read_text()):
            item = dict(item)
            item["county"] = county
            item.setdefault("legalNote", LEGAL_NOTES.get(item["type"], "Verify current law and posted restrictions."))
            item.setdefault("notes", "")
            item.setdefault("address", "")
            item.setdefault("confidence", "high")
            item["_priority"] = 90
            places.append(item)
    return places


def normalized_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", " ", name.lower())
    name = re.sub(r"\b(the|inc|llc|corp|corporation|facility|center|centre|campus)\b", " ", name)
    return re.sub(r"[^a-z0-9]+", "", name)


def distance_meters(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1, lon1 = math.radians(a["latitude"]), math.radians(a["longitude"])
    lat2, lon2 = math.radians(b["latitude"]), math.radians(b["longitude"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(value))


def duplicate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if TYPE_GROUP.get(a["type"], a["type"]) != TYPE_GROUP.get(b["type"], b["type"]):
        return False
    distance = distance_meters(a, b)
    if distance <= 18:
        return True
    a_name, b_name = normalized_name(a["name"]), normalized_name(b["name"])
    if not a_name or not b_name or a_name.startswith("unnamed") or b_name.startswith("unnamed"):
        return False
    if a_name == b_name and distance <= 500:
        return True
    shorter, longer = sorted((a_name, b_name), key=len)
    return len(shorter) >= 5 and shorter in longer and len(shorter) / len(longer) >= 0.55 and distance <= 100


def dedupe(places: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        places,
        key=lambda item: (-int(item.get("_priority", 0)), item["county"], item["type"], item["name"], item["id"]),
    )
    kept: list[dict[str, Any]] = []
    grid: dict[tuple[str, str, int, int], list[int]] = defaultdict(list)
    for place in ordered:
        group = TYPE_GROUP.get(place["type"], place["type"])
        x, y = int(place["latitude"] / 0.002), int(place["longitude"] / 0.002)
        candidates: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.extend(grid[(place["county"], group, x + dx, y + dy)])
        if any(duplicate(place, kept[index]) for index in candidates):
            continue
        grid[(place["county"], group, x, y)].append(len(kept))
        kept.append(place)
    return kept


def output_place(place: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id", "name", "type", "address", "county", "source", "notes",
        "confidence", "latitude", "longitude",
    )
    return {key: place[key] for key in keys if place.get(key) not in (None, "")}


def download_osm(destination: Path) -> None:
    query = (ROOT / "scripts" / "nj_restricted_places.overpassql").read_text()
    request = Request(
        OVERPASS_URL,
        data=urlencode({"data": query}).encode(),
        headers={"User-Agent": "CarryAwareNJ dataset builder/2.0"},
        method="POST",
    )
    with urlopen(request, timeout=600) as response:
        destination.write_bytes(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--osm-file", type=Path, help="Existing Overpass JSON; downloaded when omitted")
    parser.add_argument("--ltc-file", type=Path, help="Existing NJ DOH All_LTC.xlsx; downloaded when omitted")
    parser.add_argument("--skip-child-care", action="store_true", help="Skip DCF/Census batch geocoding")
    args = parser.parse_args()

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    county_lookup = CountyLookup(fetch_json(COUNTY_GEOJSON_URL))

    osm_path = args.osm_file or Path("/tmp/carryaware-nj-osm.json")
    if not args.osm_file:
        print("Downloading OpenStreetMap places...", file=sys.stderr)
        download_osm(osm_path)

    ltc_path = args.ltc_file or Path("/tmp/carryaware-nj-ltc.xlsx")
    if not args.ltc_file:
        print("Downloading NJ DOH long-term-care workbook...", file=sys.stderr)
        ltc_path.write_bytes(fetch_bytes(DOH_LTC_URL))

    sources: list[tuple[str, list[dict[str, Any]]]] = [
        ("legacy", load_legacy()),
        ("OpenStreetMap", load_osm(osm_path, county_lookup)),
        ("NJ DOH acute care", load_doh_acute()),
        ("NJ DOH long-term care", load_doh_ltc(ltc_path)),
        ("NJ CRC dispensaries", load_crc_dispensaries(county_lookup)),
    ]
    if not args.skip_child_care:
        sources.append(("NJ DCF child care", load_dcf_child_care()))

    for label, items in sources:
        print(f"{label}: {len(items):,}", file=sys.stderr)

    merged = dedupe(item for _, items in sources for item in items)
    by_county: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for place in merged:
        by_county[place["county"]].append(output_place(place))

    output_dir = ROOT / "data" / "v2"
    output_dir.mkdir(parents=True, exist_ok=True)
    for county in COUNTIES:
        entries = sorted(
            by_county[county],
            key=lambda item: (item["type"], item["name"].lower(), item["id"]),
        )
        (output_dir / f"{county.lower().replace(' ', '_')}.json").write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
        )

    legacy_manifest = json.loads((ROOT / "nj_county_manifest.json").read_text())
    manifest = {
        "version": 2,
        "lastUpdated": generated_at,
        "state": "NJ",
        "counties": [
            {
                "county": entry["county"],
                "version": 2,
                "lastUpdated": generated_at,
                "adjacentCounties": entry["adjacentCounties"],
                "datasetURL": (
                    "https://mattymigs.github.io/carryaware-data/data/v2/"
                    f"{entry['county'].lower().replace(' ', '_')}.json"
                ),
            }
            for entry in legacy_manifest["counties"]
        ],
    }
    (ROOT / "nj_county_manifest_v2.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )

    counts = Counter(place["type"] for place in merged)
    summary = {
        "version": 2,
        "generatedAt": generated_at,
        "totalPlaces": len(merged),
        "countsByType": dict(sorted(counts.items())),
        "countsByCounty": {county: len(by_county[county]) for county in COUNTIES},
    }
    (ROOT / "nj_dataset_summary_v2.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(f"v2 total after deduplication: {len(merged):,}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
