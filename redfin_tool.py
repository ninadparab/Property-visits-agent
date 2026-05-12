import requests
import pandas as pd
from io import StringIO
import time
import random
import json
from pathlib import Path

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

# ── Filter config ──────────────────────────────────────────────
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


def lookup_region_id(city: str, state: str) -> str | None:
    """Return Redfin region_id for a city/state.

    Checks region_map.json first; falls back to a live API call for unknown cities.
    """
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
        print(f"region_id lookup failed for {city}, {state}: {e}")
    return None


def format_price(price: int) -> str:
    if price >= 1_000_000:
        val = price / 1_000_000
        formatted = int(val) if val == int(val) else round(val, 1)
        return f"{formatted}M"
    elif price >= 1_000:
        val = price / 1_000
        formatted = int(val) if val == int(val) else round(val, 1)
        return f"{formatted}K"
    return str(price)


def build_redfin_filter_url(
    region_id, state, city,
    property_types=None, min_price=None, max_price=None,
    min_beds=None, max_beds=None, min_baths=None, max_baths=None,
    min_year_built=None, max_year_built=None,
    min_sqft=None, max_sqft=None,
    exclude_age_restricted=True, include_open_houses=True,
) -> str:
    filters = []
    if property_types:
        valid = [PROPERTY_TYPE_MAP[t] for t in property_types if t in PROPERTY_TYPE_MAP]
        if valid:
            filters.append("property-type=" + "+".join(valid))
    if min_price:   filters.append(f"min-price={format_price(min_price)}")
    if max_price:   filters.append(f"max-price={format_price(max_price)}")
    if min_beds:    filters.append(f"min-beds={min_beds}")
    if max_beds:    filters.append(f"max-beds={max_beds}")
    if min_baths:   filters.append(f"min-baths={min_baths}")
    if max_baths:   filters.append(f"max-baths={max_baths}")
    if min_year_built: filters.append(f"min-year-built={min_year_built}")
    if max_year_built: filters.append(f"max-year-built={max_year_built}")
    if min_sqft:    filters.append(f"min-sqft={min_sqft}")
    if max_sqft:    filters.append(f"max-sqft={max_sqft}")
    if exclude_age_restricted: filters.append("exclude-age-restricted")
    if include_open_houses:    filters.append("open-house-time=this-weekend")

    base = f"https://www.redfin.com/city/{region_id}/{state}/{city}"
    return f"{base}/filter/{','.join(filters)}" if filters else base


def scrape_open_houses(
    city, state, region_id=None,
    property_types=None, min_price=None, max_price=None,
    min_beds=None, max_beds=None, min_baths=None, max_baths=None,
    min_year_built=None, max_year_built=None,
    min_sqft=None, max_sqft=None,
    exclude_age_restricted=True,
) -> list[dict]:

    if region_id is None:
        region_id = lookup_region_id(city, state)
        if region_id is None:
            print(f"Could not resolve region_id for {city}, {state}")
            return []

    listing_url = build_redfin_filter_url(
        region_id=region_id, state=state, city=city,
        property_types=property_types,
        min_price=min_price, max_price=max_price,
        min_beds=min_beds, max_beds=max_beds,
        min_baths=min_baths, max_baths=max_baths,
        min_year_built=min_year_built, max_year_built=max_year_built,
        min_sqft=min_sqft, max_sqft=max_sqft,
        exclude_age_restricted=exclude_age_restricted,
        include_open_houses=True,
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
    except Exception as e:
        print(f"Warning: session warmup failed: {e}")

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
        print(f"Request failed: {e}")
        return []

    raw = r.text
    try:
        df = pd.read_csv(StringIO(raw), skiprows=1)
        if not (df.columns[0].startswith("SALE TYPE") or "ADDRESS" in df.columns):
            df = pd.read_csv(StringIO(raw))
    except Exception as e:
        print(f"CSV parse error: {e}")
        return []

    # Clean numeric columns
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
    if min_price      and price_col in df.columns: df = df[df[price_col] >= min_price]
    if max_price      and price_col in df.columns: df = df[df[price_col] <= max_price]
    if min_year_built and yr_col    in df.columns: df = df[df[yr_col]    >= min_year_built]
    if min_sqft       and sqft_col  in df.columns: df = df[df[sqft_col]  >= min_sqft]
    if max_sqft       and sqft_col  in df.columns: df = df[df[sqft_col]  <= max_sqft]

    url_col = next((c for c in df.columns if c.startswith("URL")), "")

    results = []
    for _, row in df.iterrows():
        results.append({
            "address":            row.get("ADDRESS", ""),
            "city":               row.get("CITY", city),
            "state":              row.get("STATE OR PROVINCE", state),
            "zip":                row.get("ZIP OR POSTAL CODE", ""),
            "price":              row.get(price_col, None),
            "beds":               row.get(beds_col, None),
            "baths":              row.get(baths_col, None),
            "sqft":               row.get(sqft_col, None),
            "year_built":         row.get(yr_col, None),
            "open_house_start":   row.get("NEXT OPEN HOUSE START TIME", None),
            "open_house_end":     row.get("NEXT OPEN HOUSE END TIME", None),
            "latitude":           row.get("LATITUDE", None),
            "longitude":          row.get("LONGITUDE", None),
            "url":                row.get(url_col, "") if url_col else "",
        })

    return results
