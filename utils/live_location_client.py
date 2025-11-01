# utils/live_location_client.py
import requests
from flask import current_app

GO_LIVE_LOCATION = "http://127.0.0.1:9000"  # change host/port when deployed


def send_driver_location_to_live_service(driver_id, lat, lng, ts=None, meta=None):
    """
    Send driver location update to the Go live location microservice.
    """
    payload = {
        "driver_id": str(driver_id),
        "lat": float(lat),
        "lng": float(lng),
    }
    if ts:
        payload["ts"] = ts
    if meta:
        payload["meta"] = meta

    try:
        r = requests.post(f"{GO_LIVE_LOCATION}/location", json=payload, timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if current_app:
            current_app.logger.warning("Failed to send location to live service: %s", e)
        else:
            print("Live service send failed:", e)
        return None


def get_last_known(driver_id):
    """
    Fetch last known driver location from the Go live location microservice.
    """
    try:
        r = requests.get(f"{GO_LIVE_LOCATION}/locations/{driver_id}", timeout=2)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return None
        raise
    except Exception as e:
        if current_app:
            current_app.logger.warning("Failed to fetch last-known: %s", e)
        else:
            print("Fetch last-known failed:", e)
        return None


def get_all_last_known():
    """
    Fetch all drivers' last known locations from the Go live location microservice.
    """
    try:
        r = requests.get(f"{GO_LIVE_LOCATION}/locations", timeout=3)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if current_app:
            current_app.logger.warning("Failed to fetch all last-known: %s", e)
        else:
            print("Fetch all last-known failed:", e)
        return None
