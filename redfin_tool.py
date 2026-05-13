import requests
import pandas as pd
from io import StringIO
import time
import random
import json
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


PROPERTY_TYPE_MAP = {
    "house":     "house",
    "condo":     "condo",
    "townhouse": "townhouse",
    "land":      "land",
    "other":     "other",
}

UIPT_MAP = {
    "house":     "1",
    "condo":     "2",
    "townhouse": "3",
    "land":      "6",
    "other":     "8",
}


def _lookup_region_id(city: str, state: str) -> str | None:
    key = f"{city}_{state}"
    cached = _load_region_map().get(key)
    if cached:
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
            raw_id = row.get("id", "")  # e.g. "6_14913"
            region_id = raw_id.split("_")[-1]
            if region_id.isdigit():
                return region_id
    except Exception as e:
        raise RuntimeError(f"region_id lookup failed for {city}, {state}: {e}") from e
    return None


def _build_redfin_filter_url(
    region_id, state, city,
    property_types=None, min_price=None, max_price=None,
    min_beds=None, max_beds=None, min_baths=None, max_baths=None,
    min_year_built=None, max_year_built=None,
    min_sqft=None, max_sqft=None,
    exclude_age_restricted=True,
) -> str:
    def fmt(price):
        if price >= 1_000_000:
            v = price / 1_000_000
            return f"{int(v) if v == int(v) else round(v, 1)}M"
        if price >= 1_000:
            v = price / 1_000
            return f"{int(v) if v == int(v) else round(v, 1)}K"
        return str(price)

    filters = []
    if property_types:
        valid = [PROPERTY_TYPE_MAP[t] for t in property_types if t in PROPERTY_TYPE_MAP]
        if valid:
            filters.append("property-type=" + "+".join(valid))
    if min_price:       filters.append(f"min-price={fmt(min_price)}")
    if max_price:       filters.append(f"max-price={fmt(max_price)}")
    if min_beds:        filters.append(f"min-beds={min_beds}")
    if max_beds:        filters.append(f"max-beds={max_beds}")
    if min_baths:       filters.append(f"min-baths={min_baths}")
    if max_baths:       filters.append(f"max-baths={max_baths}")
    if min_year_built:  filters.append(f"min-year-built={min_year_built}")
    if max_year_built:  filters.append(f"max-year-built={max_year_built}")
    if min_sqft:        filters.append(f"min-sqft={min_sqft}")
    if max_sqft:        filters.append(f"max-sqft={max_sqft}")
    if exclude_age_restricted: filters.append("exclude-age-restricted")
    filters.append("open-house-time=this-weekend")

    # Redfin URLs use hyphenated city names (e.g. "San-Jose", not "San Jose")
    city_slug = city.replace(" ", "-")
    base = f"https://www.redfin.com/city/{region_id}/{state}/{city_slug}"
    return f"{base}/filter/{','.join(filters)}" if filters else base


