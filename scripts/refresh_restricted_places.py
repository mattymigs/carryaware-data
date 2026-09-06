#!/usr/bin/env python3
"""Build CarryAwareNJ's backwards-compatible v2 county datasets.

The legacy manifest and data/ files are intentionally untouched. Released app
versions only understand the original place-type enum and continue to use v1.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = uuid.UUID("f4563f0b-219a-4d46-b415-8ea62b97f005")
DATASET_REVISION = 3
COUNTIES = [
    "Atlantic", "Bergen", "Burlington", "Camden", "Cape May", "Cumberland",
    "Essex", "Gloucester", "Hudson", "Hunterdon", "Mercer", "Middlesex",
    "Monmouth", "Morris", "Ocean", "Passaic", "Salem", "Somerset", "Sussex",
    "Union", "Warren",
]
ABC_COUNTY_BY_PREFIX = {
    f"{index:02d}": county for index, county in enumerate(COUNTIES, start=1)
}
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
ABC_RETAIL_URL = (
    "https://www.njoag.gov/wp-content/uploads/2026/07/"
    "RETAIL-LICENSE-REPORT-JULY-2026.xlsx"
)
ABC_SOURCE_PAGE = (
    "https://www.njoag.gov/about/divisions-and-offices/"
    "division-of-alcoholic-beverage-control-home/"
    "licensing-bureau-applications-and-information/licensing-reports/"
)
ABC_REPORT_AS_OF = "2026-07-01"
ABC_REPORT_LABEL = "July 2026"
ABC_REPORT_FILENAME = "RETAIL-LICENSE-REPORT-JULY-2026.xlsx"
ABC_REPORT_SHA256 = "5b43a9e675aa404ef45fb770c74282597c5a5bbec056d53698a5bb33791b41fd"
ABC_REVIEWED_GEOCODE_CACHE = ROOT / "data_sources" / "nj_abc_geocodes_2026-07.json"
ABC_REVIEWED_GEOCODE_CACHE_SHA256 = "4469ccccab6c6134f46490fa2b928142b2a3de6afdbf127a9546425e91630fe5"
ABC_REVIEWED_GEOCODE_COUNT = 5_178
ABC_GEOCODE_REVIEW = ROOT / "data_sources" / "nj_abc_geocode_review_2026-07.json"
ABC_GEOCODE_REVIEW_SHA256 = "9bbcb1c85a9f2d68296dc94fd39f2dec6752963dacbe33e8a2fe62a4396ce732"
ABC_UNMATCHED_REPORT = ROOT / "nj_abc_unmatched_v2.json"
ABC_ALLOWED_GEOCODE_PROVIDERS = {
    "US Census Bureau",
    "NJ Office of GIS NJ_Geocode",
}
CRC_MAP_ID = "8bed33fa-9b8c-4c51-bb33-74cd0d98628a"
CRC_MARKERS_URL = f"https://api.atlist.com/v1/map/{CRC_MAP_ID}/markers"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CENSUS_BATCH_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
CENSUS_BATCH_SIZE = 5_000
DEDUPE_GRID_SIZE_DEGREES = 0.002
# At New Jersey's northern latitude, three 0.002-degree longitude cells span
# just over 500 meters, the largest distance used by duplicate().
DEDUPE_GRID_NEIGHBOR_RADIUS = 3

# These municipal retail-license codes authorize alcohol for on-premises
# consumption. Codes 43 and 44 are distribution/package-store licenses and are
# intentionally excluded. The code is parsed from the license number so minor
# wording changes in the State workbook do not silently remove locations.
ABC_ON_PREMISE_RETAIL_CODES = {"31", "32", "33", "34", "35", "36", "37"}
ABC_REQUIRED_COLUMNS = {
    "license number",
    "license type",
    "establishment",
    "licensee",
    "inactivity start date",
    "effective date",
    "city",
    "premise address",
}
ABC_SOURCE = "NJ ABC Active Retail Licenses"
ABC_POSTAL_CITY_ALIASES = {"BURLINGTON TOWNSHIP", "FREEHOLD TOWNSHIP"}
ABC_REGRESSION_LICENSES = {
    "0704-33-007-007": ("Grasshopper Bar & Restaurant", "Essex"),
    "0707-33-004-007": ("Danahers", "Essex"),
}
ABC_LICENSE_TYPE_FALLBACK = {
    "club license": "31",
    "plenary retail consumption license with broad c": "32",
    "plenary retail consumption license": "33",
    "seasonal retail cons lic 7/1-11/14 and 5/1-6/30": "34",
    "seasonal retail cons lic 11/15-4/30": "35",
    "hotel/motel license": "36",
    "theater license": "37",
}

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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def display_name(value: Any) -> str:
    """Make State-report all-caps names readable while preserving acronyms."""

    text = clean(value)
    if not text or text != text.upper() or not any(character.isalpha() for character in text):
        return text
    text = text.title()
    text = re.sub(r"(?<=\w)'S\b", "'s", text)
    text = re.sub(
        r"\b(\d+)(St|Nd|Rd|Th)\b",
        lambda match: match.group(1) + match.group(2).lower(),
        text,
    )
    for acronym in ("AFC", "AMC", "BBQ", "Elks", "EMS", "IHOP", "LLC", "LP", "NJ", "USA", "VFW", "YMCA"):
        text = re.sub(rf"\b{re.escape(acronym.title())}\b", acronym, text)
    return text


def county_name(value: Any) -> str | None:
    text = re.sub(r"\s*(?:\.{3}|…)\s*$", "", clean(value))
    text = re.sub(r"\s+County$", "", text, flags=re.IGNORECASE).title()
    return text if text in COUNTIES else None


def valid_coordinate(lat: float, lon: float) -> bool:
    west, south, east, north = NJ_BOUNDS
    return south <= lat <= north and west <= lon <= east


def deterministic_id(source: str, source_id: str, place_type: str, lat: float, lon: float) -> str:
    key = f"{source}|{source_id}|{place_type}|{lat:.7f}|{lon:.7f}"
    return str(uuid.uuid5(NAMESPACE, key))


def deterministic_source_id(source: str, source_id: str, place_type: str) -> str:
    key = f"{source}|{source_id}|{place_type}"
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
    stable_source_id: bool = False,
) -> dict[str, Any]:
    place = {
        "id": (
            deterministic_source_id(source, source_id, place_type)
            if stable_source_id
            else deterministic_id(source, source_id, place_type, latitude, longitude)
        ),
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


def geocode_match(
    latitude: float,
    longitude: float,
    address: str = "",
    *,
    provider: str = "",
    **metadata: Any,
) -> dict[str, Any]:
    match: dict[str, Any] = {
        "latitude": float(latitude),
        "longitude": float(longitude),
        "address": clean(address),
    }
    if clean(provider):
        match["provider"] = clean(provider)
    for key, value in metadata.items():
        if value not in (None, ""):
            match[key] = value
    return match


def geocode_components(match: Any) -> tuple[float, float, str]:
    """Return coordinates/address from current or legacy in-memory matches."""

    if isinstance(match, dict):
        return (
            float(match["latitude"]),
            float(match["longitude"]),
            clean(match.get("address")),
        )
    latitude, longitude, address = match
    return float(latitude), float(longitude), clean(address)


def census_batch_geocode(
    addresses: Iterable[tuple[str, str, str, str, str]],
    *,
    label: str = "addresses",
) -> dict[str, dict[str, Any]]:
    """Geocode keyed US addresses with the Census batch service.

    Input tuples are ``(key, street, city, state, zip)``. Work is split below
    the service's 10,000-row limit so a statewide source can be retried in
    manageable pieces and callers can persist the returned keyed matches.
    """

    pending = list(addresses)
    matches: dict[str, dict[str, Any]] = {}
    for start in range(0, len(pending), CENSUS_BATCH_SIZE):
        batch = pending[start:start + CENSUS_BATCH_SIZE]
        matches.update(_census_batch_geocode_request(batch, label=label))
        print(
            f"Census geocoded {min(start + len(batch), len(pending)):,}/"
            f"{len(pending):,} {label}",
            file=sys.stderr,
        )
    return matches


def _census_batch_geocode_request(
    addresses: list[tuple[str, str, str, str, str]],
    *,
    label: str,
) -> dict[str, dict[str, Any]]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for key, street, city, state, zip_code in addresses:
        writer.writerow([key, clean(street), clean(city), clean(state), clean(zip_code)])

    boundary = f"----CarryAwareNJ{uuid.uuid4().hex}"
    parts: list[bytes] = []

    def add_part(disposition: str, content: bytes, content_type: str | None = None) -> None:
        headers = [f"--{boundary}", f"Content-Disposition: form-data; {disposition}"]
        if content_type:
            headers.append(f"Content-Type: {content_type}")
        parts.append(("\r\n".join(headers) + "\r\n\r\n").encode() + content + b"\r\n")

    add_part(
        f'name="addressFile"; filename="{re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")}.csv"',
        buffer.getvalue().encode(),
        "text/csv",
    )
    add_part('name="benchmark"', b"Public_AR_Current")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    request = Request(
        CENSUS_BATCH_GEOCODER_URL,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "CarryAwareNJ dataset builder/2.0",
        },
        method="POST",
    )
    expected_keys = [item[0] for item in addresses]
    last_error: Exception | None = None
    result_rows: list[list[str]] = []
    for attempt in range(3):
        try:
            with urlopen(request, timeout=300) as response:
                result_rows = list(csv.reader(io.StringIO(response.read().decode("utf-8"))))
            validate_census_response(result_rows, expected_keys)
            break
        except Exception as error:  # pragma: no cover - retry is network-dependent
            last_error = error
            if attempt < 2:
                time.sleep(2 ** attempt)
    else:  # pragma: no cover - retry is network-dependent
        raise RuntimeError(f"Census geocoding failed for {label}: {last_error}")

    matches: dict[str, dict[str, Any]] = {}
    for result in result_rows:
        if len(result) < 6 or result[2].lower() != "match":
            continue
        try:
            lon_text, lat_text = result[5].split(",", 1)
            lat, lon = float(lat_text), float(lon_text)
        except (ValueError, TypeError):
            continue
        if valid_coordinate(lat, lon):
            matches[result[0]] = geocode_match(
                lat,
                lon,
                clean(result[4]),
                provider="US Census Bureau",
                matchType=clean(result[3]),
            )
    return matches


def validate_census_response(
    result_rows: list[list[str]],
    expected_keys: Iterable[str],
) -> None:
    expected = list(expected_keys)
    if len(expected) != len(set(expected)):
        raise ValueError("Census request contains duplicate keys")
    malformed = [row for row in result_rows if len(row) < 3]
    if malformed:
        raise ValueError(f"Census response contains {len(malformed)} malformed rows")
    returned = [row[0] for row in result_rows]
    duplicates = [key for key, count in Counter(returned).items() if count != 1]
    missing = sorted(set(expected) - set(returned))
    unexpected = sorted(set(returned) - set(expected))
    unknown_statuses = sorted({
        clean(row[2]) for row in result_rows
        if clean(row[2]).lower() not in {"match", "no_match", "tie"}
    })
    invalid_matches = 0
    for row in result_rows:
        if clean(row[2]).lower() != "match":
            continue
        if len(row) < 6:
            invalid_matches += 1
            continue
        try:
            longitude_text, latitude_text = row[5].split(",", 1)
            latitude, longitude = float(latitude_text), float(longitude_text)
            if not math.isfinite(latitude) or not math.isfinite(longitude):
                raise ValueError
        except (TypeError, ValueError):
            invalid_matches += 1
    if (
        len(result_rows) != len(expected)
        or duplicates
        or missing
        or unexpected
        or unknown_statuses
        or invalid_matches
    ):
        raise ValueError(
            "Incomplete Census response: "
            f"expected={len(expected)}, returned={len(result_rows)}, "
            f"duplicates={len(duplicates)}, missing={len(missing)}, "
            f"unexpected={len(unexpected)}, unknownStatuses={unknown_statuses}, "
            f"invalidMatches={invalid_matches}"
        )


def geocode_dcf(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    addresses = [
        (
            str(index),
            clean(row.get("addr1")),
            clean(row.get("city")),
            "NJ",
            clean(row.get("zip")),
        )
        for index, row in enumerate(rows)
    ]
    return census_batch_geocode(addresses, label="child-care addresses")


def abc_license_code(license_number: str, license_type: str = "") -> str | None:
    parts = clean(license_number).split("-")
    if len(parts) >= 2 and re.fullmatch(r"\d{2}", parts[1]):
        return parts[1]
    return ABC_LICENSE_TYPE_FALLBACK.get(clean(license_type).lower())


def read_abc_retail_rows(
    path: Path,
    *,
    audit: Counter[str] | None = None,
) -> list[dict[str, Any]]:
    """Read active on-premises licenses from an NJ ABC retail report."""

    try:
        import openpyxl
    except ImportError as error:  # pragma: no cover
        raise RuntimeError("openpyxl is required to read the NJ ABC retail workbook") from error

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        matching_headers: list[tuple[Any, int, list[str]]] = []
        for sheet in workbook.worksheets:
            for row_number, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                candidate = [clean(value).lower() for value in values]
                if ABC_REQUIRED_COLUMNS.issubset(set(candidate)):
                    matching_headers.append((sheet, row_number, candidate))

        if len(matching_headers) != 1:
            locations = [
                f"{sheet.title}!{row_number}"
                for sheet, row_number, _ in matching_headers
            ]
            raise ValueError(
                f"NJ ABC workbook {path} must contain exactly one retail-license header; "
                f"found {len(matching_headers)} matching headers: {locations}"
            )

        sheet, header_row, headers = matching_headers[0]
        if sheet.sheet_state != "visible":
            raise ValueError(
                f"NJ ABC workbook {path} retail-license header {sheet.title}!{header_row} "
                f"is on a {sheet.sheet_state} sheet; the source sheet must be visible"
            )
        duplicate_required_columns = sorted(
            column for column in ABC_REQUIRED_COLUMNS if headers.count(column) != 1
        )
        if duplicate_required_columns:
            raise ValueError(
                f"NJ ABC workbook {path} retail-license header {sheet.title}!{header_row} "
                f"must contain each required column exactly once; invalid columns: "
                f"{duplicate_required_columns}"
            )
        eligible: list[dict[str, Any]] = []
        for values in sheet.iter_rows(min_row=header_row + 1, values_only=True):
            row = dict(zip(headers, values))
            if audit is not None:
                audit["workbookRows"] += 1
            license_number = clean(row.get("license number"))
            license_type = clean(row.get("license type"))
            parts = license_number.split("-")
            parsed_code = parts[1] if len(parts) >= 2 and re.fullmatch(r"\d{2}", parts[1]) else None
            license_code = abc_license_code(license_number, license_type)
            if audit is not None and parsed_code is None and license_code is not None:
                audit["licenseTypeFallbacks"] += 1
            if audit is not None and parsed_code is None and license_code is None:
                audit["unrecognizedLicenseRows"] += 1
            address = clean(row.get("premise address"))
            city = clean(row.get("city"))
            if license_code not in ABC_ON_PREMISE_RETAIL_CODES:
                if audit is not None:
                    audit["excludedNonOnPremises"] += 1
                continue
            if clean(row.get("inactivity start date")):
                if audit is not None:
                    audit["excludedInactiveOnPremises"] += 1
                continue
            if audit is not None:
                audit["eligibleActiveOnPremises"] += 1
                if not address:
                    audit["missingPremiseAddress"] += 1
                if not city:
                    audit["missingCityField"] += 1
            row["license number"] = license_number
            row["license code"] = license_code
            row["license type"] = license_type
            row["premise address"] = address
            row["city"] = city
            eligible.append(row)
        return eligible
    finally:
        workbook.close()


def abc_geocode_key(row: dict[str, Any]) -> str:
    return "|".join((
        clean(row.get("license number")).upper(),
        clean(row.get("premise address")).upper(),
        clean(row.get("city")).upper(),
    ))


def abc_geocoder_components(
    row: dict[str, Any],
    known_cities: Iterable[str] = (),
) -> tuple[str, str, str, str]:
    """Separate postal suffixes without changing the stable source/cache key."""

    address = clean(row.get("premise address"))
    city = clean(row.get("city"))
    postal_match = re.match(
        r"^(.*?)(?:,\s*)?(?:NJ|NEW JERSEY)\s+(\d{5}(?:-\d{4})?)\s*$",
        address,
        flags=re.IGNORECASE,
    )
    if postal_match is None:
        return address, city, "NJ", ""

    body, zip_code = clean(postal_match.group(1)).rstrip(","), postal_match.group(2)
    city_candidates = {
        clean(candidate) for candidate in (*known_cities, *ABC_POSTAL_CITY_ALIASES)
        if clean(candidate)
    }
    city_candidates.update(
        re.sub(r"\bTWP\b", "TOWNSHIP", candidate, flags=re.IGNORECASE)
        for candidate in tuple(city_candidates)
        if re.search(r"\bTWP\b", candidate, flags=re.IGNORECASE)
    )
    if city:
        city_candidates.add(city)
    for candidate in sorted(city_candidates, key=len, reverse=True):
        suffix = candidate.upper()
        body_upper = body.upper()
        if body_upper == suffix:
            return "", candidate, "NJ", zip_code
        if body_upper.endswith(" " + suffix):
            street = clean(body[:-(len(candidate) + 1)]).rstrip(",")
            return street, candidate, "NJ", zip_code
    return body, city, "NJ", zip_code


def abc_expected_county(row: dict[str, Any]) -> str | None:
    license_number = clean(row.get("license number"))
    prefix = license_number.split("-", 1)[0][:2]
    return ABC_COUNTY_BY_PREFIX.get(prefix)


def leading_address_numbers(value: Any) -> set[int]:
    """Return plausible leading house numbers, including multi-premise forms."""

    address = re.sub(r"\s+AND\s+", " & ", clean(value), flags=re.IGNORECASE)
    prefix_match = re.match(r"^\s*([0-9][0-9\s,&/-]*?)(?=\s+[A-Za-z])", address)
    number = r"\d+[A-Z]?(?:\s*-\s*\d+[A-Z]?)?"
    separator = r"(?:\s*(?:,|&|/)\s*|\s+AND\s+|\s+)"
    if prefix_match is None:
        prefix_match = re.match(
            rf"^\s*({number}(?:{separator}{number})*)\b",
            clean(value),
            flags=re.IGNORECASE,
        )
    if prefix_match is None:
        return set()
    prefix = prefix_match.group(1)
    numbers: set[int] = set()
    chain_pattern = r"\d+(?:\s*-\s*\d+){2,}"
    for chain_match in re.finditer(chain_pattern, prefix):
        numbers.update(int(number) for number in re.findall(r"\d+", chain_match.group(0)))
    prefix_without_chains = re.sub(chain_pattern, " ", prefix)
    for range_match in re.finditer(r"(\d+)\s*-\s*(\d+)", prefix_without_chains):
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if start <= end and end - start <= 100:
            numbers.update(range(start, end + 1))
        else:
            numbers.update((start, end))
    prefix_without_ranges = re.sub(r"\d+\s*-\s*\d+", " ", prefix_without_chains)
    numbers.update(int(number) for number in re.findall(r"\d+", prefix_without_ranges))
    return numbers


def abc_geocode_quality_rejection(
    row: dict[str, Any],
    match: dict[str, Any],
) -> str | None:
    provider = clean(match.get("provider"))
    provider_lower = provider.lower()
    match_type = clean(match.get("matchType"))
    if "census" in provider_lower:
        match["provider"] = "US Census Bureau"
        if match_type.lower() != "exact":
            return "censusMatchNotExact"
    elif "nj_geocode" in provider_lower or "nj office of gis" in provider_lower:
        try:
            score = float(match.get("score"))
        except (TypeError, ValueError):
            return "njginMissingScore"
        if clean(match.get("status")).upper() != "M":
            return "njginStatusNotMatched"
        if score < 95:
            return "njginScoreBelow95"
        if match_type not in {"PointAddress", "Subaddress", "StreetAddress"}:
            return "njginMatchTypeNotAccepted"
        if clean(match.get("region")).upper() != "NJ":
            return "njginRegionNotNJ"
    else:
        return "unreviewedGeocodeProvider"

    input_numbers = leading_address_numbers(row.get("premise address"))
    matched_numbers = leading_address_numbers(match.get("address"))
    if input_numbers and matched_numbers and input_numbers.isdisjoint(matched_numbers):
        return "addressNumberMismatch"
    return None


def load_geocode_cache(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text())
    cached = payload.get("matches", payload)
    default_provider = clean(payload.get("source")) if isinstance(payload, dict) else ""
    matches: dict[str, dict[str, Any]] = {}
    for key, item in cached.items():
        try:
            lat, lon, address = geocode_components(item)
        except (KeyError, TypeError, ValueError):
            continue
        if valid_coordinate(lat, lon):
            metadata = dict(item) if isinstance(item, dict) else {}
            metadata.pop("latitude", None)
            metadata.pop("longitude", None)
            metadata.pop("address", None)
            if default_provider and not clean(metadata.get("provider")):
                metadata["provider"] = default_provider
            matches[key] = geocode_match(lat, lon, address, **metadata)
    return matches


def save_geocode_cache(
    path: Path,
    matches: dict[str, dict[str, Any]],
    *,
    workbook_path: Path | None = None,
    addressable_license_count: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized_matches: dict[str, dict[str, Any]] = {}
    providers: set[str] = set()
    for key, raw_match in sorted(matches.items()):
        lat, lon, address = geocode_components(raw_match)
        item = dict(raw_match) if isinstance(raw_match, dict) else {}
        item.update({
            "latitude": round(lat, 7),
            "longitude": round(lon, 7),
            "address": address,
        })
        provider = clean(item.get("provider"))
        if provider:
            providers.add(provider)
        serialized_matches[key] = item
    payload = {
        "version": 2,
        "reportAsOf": ABC_REPORT_AS_OF,
        "providers": sorted(providers),
        "acceptedGeocodeCount": len(serialized_matches),
        "matches": serialized_matches,
    }
    if workbook_path is not None:
        payload["workbookSHA256"] = file_sha256(workbook_path)
    if addressable_license_count is not None:
        payload["addressableLicenseCount"] = addressable_license_count
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    temporary_path.replace(path)


def validate_reviewed_geocode_cache(
    path: Path,
    workbook_path: Path,
    addressable_rows: Iterable[dict[str, Any]],
) -> None:
    payload = json.loads(path.read_text())
    rows = list(addressable_rows)
    matches = payload.get("matches")
    problems: list[str] = []
    if payload.get("version") != 2:
        problems.append("version is not 2")
    if payload.get("reportAsOf") not in (None, ABC_REPORT_AS_OF):
        problems.append("reportAsOf conflicts with the reviewed workbook")
    if payload.get("sourceWorkbook") not in (None, ABC_REPORT_FILENAME):
        problems.append("source workbook filename does not match")
    if payload.get("workbookSHA256") != file_sha256(workbook_path):
        problems.append("workbook SHA-256 does not match")
    cache_summary = payload.get("summary") or {}
    addressable_count = payload.get("addressableLicenseCount", cache_summary.get("addressable"))
    if addressable_count != len(rows):
        problems.append("addressable license count does not match")
    if not isinstance(matches, dict):
        problems.append("matches is not an object")
        matches = {}
    accepted_count = payload.get("acceptedGeocodeCount", cache_summary.get("accepted"))
    if accepted_count != len(matches):
        problems.append("accepted geocode count does not match matches")
    providers = set(payload.get("providers") or [])
    if not providers or not providers.issubset(ABC_ALLOWED_GEOCODE_PROVIDERS):
        problems.append(f"unreviewed provider list: {sorted(providers)}")
    entry_providers = {
        clean(match.get("provider"))
        for match in matches.values()
        if isinstance(match, dict)
    }
    if entry_providers != providers:
        problems.append(
            f"provider list does not match cache entries: {sorted(entry_providers)}"
        )
    row_keys = {abc_geocode_key(row) for row in rows}
    unexpected_keys = set(matches) - row_keys
    if unexpected_keys:
        problems.append(f"{len(unexpected_keys)} cache keys are outside the workbook scope")
    if ABC_REVIEWED_GEOCODE_COUNT and len(matches) != ABC_REVIEWED_GEOCODE_COUNT:
        problems.append(
            f"expected {ABC_REVIEWED_GEOCODE_COUNT} accepted geocodes, found {len(matches)}"
        )
    if ABC_REVIEWED_GEOCODE_CACHE_SHA256:
        actual_sha256 = file_sha256(path)
        if actual_sha256 != ABC_REVIEWED_GEOCODE_CACHE_SHA256:
            problems.append("reviewed cache SHA-256 does not match")
    if not ABC_GEOCODE_REVIEW.is_file():
        problems.append("reviewed geocode rejection artifact is missing")
    elif file_sha256(ABC_GEOCODE_REVIEW) != ABC_GEOCODE_REVIEW_SHA256:
        problems.append("reviewed geocode rejection artifact SHA-256 does not match")
    if problems:
        raise RuntimeError("Invalid reviewed NJ ABC geocode cache: " + "; ".join(problems))


def validate_abc_geocode_matches(
    rows: Iterable[dict[str, Any]],
    matches: dict[str, dict[str, Any]],
    county_lookup: CountyLookup,
) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, dict[str, Any]]]]:
    """Reject pins outside the county encoded by an official license number."""

    accepted: dict[str, dict[str, Any]] = {}
    rejected: dict[str, tuple[str, dict[str, Any]]] = {}
    for row in rows:
        key = abc_geocode_key(row)
        raw_match = matches.get(key)
        if raw_match is None:
            continue
        try:
            lat, lon, address = geocode_components(raw_match)
        except (KeyError, TypeError, ValueError):
            rejected[key] = ("invalidGeocode", dict(raw_match))
            continue
        match = dict(raw_match)
        expected_county = abc_expected_county(row)
        coordinate_county = county_lookup.find(lat, lon)
        provider_county_text = clean(match.get("county"))
        provider_county = county_name(provider_county_text) if provider_county_text else None
        rejection_match = {
            **match,
            "expectedCounty": expected_county,
            "coordinateCounty": coordinate_county,
        }
        if expected_county is None:
            rejected[key] = ("unrecognizedLicenseCountyPrefix", rejection_match)
        elif coordinate_county is None:
            rejected[key] = ("coordinateOutsideKnownNJCounty", rejection_match)
        elif coordinate_county != expected_county:
            rejected[key] = ("coordinateCountyMismatch", rejection_match)
        elif provider_county_text and provider_county is None:
            rejected[key] = ("unrecognizedProviderCounty", rejection_match)
        elif provider_county is not None and provider_county != expected_county:
            rejected[key] = ("providerCountyMismatch", rejection_match)
        else:
            quality_rejection = abc_geocode_quality_rejection(row, match)
            if quality_rejection is not None:
                rejected[key] = (quality_rejection, rejection_match)
                continue
            match = geocode_match(lat, lon, address, **{
                metadata_key: metadata_value
                for metadata_key, metadata_value in match.items()
                if metadata_key not in {"latitude", "longitude", "address", "county"}
            })
            match["county"] = coordinate_county
            accepted[key] = match
    return accepted, rejected


def formatted_date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return clean(value)


def abc_review_row(
    row: dict[str, Any],
    reason: str,
    match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    review: dict[str, Any] = {
        "reason": reason,
        "licenseNumber": clean(row.get("license number")),
        "licenseCode": clean(row.get("license code")),
        "licenseType": clean(row.get("license type")),
        "establishment": clean(row.get("establishment")),
        "licensee": clean(row.get("licensee")),
        "effectiveDate": formatted_date(row.get("effective date")),
        "city": clean(row.get("city")),
        "premiseAddress": clean(row.get("premise address")),
    }
    if match is not None:
        try:
            lat, lon, matched_address = geocode_components(match)
        except (KeyError, TypeError, ValueError):
            pass
        else:
            review["rejectedGeocode"] = {
                "latitude": lat,
                "longitude": lon,
                "address": matched_address,
                **{
                    key: value for key, value in match.items()
                    if key not in {"latitude", "longitude", "address"}
                },
            }
    return review


def write_abc_unmatched_report(
    path: Path,
    workbook_path: Path,
    rows: list[dict[str, Any]],
    matches: dict[str, dict[str, Any]],
    audit: Counter[str],
    rejected: dict[str, tuple[str, dict[str, Any]]] | None = None,
    geocode_cache_path: Path | None = None,
    geocode_review_path: Path | None = None,
) -> None:
    rejected = rejected or {}
    unresolved: list[dict[str, Any]] = []
    for row in rows:
        key = abc_geocode_key(row)
        if clean(row.get("premise address")) and key in matches:
            continue
        if not clean(row.get("premise address")):
            reason, rejected_match = "missingPremiseAddress", None
        elif key in rejected:
            reason, rejected_match = rejected[key]
        else:
            reason, rejected_match = "noAcceptedGeocode", None
        unresolved.append(abc_review_row(row, reason, rejected_match))
    matched_count = sum(
        bool(clean(row.get("premise address"))) and abc_geocode_key(row) in matches
        for row in rows
    )
    providers = Counter(
        (
            clean(matches[abc_geocode_key(row)].get("provider"))
            if isinstance(matches[abc_geocode_key(row)], dict)
            else ""
        ) or "Unknown"
        for row in rows
        if clean(row.get("premise address")) and abc_geocode_key(row) in matches
    )
    rejection_reasons = Counter(reason for reason, _ in rejected.values())
    report_audit = dict(sorted(audit.items()))
    report_audit.update({
        "addressableActiveOnPremises": sum(bool(clean(row.get("premise address"))) for row in rows),
        "geocoded": matched_count,
        "materialized": matched_count,
        "geocodedByProvider": dict(sorted(providers.items())),
        "rejectedGeocodesByReason": dict(sorted(rejection_reasons.items())),
        "unresolved": len(unresolved),
    })
    payload = {
        "version": 2,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sourceWorkbook": workbook_path.name,
        "sourcePage": ABC_SOURCE_PAGE,
        "sourceReportURL": ABC_RETAIL_URL,
        "reportAsOf": ABC_REPORT_AS_OF,
        "workbookSHA256": file_sha256(workbook_path),
        "audit": report_audit,
        "unresolvedLicenses": sorted(
            unresolved,
            key=lambda item: (item["reason"], item["licenseNumber"], item["establishment"]),
        ),
    }
    if geocode_cache_path is not None and geocode_cache_path.is_file():
        payload["geocodeCache"] = geocode_cache_path.name
        payload["geocodeCacheSHA256"] = file_sha256(geocode_cache_path)
    if geocode_review_path is not None and geocode_review_path.is_file():
        payload["geocodeReview"] = geocode_review_path.name
        payload["geocodeReviewSHA256"] = file_sha256(geocode_review_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def abc_places_from_rows(
    rows: Iterable[dict[str, Any]],
    matches: dict[str, dict[str, Any]],
    county_lookup: CountyLookup,
) -> list[dict[str, Any]]:
    placeholders = {"", "NA", "N/A", "NONE", "NOT AVAILABLE", "ABC POCKET"}
    places: list[dict[str, Any]] = []
    for row in rows:
        match = matches.get(abc_geocode_key(row))
        if not match:
            continue
        lat, lon, matched_address = geocode_components(match)
        county = county_lookup.find(lat, lon)
        expected_county = abc_expected_county(row)
        if not county or county != expected_county:
            raise RuntimeError(
                f"Unvalidated NJ ABC geocode reached materialization for "
                f"{clean(row.get('license number'))}: expected {expected_county}, found {county}"
            )
        establishment = clean(row.get("establishment"))
        licensee = clean(row.get("licensee"))
        name = display_name(
            establishment if establishment.upper() not in placeholders else licensee
        )
        if not name:
            street_label = display_name(clean(row.get("premise address")).split(",", 1)[0])
            name = f"Licensed Alcohol-Serving Premises — {street_label}"
        license_type = clean(row.get("license type"))
        notes = (
            f"Listed in the NJ ABC {ABC_REPORT_LABEL} report as an active {license_type}; "
            "verify current operation and on-premises alcohol service."
        )
        license_code = clean(row.get("license code"))
        if license_code == "34":
            notes += " Seasonal license term: May 1 through November 14."
        elif license_code == "35":
            notes += " Seasonal license term: November 15 through April 30."
        places.append(make_place(
            name=name,
            place_type="Alcohol-Serving Location",
            latitude=lat,
            longitude=lon,
            county=county,
            source=ABC_SOURCE,
            source_id=clean(row.get("license number")) or abc_geocode_key(row),
            address=matched_address or (
                f"{clean(row.get('premise address'))}, {clean(row.get('city'))}, NJ"
            ),
            confidence="high",
            notes=notes,
            priority=120,
            stable_source_id=True,
        ))
    return places


def load_abc_retail(
    path: Path,
    county_lookup: CountyLookup,
    *,
    geocode_cache_path: Path | None = None,
    unmatched_report_path: Path | None = None,
    refresh_geocodes: bool = False,
    require_reviewed_cache: bool = False,
    geocode_review_path: Path | None = None,
) -> list[dict[str, Any]]:
    audit: Counter[str] = Counter()
    rows = read_abc_retail_rows(path, audit=audit)
    addressable_rows = [row for row in rows if clean(row.get("premise address"))]
    known_cities = {clean(row.get("city")) for row in rows if clean(row.get("city"))}
    geocoder_components = {
        abc_geocode_key(row): abc_geocoder_components(row, known_cities)
        for row in addressable_rows
    }
    audit["cityDerivedFromPremiseAddress"] = sum(
        not clean(row.get("city")) and bool(geocoder_components[abc_geocode_key(row)][1])
        for row in addressable_rows
    )
    if require_reviewed_cache:
        if geocode_cache_path is None or not geocode_cache_path.is_file():
            raise RuntimeError("Reviewed NJ ABC geocode cache is required")
        validate_reviewed_geocode_cache(
            geocode_cache_path,
            path,
            addressable_rows,
        )
    cached_matches = load_geocode_cache(geocode_cache_path)
    matches, rejected = validate_abc_geocode_matches(
        addressable_rows,
        cached_matches,
        county_lookup,
    )
    if require_reviewed_cache and rejected:
        reasons = Counter(reason for reason, _ in rejected.values())
        raise RuntimeError(
            f"Reviewed NJ ABC cache contains {len(rejected)} rejected matches: "
            f"{dict(sorted(reasons.items()))}"
        )
    missing_rows = [row for row in addressable_rows if abc_geocode_key(row) not in matches]
    if missing_rows and refresh_geocodes:
        addresses = [
            (
                abc_geocode_key(row),
                *geocoder_components[abc_geocode_key(row)],
            )
            for row in missing_rows
        ]
        census_matches = census_batch_geocode(addresses, label="NJ ABC retail premises")
        accepted_census, rejected_census = validate_abc_geocode_matches(
            missing_rows,
            census_matches,
            county_lookup,
        )
        matches.update(accepted_census)
        rejected.update(rejected_census)
    rejected = {key: value for key, value in rejected.items() if key not in matches}
    if geocode_cache_path is not None and refresh_geocodes:
        save_geocode_cache(
            geocode_cache_path,
            matches,
            workbook_path=path,
            addressable_license_count=len(addressable_rows),
        )
    if unmatched_report_path is not None:
        write_abc_unmatched_report(
            unmatched_report_path,
            path,
            rows,
            matches,
            audit,
            rejected,
            geocode_cache_path,
            geocode_review_path,
        )
    matched_count = sum(abc_geocode_key(row) in matches for row in addressable_rows)
    providers = Counter(
        clean(matches[abc_geocode_key(row)].get("provider")) or "Unknown"
        for row in addressable_rows
        if abc_geocode_key(row) in matches
    )
    print(
        f"NJ ABC audit: {audit['eligibleActiveOnPremises']:,} eligible active; "
        f"{len(addressable_rows):,} addressable; {matched_count:,} geocoded "
        f"({dict(sorted(providers.items()))}); "
        f"{len(addressable_rows) - matched_count:,} addressable unmatched; "
        f"{len(rejected):,} rejected geocodes; "
        f"{audit['missingPremiseAddress']:,} missing an address",
        file=sys.stderr,
    )
    return abc_places_from_rows(addressable_rows, matches, county_lookup)


def load_dcf_child_care() -> list[dict[str, Any]]:
    rows = fetch_json(DCF_CHILD_CARE_URL)
    matches = geocode_dcf(rows)
    places: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        match = matches.get(str(index))
        county = county_name(row.get("county"))
        if not match or not county:
            continue
        lat, lon, matched_address = geocode_components(match)
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


def load_v2_baseline() -> list[dict[str, Any]]:
    """Load the currently published v2 files for a targeted source augmentation."""

    places: list[dict[str, Any]] = []
    for county in COUNTIES:
        path = ROOT / "data" / "v2" / f"{county.lower().replace(' ', '_')}.json"
        for raw_place in json.loads(path.read_text()):
            place = dict(raw_place)
            place["county"] = county
            places.append(place)
    return places


def normalized_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", " ", name.lower())
    name = re.sub(r"\b(the|inc|llc|corp|corporation|facility|center|centre|campus)\b", " ", name)
    return re.sub(r"[^a-z0-9]+", "", name)


def normalized_street_address(address: str) -> str:
    street = clean(address).split(",", 1)[0].lower()
    if not re.search(r"\d", street):
        return ""
    replacements = {
        "avenue": "ave", "street": "st", "road": "rd", "boulevard": "blvd",
        "drive": "dr", "highway": "hwy", "lane": "ln", "place": "pl",
        "parkway": "pkwy", "route": "rt", "turnpike": "tpke",
    }
    for word, abbreviation in replacements.items():
        street = re.sub(rf"\b{word}\b", abbreviation, street)
    street = re.sub(r"\b(suite|ste|unit|floor|fl)\s*[a-z0-9-]+\b", " ", street)
    return re.sub(r"[^a-z0-9]+", "", street)


def distance_meters(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1, lon1 = math.radians(a["latitude"]), math.radians(a["longitude"])
    lat2, lon2 = math.radians(b["latitude"]), math.radians(b["longitude"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(value))


def duplicate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if TYPE_GROUP.get(a["type"], a["type"]) != TYPE_GROUP.get(b["type"], b["type"]):
        return False
    a_source = clean(a.get("source"))
    b_source = clean(b.get("source"))
    if a_source == ABC_SOURCE and b_source == ABC_SOURCE:
        # One address can legitimately host several separately licensed
        # restaurants, hotel outlets or club premises. Only collapse the exact
        # same stable license record, never two different ABC licenses.
        return a["id"] == b["id"]
    distance = distance_meters(a, b)
    contains_abc = ABC_SOURCE in {a_source, b_source}
    if distance <= 18 and not contains_abc:
        return True
    a_name, b_name = normalized_name(a["name"]), normalized_name(b["name"])
    if not a_name or not b_name or a_name.startswith("unnamed") or b_name.startswith("unnamed"):
        return False
    if a_name == b_name and distance <= 500:
        return True
    shorter, longer = sorted((a_name, b_name), key=len)
    names_compatible = (
        len(shorter) >= 5
        and shorter in longer
        and len(shorter) / len(longer) >= 0.55
    )
    if not names_compatible:
        return False
    a_address = normalized_street_address(a.get("address", ""))
    b_address = normalized_street_address(b.get("address", ""))
    same_address = bool(a_address and a_address == b_address)
    return distance <= 100 or (a_source != b_source and same_address and distance <= 250)


def dedupe(places: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        places,
        key=lambda item: (-int(item.get("_priority", 0)), item["county"], item["type"], item["name"], item["id"]),
    )
    kept: list[dict[str, Any]] = []
    grid: dict[tuple[str, str, int, int], list[int]] = defaultdict(list)
    for place in ordered:
        group = TYPE_GROUP.get(place["type"], place["type"])
        x = int(place["latitude"] / DEDUPE_GRID_SIZE_DEGREES)
        y = int(place["longitude"] / DEDUPE_GRID_SIZE_DEGREES)
        candidates: list[int] = []
        neighbor_offsets = range(-DEDUPE_GRID_NEIGHBOR_RADIUS, DEDUPE_GRID_NEIGHBOR_RADIUS + 1)
        for dx in neighbor_offsets:
            for dy in neighbor_offsets:
                candidates.extend(grid[(place["county"], group, x + dx, y + dy)])
        if any(duplicate(place, kept[index]) for index in candidates):
            continue
        grid[(place["county"], group, x, y)].append(len(kept))
        kept.append(place)
    return kept


def augment_v2_baseline_with_abc(
    baseline: Iterable[dict[str, Any]],
    abc_places: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Layer official ABC rows onto v2 without refreshing unrelated sources.

    Existing official ABC rows are replaced on repeat runs. An OpenStreetMap
    alcohol marker is removed only when the normal name/address-aware duplicate
    check identifies an official ABC marker for the same establishment. All
    non-alcohol baseline records pass through unchanged.
    """

    official = list(abc_places)
    official_ids = [place["id"] for place in official]
    if len(official_ids) != len(set(official_ids)):
        raise RuntimeError("NJ ABC materialization produced duplicate stable license IDs")

    official_by_county: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for place in official:
        official_by_county[place["county"]].append(place)

    kept: list[dict[str, Any]] = []
    replaced_osm = 0
    for place in baseline:
        if clean(place.get("source")) == ABC_SOURCE:
            continue
        is_osm_alcohol = (
            place.get("type") == "Alcohol-Serving Location"
            and clean(place.get("source")) == "OpenStreetMap contributors"
        )
        if is_osm_alcohol and any(
            duplicate(abc_place, place)
            for abc_place in official_by_county.get(clean(place.get("county")), [])
        ):
            replaced_osm += 1
            continue
        kept.append(place)
    kept.extend(official)
    return kept, replaced_osm


