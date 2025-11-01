# utils/geocode.py
import requests
from time import sleep
from flask import current_app

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

def geocode_address(address, country_codes=None, limit=1, pause=0.1):
    """
    Geocode a string address to (lat, lng) using Nominatim.
    Returns (lat, lng) as floats or (None, None) if not found.
    NOTE: Respect Nominatim usage policy: add caching & rate-limiting for production.
    """
    if not address:
        return None, None

    params = {
        "q": address,
        "format": "json",
        "limit": limit,
        "addressdetails": 0
    }
    if country_codes:
        params["countrycodes"] = country_codes  # e.g. "gh"

    headers = {
        "User-Agent": current_app.config.get("GEOCODER_USER_AGENT", "myapp/1.0 (+https://example.com)"),
        "Accept-Language": "en"
    }

    try:
        # polite pause to avoid hammering free Nominatim
        if pause:
            sleep(pause)
        r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=5)
        r.raise_for_status()
        arr = r.json()
        if arr:
            lat = float(arr[0]["lat"])
            lng = float(arr[0]["lon"])
            return lat, lng
    except Exception as e:
        current_app.logger.debug("Geocode failed for %r: %s", address, e)
    return None, None
