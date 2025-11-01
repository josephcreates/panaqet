import json
import time
import logging
from typing import Optional, Dict
import asyncio
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from nats.aio.client import Client as NATS
from nats.aio.errors import ErrConnectionClosed, ErrTimeout, ErrNoServers

LOG = logging.getLogger("live_location")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Constants
NATS_URL = "nats://127.0.0.1:4222"
NATS_SUBJECT = "drivers.locations"

# In-memory cache
last_known_locations: Dict[str, dict] = {}

# Flask + SocketIO
app = Flask("live_location")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

# --- NATS Connection ---
nats_client = NATS()
nats_connected = False  # flag to avoid multiple connections


async def nats_connect():
    global nats_connected
    if nats_connected:
        return

    try:
        await nats_client.connect(servers=[NATS_URL])
        nats_connected = True
        LOG.info("Connected to NATS server at %s", NATS_URL)

        async def message_handler(msg):
            data = json.loads(msg.data.decode())
            driver_id = data["driver_id"]
            last_known_locations[driver_id] = data
            socketio.emit("location", data, namespace="/monitor", broadcast=True)

        await nats_client.subscribe(NATS_SUBJECT, cb=message_handler)

        # Keep the connection alive
        while True:
            await asyncio.sleep(1)

    except (ErrNoServers, ErrConnectionClosed, ErrTimeout) as e:
        LOG.error("NATS connection error: %s", e)
        nats_connected = False
        await asyncio.sleep(5)  # retry after 5s
        await nats_connect()  # recursive reconnect


def start_nats_background():
    """Run NATS connection in a separate asyncio task."""
    async def runner():
        while True:
            try:
                await nats_connect()
            except Exception as e:
                LOG.error("NATS error: %s", e)
            await asyncio.sleep(5)  # retry every 5s if connection fails

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(runner())
    loop.run_forever()


# --- Lifecycle Hook ---
@app.before_request
def before_request():
    """Start NATS background task on first request."""
    if not nats_connected:
        LOG.info("Starting NATS background task...")
        socketio.start_background_task(start_nats_background)


# --- Helper Functions ---
def store_last_known(driver_id: str, payload: dict):
    payload["_received_at"] = time.time()
    last_known_locations[driver_id] = payload

    if nats_connected:
        loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(
            nats_client.publish(NATS_SUBJECT, json.dumps(payload).encode()),
            loop
        )


def get_last_known(driver_id: Optional[str] = None):
    if driver_id:
        return last_known_locations.get(driver_id)
    return last_known_locations


# --- SocketIO Events ---
@socketio.on("register_driver", namespace="/driver")
def on_register_driver(data):
    driver_id = data.get("driver_id")
    if not driver_id:
        emit("error", {"error": "missing driver_id"})
        return
    join_room(driver_id)
    LOG.info("Driver registered socket: %s", driver_id)
    emit("registered", {"ok": True})


@socketio.on("location_update", namespace="/driver")
def on_location_update(data):
    try:
        driver_id = str(data.get("driver_id"))
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
    except Exception:
        emit("error", {"error": "invalid payload"})
        return

    payload = {
        "type": "location",
        "driver_id": driver_id,
        "lat": lat,
        "lng": lng,
        "ts": data.get("ts", time.time()),
        "meta": data.get("meta", {}),
    }

    store_last_known(driver_id, payload)
    socketio.emit("location", payload, namespace="/monitor", broadcast=True)
    emit("ok", {"received": True})


@socketio.on("register_monitor", namespace="/monitor")
def on_register_monitor(data):
    join_room(data.get("subscribe", "all"))
    emit("registered", {"ok": True})
    LOG.info("Monitor registered")


# --- HTTP Endpoints ---
@app.route("/location", methods=["POST"])
def post_location():
    payload = request.get_json(force=True)
    if not payload:
        return jsonify({"error": "invalid json"}), 400
    driver_id = payload.get("driver_id")
    lat = payload.get("lat")
    lng = payload.get("lng")
    if not driver_id or lat is None or lng is None:
        return jsonify({"error": "missing fields"}), 400

    data = {
        "type": "location",
        "driver_id": str(driver_id),
        "lat": float(lat),
        "lng": float(lng),
        "ts": payload.get("ts", time.time()),
        "meta": payload.get("meta", {}),
    }
    store_last_known(str(driver_id), data)
    socketio.emit("location", data, namespace="/monitor", broadcast=True)
    return jsonify({"ok": True})


@app.route("/locations", methods=["GET"])
def http_get_locations():
    return jsonify({"count": len(last_known_locations), "locations": last_known_locations})


@app.route("/locations/<driver_id>", methods=["GET"])
def http_get_location(driver_id):
    val = get_last_known(driver_id)
    if not val:
        return jsonify({"error": "not found"}), 404
    return jsonify(val)


if __name__ == "__main__":
    LOG.info("Live Location Service starting...")
    socketio.run(app, host="0.0.0.0", port=9000)