def validate_abc_regression_markers(places: Iterable[dict[str, Any]]) -> None:
    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for place in places:
        indexed[place["id"]].append(place)
    for license_number, (expected_name, expected_county) in ABC_REGRESSION_LICENSES.items():
        expected_id = deterministic_source_id(
            ABC_SOURCE,
            license_number,
            "Alcohol-Serving Location",
        )
        matches = indexed.get(expected_id, [])
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one generated NJ ABC marker for license {license_number}; "
                f"found {len(matches)}"
            )
        marker = matches[0]
        if marker["name"] != expected_name or marker["county"] != expected_county:
            raise RuntimeError(
                f"NJ ABC regression marker {license_number} changed unexpectedly: "
                f"{marker['name']} in {marker['county']}"
            )


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


def resolve_abc_artifact_paths(
    cache_path: Path | None,
    unmatched_report_path: Path | None,
    *,
    refresh_geocodes: bool,
    source_workbook_path: Path | None = None,
) -> tuple[Path, Path, Path | None]:
    """Keep exploratory refresh output separate from pinned release artifacts."""

    if refresh_geocodes:
        if cache_path is None or unmatched_report_path is None:
            raise ValueError(
                "--refresh-abc-geocodes requires explicit --abc-geocode-cache and "
                "--abc-unmatched-report output paths"
            )
        protected_paths = {
            "pinned release geocode cache": ABC_REVIEWED_GEOCODE_CACHE,
            "pinned geocode review": ABC_GEOCODE_REVIEW,
            "published unmatched report": ABC_UNMATCHED_REPORT,
        }
        outputs = {
            "--abc-geocode-cache": cache_path,
            "--abc-unmatched-report": unmatched_report_path,
        }

        def same_target(first: Path, second: Path) -> bool:
            if first.resolve() == second.resolve():
                return True
            try:
                return first.exists() and second.exists() and first.samefile(second)
            except OSError:
                return False

        for option, output_path in outputs.items():
            for protected_label, protected_path in protected_paths.items():
                if same_target(output_path, protected_path):
                    raise ValueError(
                        f"{option} cannot target the {protected_label} during a refresh"
                    )
            resolved_output = output_path.resolve()
            resolved_root = ROOT.resolve()
            if resolved_output == resolved_root or resolved_root in resolved_output.parents:
                raise ValueError(
                    f"{option} must be a staging path outside the repository during a refresh"
                )
            if source_workbook_path is not None and same_target(
                output_path, source_workbook_path
            ):
                raise ValueError(
                    f"{option} cannot overwrite the NJ ABC source workbook"
                )
        if same_target(cache_path, unmatched_report_path):
            raise ValueError(
                "--abc-geocode-cache and --abc-unmatched-report must be different paths"
            )
        # A new refresh has no reviewed NJGIN rejection artifact yet. Do not
        # attach the pinned release review to its cache/report provenance.
        return cache_path, unmatched_report_path, None
    return (
        cache_path or ABC_REVIEWED_GEOCODE_CACHE,
        unmatched_report_path or ABC_UNMATCHED_REPORT,
        ABC_GEOCODE_REVIEW,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--osm-file", type=Path, help="Existing Overpass JSON; downloaded when omitted")
    parser.add_argument("--ltc-file", type=Path, help="Existing NJ DOH All_LTC.xlsx; downloaded when omitted")
    parser.add_argument(
        "--abc-retail-file",
        type=Path,
        help="Reviewed NJ ABC active retail-license workbook (required unless --skip-abc)",
    )
    parser.add_argument(
        "--abc-geocode-cache",
        type=Path,
        help="Provider-aware JSON cache (defaults to the pinned release cache)",
    )
    parser.add_argument(
        "--abc-unmatched-report",
        type=Path,
        help="Unmatched-license report (defaults to the published release report)",
    )
    parser.add_argument(
        "--abc-only",
        action="store_true",
        help="Augment current v2 files with ABC rows without refreshing unrelated sources",
    )
    parser.add_argument(
        "--refresh-abc-geocodes",
        action="store_true",
        help=(
            "Geocode cache misses into an explicit staging cache/report pair without "
            "changing release datasets"
        ),
    )
    parser.add_argument("--skip-abc", action="store_true", help="Skip NJ ABC retail-license ingestion")
    parser.add_argument("--skip-child-care", action="store_true", help="Skip DCF/Census batch geocoding")
    args = parser.parse_args()

    if args.abc_only and args.skip_abc:
        parser.error("--abc-only cannot be combined with --skip-abc")
    if args.refresh_abc_geocodes and args.skip_abc:
        parser.error("--refresh-abc-geocodes cannot be combined with --skip-abc")

    try:
        abc_geocode_cache_path, abc_unmatched_report_path, abc_geocode_review_path = (
            resolve_abc_artifact_paths(
                args.abc_geocode_cache,
                args.abc_unmatched_report,
                refresh_geocodes=args.refresh_abc_geocodes,
                source_workbook_path=args.abc_retail_file,
            )
        )
    except ValueError as error:
        parser.error(str(error))

    if not args.skip_abc and args.abc_retail_file is None:
        parser.error(
            "--abc-retail-file is required for a reliable build; download the current "
            "Retail Licensee Listing from the NJ ABC Licensing Reports page"
        )

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    county_lookup = CountyLookup(fetch_json(COUNTY_GEOJSON_URL))

    abc_path = args.abc_retail_file
    abc_workbook_sha256: str | None = None
    if not args.skip_abc:
        if not abc_path.is_file():
            parser.error(f"NJ ABC workbook not found: {abc_path}")
        abc_workbook_sha256 = file_sha256(abc_path)
        if abc_workbook_sha256 != ABC_REPORT_SHA256:
            raise RuntimeError(
                "NJ ABC workbook SHA-256 does not match the reviewed "
                f"{ABC_REPORT_LABEL} report; update the report URL/date/hash after review"
            )
        if not abc_geocode_cache_path.is_file() and not args.refresh_abc_geocodes:
            parser.error(
                f"reviewed NJ ABC geocode cache not found: {abc_geocode_cache_path}; "
                "restore the tracked cache or pass --refresh-abc-geocodes explicitly"
            )

    if args.refresh_abc_geocodes:
        abc_places = load_abc_retail(
            abc_path,
            county_lookup,
            geocode_cache_path=abc_geocode_cache_path,
            unmatched_report_path=abc_unmatched_report_path,
            refresh_geocodes=True,
            require_reviewed_cache=False,
            geocode_review_path=None,
        )
        print(
            f"Exploratory NJ ABC refresh staged {len(abc_places):,} accepted places; "
            "release datasets were not changed.",
            file=sys.stderr,
        )
        return 0

    if args.abc_only:
        baseline = load_v2_baseline()
        abc_places = load_abc_retail(
            abc_path,
            county_lookup,
            geocode_cache_path=abc_geocode_cache_path,
            unmatched_report_path=abc_unmatched_report_path,
            refresh_geocodes=args.refresh_abc_geocodes,
            require_reviewed_cache=not args.refresh_abc_geocodes,
            geocode_review_path=abc_geocode_review_path,
        )
        merged, replaced_osm = augment_v2_baseline_with_abc(baseline, abc_places)
        print(f"v2 baseline: {len(baseline):,}", file=sys.stderr)
        print(f"NJ ABC active on-premises retail licenses: {len(abc_places):,}", file=sys.stderr)
        print(f"Replaced matching OpenStreetMap alcohol markers: {replaced_osm:,}", file=sys.stderr)
    else:
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
        if not args.skip_abc:
            sources.append((
                "NJ ABC active on-premises retail licenses",
                load_abc_retail(
                    abc_path,
                    county_lookup,
                    geocode_cache_path=abc_geocode_cache_path,
                    unmatched_report_path=abc_unmatched_report_path,
                    refresh_geocodes=args.refresh_abc_geocodes,
                    require_reviewed_cache=not args.refresh_abc_geocodes,
                    geocode_review_path=abc_geocode_review_path,
                ),
            ))
        if not args.skip_child_care:
            sources.append(("NJ DCF child care", load_dcf_child_care()))

        for label, items in sources:
            print(f"{label}: {len(items):,}", file=sys.stderr)
        merged = dedupe(item for _, items in sources for item in items)
    if not args.skip_abc:
        validate_abc_regression_markers(merged)
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
        "version": DATASET_REVISION,
        "lastUpdated": generated_at,
        "state": "NJ",
        "counties": [
            {
                "county": entry["county"],
                "version": DATASET_REVISION,
                "lastUpdated": generated_at,
                "adjacentCounties": entry["adjacentCounties"],
                "datasetURL": (
                    "https://mattymigs.github.io/carryaware-data/data/v2/"
                    f"{entry['county'].lower().replace(' ', '_')}.json"
                    f"?v={DATASET_REVISION}"
                ),
            }
            for entry in legacy_manifest["counties"]
        ],
    }
    (ROOT / "nj_county_manifest_v2.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )

    counts = Counter(place["type"] for place in merged)
    source_counts = Counter(clean(place.get("source")) or "Unknown" for place in merged)
    summary = {
        "version": DATASET_REVISION,
        "generatedAt": generated_at,
        "totalPlaces": len(merged),
        "countsByType": dict(sorted(counts.items())),
        "countsBySource": dict(sorted(source_counts.items())),
        "countsByCounty": {county: len(by_county[county]) for county in COUNTIES},
    }
    if abc_workbook_sha256 is not None:
        summary["sourceProvenance"] = {
            "njABC": {
                "sourcePage": ABC_SOURCE_PAGE,
                "sourceReportURL": ABC_RETAIL_URL,
                "reportAsOf": ABC_REPORT_AS_OF,
                "workbookSHA256": abc_workbook_sha256,
            }
        }
        if abc_geocode_cache_path.is_file():
            summary["sourceProvenance"]["njABC"].update({
                "geocodeCache": abc_geocode_cache_path.name,
                "geocodeCacheSHA256": file_sha256(abc_geocode_cache_path),
            })
        if abc_geocode_review_path is not None and abc_geocode_review_path.is_file():
            summary["sourceProvenance"]["njABC"].update({
                "geocodeReview": abc_geocode_review_path.name,
                "geocodeReviewSHA256": file_sha256(abc_geocode_review_path),
            })
    (ROOT / "nj_dataset_summary_v2.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(f"v2 total after deduplication: {len(merged):,}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
