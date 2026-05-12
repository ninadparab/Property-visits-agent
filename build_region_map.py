"""
Builds region_map.json from a manually-curated list of Redfin region IDs.

How to find a region_id for a new city:
  1. Go to https://www.redfin.com and search for the city.
  2. Look at the URL — it will look like:
         https://www.redfin.com/city/14913/WA/Redmond
  3. The number between /city/ and the state is the region_id.
  4. Add it to KNOWN_IDS below and re-run this script.

Usage:
    python build_region_map.py
"""

from pathlib import Path
import json

OUTPUT_FILE = Path(__file__).parent / "region_map.json"

# city_STATE -> region_id
# Find IDs from Redfin city URLs: redfin.com/city/{region_id}/{STATE}/{City}
KNOWN_IDS: dict[str, str] = {
    # ── Eastside ──────────────────────────────────────────────
    "Redmond_WA":          "14913",
    "Bellevue_WA":         "16163",
    "Kirkland_WA":         "16160",
    "Sammamish_WA":        "30772",
    "Issaquah_WA":         "16154",
    "Mercer Island_WA":    "16161",
    "Newcastle_WA":        "27505",
    "Medina_WA":           "16157",
    # ── Seattle core ──────────────────────────────────────────
    "Seattle_WA":          "16163",   # placeholder — confirm from URL
    # ── North ─────────────────────────────────────────────────
    "Bothell_WA":          "16164",
    "Woodinville_WA":      "16165",
    "Kenmore_WA":          "29804",
    "Shoreline_WA":        "30432",
    "Lake Forest Park_WA": "29803",
    "Edmonds_WA":          "16166",
    "Lynnwood_WA":         "16167",
    "Mukilteo_WA":         "16168",
    "Mountlake Terrace_WA":"30433",
    "Everett_WA":          "16169",
    "Snohomish_WA":        "16170",
    "Monroe_WA":           "16171",
    # ── East foothills ────────────────────────────────────────
    "Duvall_WA":           "16172",
    "Carnation_WA":        "16173",
    "Snoqualmie_WA":       "16174",
    "North Bend_WA":       "16175",
    # ── South ─────────────────────────────────────────────────
    "Renton_WA":           "16176",
    "Kent_WA":             "16177",
    "Auburn_WA":           "16178",
    "Federal Way_WA":      "16179",
    "Burien_WA":           "16180",
    "Tukwila_WA":          "16181",
    "Des Moines_WA":       "16182",
    "Maple Valley_WA":     "16183",
}

# IDs above (except Redmond) are placeholder estimates.
# Replace each with the real ID found from the Redfin city URL before use.
VERIFIED: set[str] = {
    "Redmond_WA",
}


def main():
    existing: dict[str, str] = {}
    if OUTPUT_FILE.exists():
        existing = json.loads(OUTPUT_FILE.read_text())

    merged = {**existing, **KNOWN_IDS}
    OUTPUT_FILE.write_text(json.dumps(merged, indent=2, sort_keys=True))

    unverified = [k for k in KNOWN_IDS if k not in VERIFIED]
    print(f"Wrote {len(merged)} entries to {OUTPUT_FILE.name}")
    if unverified:
        print(f"\nWARNING: {len(unverified)} entries have placeholder IDs -- verify from Redfin URLs:")
        for k in sorted(unverified):
            print(f"   {k}: {KNOWN_IDS[k]}")


if __name__ == "__main__":
    main()
