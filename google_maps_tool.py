"""
Google Maps Distance Matrix tool for open house visit planning.

Requires:
    pip install googlemaps
    GOOGLE_MAPS_API_KEY environment variable set

API docs: https://developers.google.com/maps/documentation/distance-matrix
"""

import os
import time
from datetime import datetime

from dotenv import load_dotenv
import googlemaps
from langchain_core.tools import tool

load_dotenv()
from typing import Literal

# Google Maps Distance Matrix API limits per request
_MAX_ORIGINS = 10
_MAX_DESTS   = 10  # 10 × 10 = 100 elements — safe for free-tier accounts

TravelMode = Literal["driving", "transit", "walking", "bicycling"]

_client: googlemaps.Client | None = None


def _get_client() -> googlemaps.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not api_key:
            raise EnvironmentError("GOOGLE_MAPS_API_KEY environment variable is not set")
        _client = googlemaps.Client(key=api_key)
    return _client


def get_distance_matrix(
    origins: list[str],
    destinations: list[str],
    mode: TravelMode = "driving",
    departure_time: str | None = None,
) -> list[dict]:
    """
    Return travel time and distance between every origin-destination pair.

    Args:
        origins:        List of addresses or "lat,lng" strings.
        destinations:   List of addresses or "lat,lng" strings.
        mode:           Travel mode — "driving" | "transit" | "walking" | "bicycling".
        departure_time: ISO-8601 datetime string (e.g. "2025-05-17T10:00:00") used
                        for traffic-aware driving times or transit schedules.
                        Pass None to use a typical/average travel time.

    Returns:
        Flat list of dicts, one per origin-destination pair:
        [
          {
            "origin":        "123 Main St, Redmond, WA",
            "destination":   "456 Oak Ave, Bellevue, WA",
            "mode":          "driving",
            "distance_m":    12500,          # metres
            "distance_text": "12.5 km",
            "duration_s":    900,            # seconds
            "duration_text": "15 mins",
            "status":        "OK",           # or "ZERO_RESULTS" / "NOT_FOUND" etc.
          },
          ...
        ]
    """
    kwargs: dict = {"mode": mode}
    if departure_time:
        kwargs["departure_time"] = datetime.fromisoformat(departure_time)

    response = _get_client().distance_matrix(origins, destinations, **kwargs)

    results = []
    for i, row in enumerate(response["rows"]):
        for j, element in enumerate(row["elements"]):
            status = element.get("status", "UNKNOWN")
            entry: dict = {
                "origin":      origins[i],
                "destination": destinations[j],
                "mode":        mode,
                "status":      status,
            }
            if status == "OK":
                entry["distance_m"]    = element["distance"]["value"]
                entry["distance_text"] = element["distance"]["text"]
                entry["duration_s"]    = element["duration"]["value"]
                entry["duration_text"] = element["duration"]["text"]
            else:
                entry["distance_m"]    = None
                entry["distance_text"] = None
                entry["duration_s"]    = None
                entry["duration_text"] = None
            results.append(entry)

    return results


def _get_distance_matrix_batched(
    origins: list[str],
    destinations: list[str],
    mode: str,
    departure_time: str | None,
) -> list[dict]:
    """
    Call the Distance Matrix API in batches of _MAX_ORIGINS × _MAX_DESTS to
    stay within the 100-element-per-request limit on free-tier accounts.
    """
    all_results: list[dict] = []
    for i in range(0, len(origins), _MAX_ORIGINS):
        orig_batch = origins[i : i + _MAX_ORIGINS]
        for j in range(0, len(destinations), _MAX_DESTS):
            dest_batch = destinations[j : j + _MAX_DESTS]
            all_results.extend(
                get_distance_matrix(orig_batch, dest_batch, mode, departure_time)
            )
            # Brief pause to avoid hitting per-second rate limits
            if j + _MAX_DESTS < len(destinations):
                time.sleep(0.1)
        if i + _MAX_ORIGINS < len(origins):
            time.sleep(0.1)
    return all_results


@tool
def build_travel_time_matrix(
    locations: list[str],
    mode: str = "driving",
    departure_time: str | None = None,
) -> dict[str, int]:
    """
    Return travel time in seconds between every pair of addresses.

    Use this after getting open house listings to build a travel time matrix
    before scheduling visits. Include the user's home address as the first entry
    in locations so the schedule can start from home.

    Large location lists are automatically split into batches of 10 × 10 to
    stay within the Google Maps API 100-element-per-request limit.

    Args:
        locations:      List of addresses — put the user's home address first,
                        followed by all open house addresses.
        mode:           Travel mode: "driving" (default), "transit", "walking", "bicycling".
        departure_time: ISO-8601 string (e.g. "2025-05-17T09:00:00") for
                        traffic-aware times. Omit for average/typical times.

    Returns:
        Dict with "ORIGIN -> DESTINATION" keys and seconds as values.
        Example: {"123 Main St -> 456 Oak Ave": 900, ...}
    """
    pairs = _get_distance_matrix_batched(locations, locations, mode=mode, departure_time=departure_time)
    matrix: dict[str, int] = {}
    for p in pairs:
        if p["origin"] == p["destination"]:
            continue
        if p["status"] == "OK":
            matrix[f"{p['origin']} -> {p['destination']}"] = p["duration_s"]
    return matrix