def _nan_to_none(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


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
        Returns empty list with an "error" key if the request fails.
    """
    region_id = _lookup_region_id(city, state)
    if region_id is None:
        return [{"error": f"Could not resolve Redfin region ID for {city}, {state}"}]

    listing_url = _build_redfin_filter_url(
        region_id=region_id, state=state, city=city,
        property_types=property_types,
        min_price=min_price, max_price=max_price,
        min_beds=min_beds, max_beds=max_beds,
        min_baths=min_baths, max_baths=max_baths,
        min_year_built=min_year_built, max_year_built=max_year_built,
        min_sqft=min_sqft, max_sqft=max_sqft,
        exclude_age_restricted=True,
    )

    session = requests.Session()
    headers_browser = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    try:
        session.get(listing_url, headers=headers_browser, timeout=15)
        time.sleep(random.uniform(2, 4))
    except Exception:
        pass  # session warmup is best-effort

    uipt_codes = (
        ",".join(UIPT_MAP[t] for t in property_types if t in UIPT_MAP)
        if property_types else "1,2,3"
    )
    csv_url = (
        f"https://www.redfin.com/stingray/api/gis-csv"
        f"?al=1"
        f"&market={state.lower()}"
        f"&region_id={region_id}"
        f"&region_type=6"
        f"&sf=1,2,3,5,6,7"
        f"&num_homes=350"
        f"&uipt={uipt_codes}"
        f"&open_house_time=this-weekend"
    )
    headers_csv = {
        **headers_browser,
        "Referer": listing_url,
        "Sec-Fetch-Site": "same-origin",
    }
    try:
        r = session.get(csv_url, headers=headers_csv, timeout=15)
        r.raise_for_status()
    except Exception as e:
        return [{"error": f"Redfin CSV request failed: {e}"}]

    raw = r.text
    try:
        df = pd.read_csv(StringIO(raw), skiprows=1)
        if not (df.columns[0].startswith("SALE TYPE") or "ADDRESS" in df.columns):
            df = pd.read_csv(StringIO(raw))
    except Exception as e:
        return [{"error": f"CSV parse failed: {e}"}]

    price_col = "PRICE"
    sqft_col  = "SQUARE FEET"
    beds_col  = "BEDS"
    baths_col = "BATHS"
    yr_col    = "YEAR BUILT"

    if price_col in df.columns:
        df[price_col] = pd.to_numeric(df[price_col].astype(str).str.replace(r'[\$,]', '', regex=True), errors='coerce')
    if sqft_col in df.columns:
        df[sqft_col]  = pd.to_numeric(df[sqft_col].astype(str).str.replace(r'[,]', '', regex=True), errors='coerce')

    if min_beds       and beds_col  in df.columns: df = df[df[beds_col]  >= min_beds]
    if max_beds       and beds_col  in df.columns: df = df[df[beds_col]  <= max_beds]
    if min_baths      and baths_col in df.columns: df = df[df[baths_col] >= min_baths]
    if max_baths      and baths_col in df.columns: df = df[df[baths_col] <= max_baths]
    if min_price      and price_col in df.columns: df = df[df[price_col] >= min_price]
    if max_price      and price_col in df.columns: df = df[df[price_col] <= max_price]
    if min_year_built and yr_col    in df.columns: df = df[df[yr_col]    >= min_year_built]
    if max_year_built and yr_col    in df.columns: df = df[df[yr_col]    <= max_year_built]
    if min_sqft       and sqft_col  in df.columns: df = df[df[sqft_col]  >= min_sqft]
    if max_sqft       and sqft_col  in df.columns: df = df[df[sqft_col]  <= max_sqft]

    url_col = next((c for c in df.columns if c.startswith("URL")), "")

    results = []
    for _, row in df.iterrows():
        results.append({
            "address":          _nan_to_none(row.get("ADDRESS", "")),
            "city":             _nan_to_none(row.get("CITY", city)),
            "state":            _nan_to_none(row.get("STATE OR PROVINCE", state)),
            "zip":              _nan_to_none(row.get("ZIP OR POSTAL CODE", "")),
            "price":            _nan_to_none(row.get(price_col)),
            "beds":             _nan_to_none(row.get(beds_col)),
            "baths":            _nan_to_none(row.get(baths_col)),
            "sqft":             _nan_to_none(row.get(sqft_col)),
            "year_built":       _nan_to_none(row.get(yr_col)),
            "open_house_start": _nan_to_none(row.get("NEXT OPEN HOUSE START TIME")),
            "open_house_end":   _nan_to_none(row.get("NEXT OPEN HOUSE END TIME")),
            "latitude":         _nan_to_none(row.get("LATITUDE")),
            "longitude":        _nan_to_none(row.get("LONGITUDE")),
            "url":              _nan_to_none(row.get(url_col, "")) if url_col else "",
        })

    return results
