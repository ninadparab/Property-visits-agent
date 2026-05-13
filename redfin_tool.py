import requests
import json
import time
import random
import re
from datetime import datetime
from pathlib import Path

from langchain_core.tools import tool

_REGION_MAP_FILE = Path(__file__).parent / "region_map.json"
_region_map: dict[str, str] | None = None


def _load_region_map() -> dict[str, str]:
    global _region_map
    if _region_map is None:
        if _REGION_MAP_FILE.exists():
            _region_map = json.loads(_REGION_MAP_FILE.read_text())
        else:
            _region_map = {}
    return _region_map


UIPT_MAP = {
    "house":     "1",
    "condo":     "2",
    "townhouse": "3",
    "land":      "6",
    "other":     "8",
}


def _validate_region_id(region_id: str, state: str, city: str) -> bool:
    """Return True if the Redfin city URL for this region_id resolves to the expected city."""
    city_slug = city.replace(" ", "-")
    verify_url = f"https://www.redfin.com/city/{region_id}/{state}/{city_slug}"
    try:
        r = requests.get(
            verify_url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=8,
            allow_redirects=True,
        )
        return city_slug.lower() in r.url.lower()
    except Exception:
        return True  # can't reach Redfin — assume valid to avoid breaking offline use


def _lookup_region_id(city: str, state: str) -> str | None:
    key = f"{city}_{state}"
    region_map = _load_region_map()
    cached = region_map.get(key)

    # Validate the cached ID — Redfin occasionally reassigns region IDs
    if cached and _validate_region_id(cached, state, city):
        return cached

    url = "https://www.redfin.com/stingray/do/location-autocomplete"
    params = {"location": f"{city}, {state}", "v": "2"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        text = r.text.lstrip("{}&")
        data = json.loads(text)
        rows = data.get("payload", {}).get("sections", [{}])[0].get("rows", [])
        for row in rows:
            if row.get("type") != "6":
                continue
            if f", {state}" not in row.get("name", ""):
                continue
            raw_id = row.get("id", "")
            region_id = raw_id.split("_")[-1]
            if not region_id.isdigit():
                continue
            # Validate before caching — autocomplete occasionally returns wrong IDs
            if not _validate_region_id(region_id, state, city):
                continue
            # Save the verified ID
            region_map[key] = region_id
            _REGION_MAP_FILE.write_text(json.dumps(region_map, indent=2))
            return region_id
    except Exception as e:
        raise RuntimeError(f"region_id lookup failed for {city}, {state}: {e}") from e
    return None


def _build_listing_url(region_id: str, state: str, city: str, property_types: list[str] | None) -> str:
    """Build a human-readable Redfin filter URL for session warmup."""
    uipt_types = [t for t in (property_types or ["house", "condo", "townhouse"]) if t in UIPT_MAP]
    type_filter = "property-type=" + "+".join(uipt_types) if uipt_types else ""
    parts = [p for p in [type_filter, "open-house-time=this-weekend"] if p]
    city_slug = city.replace(" ", "-")
    base = f"https://www.redfin.com/city/{region_id}/{state}/{city_slug}"
    return f"{base}/filter/{','.join(parts)}" if parts else base


def _ms_to_iso(ms: int | None) -> str | None:
    """Convert Unix milliseconds timestamp to local ISO 8601 datetime string."""
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S")


def _get(obj: dict, *keys, default=None):
    """Safe nested dict access."""
    for k in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(k, default)
        if obj is None:
            return default
    return obj


@tool
def scrape_open_houses(
    city: str,
    state: str,
    property_types: list[str] | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    min_beds: int | None = None,
    max_beds: int | None = None,
    min_baths: float | None = None,
    max_baths: float | None = None,
    min_year_built: int | None = None,
    max_year_built: int | None = None,
    min_sqft: int | None = None,
    max_sqft: int | None = None,
) -> list[dict]:
    """
    Fetch open houses this weekend from Redfin for a given city and state,
    with optional filters for price, beds, baths, year built, and square footage.

    Args:
        city:           City name (e.g. "Bellevue", "San Jose").
        state:          Two-letter state abbreviation (e.g. "WA", "CA").
        property_types: List of property types to include. Valid values:
                        "house", "condo", "townhouse", "land", "other".
                        Defaults to house + condo + townhouse.
        min_price:      Minimum listing price in dollars.
        max_price:      Maximum listing price in dollars.
        min_beds:       Minimum number of bedrooms.
        max_beds:       Maximum number of bedrooms.
        min_baths:      Minimum number of bathrooms.
        max_baths:      Maximum number of bathrooms.
        min_year_built: Earliest year the property was built.
        max_year_built: Latest year the property was built.
        min_sqft:       Minimum square footage.
        max_sqft:       Maximum square footage.

    Returns:
        List of open house dicts, each containing:
        address, city, state, zip, price, beds, baths, sqft, year_built,
        open_house_start, open_house_end, latitude, longitude, url.
        Returns [{"error": "..."}] if the request fails.
    """
    region_id = _lookup_region_id(city, state)
    if region_id is None:
        return [{"error": f"Could not resolve Redfin region ID for {city}, {state}"}]

    uipt_codes = (
        ",".join(UIPT_MAP[t] for t in property_types if t in UIPT_MAP)
        if property_types else "1,2,3"
    )

    listing_url = _build_listing_url(region_id, state, city, property_types)

    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    session = requests.Session()

    # Session warmup — establishes cookies Redfin expects
    try:
        session.get(listing_url, headers={"User-Agent": ua, "Accept": "text/html"}, timeout=15)
        time.sleep(random.uniform(2, 4))
    except Exception:
        pass

    # JSON search endpoint — includes openHouseStart/End as Unix ms timestamps
    json_url = (
        f"https://www.redfin.com/stingray/api/gis"
        f"?al=1&market={state.lower()}"
        f"&region_id={region_id}&region_type=6"
        f"&sf=1,2,3,5,6,7&num_homes=350"
        f"&uipt={uipt_codes}"
        f"&open_house_time=this-weekend&v=8"
    )
    try:
        r = session.get(json_url, headers={
            "User-Agent": ua,
            "Accept": "application/json",
            "Referer": listing_url,
            "Sec-Fetch-Site": "same-origin",
        }, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return [{"error": f"Redfin JSON request failed: {e}"}]

    # Response format: {}&&{actual JSON}  — strip the leading garbage
    raw = r.text
    parts = re.split(r"\}&+&\{", raw)
    if len(parts) >= 2:
        raw = "{" + parts[-1]
    else:
        raw = re.sub(r"^[^{]*", "", raw)

    try:
        data = json.loads(raw)
    except Exception as e:
        return [{"error": f"JSON parse failed: {e}"}]

    homes = data.get("payload", {}).get("homes", [])
    if not homes:
        return []

    results = []
    for h in homes:
        # Only keep listings with a confirmed open house time
        if not h.get("openHouseStart"):
            continue

        price      = _get(h, "price", "value")
        beds       = h.get("beds")
        baths      = h.get("baths")
        sqft       = _get(h, "sqFt", "value")
        year_built = _get(h, "yearBuilt", "value")

        # Client-side filtering (server only filters by uipt and open_house_time)
        if min_price      is not None and price      is not None and price < min_price:           continue
        if max_price      is not None and price      is not None and price > max_price:           continue
        if min_beds       is not None and beds       is not None and beds < min_beds:             continue
        if max_beds       is not None and beds       is not None and beds > max_beds:             continue
        if min_baths      is not None and baths      is not None and baths < min_baths:          continue
        if max_baths      is not None and baths      is not None and baths > max_baths:          continue
        if min_year_built is not None and year_built is not None and year_built < min_year_built: continue
        if max_year_built is not None and year_built is not None and year_built > max_year_built: continue
        if min_sqft       is not None and sqft       is not None and sqft < min_sqft:            continue
        if max_sqft       is not None and sqft       is not None and sqft > max_sqft:            continue

        results.append({
            "address":          _get(h, "streetLine", "value") or "",
            "city":             h.get("city", city),
            "state":            h.get("state", state),
            "zip":              _get(h, "postalCode", "value") or "",
            "price":            price,
            "beds":             beds,
            "baths":            baths,
            "sqft":             sqft,
            "year_built":       year_built,
            "open_house_start": _ms_to_iso(h.get("openHouseStart")),
            "open_house_end":   _ms_to_iso(h.get("openHouseEnd")),
            "latitude":         _get(h, "latLong", "value", "latitude"),
            "longitude":        _get(h, "latLong", "value", "longitude"),
            "url":              "https://www.redfin.com" + h.get("url", ""),
        })

    return results
