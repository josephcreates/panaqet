# routing_service.py
import asyncio
import logging
import time
from typing import Optional, List, Tuple, Dict, Any, Set

from fastapi import FastAPI, BackgroundTasks, Query, HTTPException, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Import your graph utilities (lazy loader version)
from graphml import (
    build_region_index,
    find_regions_for_points,
    load_composed_subgraph_for_paths,
    get_route_on_roads,
)

LOG = logging.getLogger("routing_api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

app = FastAPI(title="Routing & Geocoding Service (with Live Location)", version="2.1")


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5000",
        "http://localhost:5000",
        # add other origins used by your front-end, e.g. dev server origin
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Config ----------
PRELOAD_POINTS = [
    {"lat": 5.6037, "lng": -0.1870},  # Accra
    {"lat": 6.6666, "lng": -1.6163},  # Kumasi
]
ROUTE_CACHE_TTL = 60 * 60      # 1 hour
ROUTE_CACHE_MAX = 2000
COORD_ROUND_DECIMALS = 7
LOCATION_RETENTION_SECONDS = 60 * 60 * 6  # keep last-known for 6 hours (in-memory)
# ----------------------------

# Global routing caches & index
_region_index = None
_route_cache: Dict[str, Tuple[float, dict]] = {}  # key -> (timestamp, result)
_route_cache_access_order: Dict[str, float] = {}  # key -> last_access_time (LRU)


def _make_cache_key(pickup_lat, pickup_lng, drop_lat, drop_lng, alternatives):
    return (
        f"{round(pickup_lat, COORD_ROUND_DECIMALS)}|{round(pickup_lng, COORD_ROUND_DECIMALS)}|"
        f"{round(drop_lat, COORD_ROUND_DECIMALS)}|{round(drop_lng, COORD_ROUND_DECIMALS)}|{int(alternatives)}"
    )


def _cache_get(key: str):
    item = _route_cache.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > ROUTE_CACHE_TTL:
        _route_cache.pop(key, None)
        _route_cache_access_order.pop(key, None)
        return None
    _route_cache_access_order[key] = time.time()
    return val


def _cache_set(key: str, value: dict):
    if key in _route_cache:
        _route_cache[key] = (time.time(), value)
        _route_cache_access_order[key] = time.time()
        return
    if len(_route_cache) >= ROUTE_CACHE_MAX:
        oldest = sorted(_route_cache_access_order.items(), key=lambda kv: kv[1])[: max(1, int(0.1 * ROUTE_CACHE_MAX))]
        for k, _ in oldest:
            _route_cache.pop(k, None)
            _route_cache_access_order.pop(k, None)
    _route_cache[key] = (time.time(), value)
    _route_cache_access_order[key] = time.time()



# ---------- Live-location management (WebSocket + HTTP fallback) ----------
class ConnectionManager:
    """
    Manages WebSocket connections:
    - drivers connect to /ws/driver/{driver_id} and send locations
    - monitors (dispatchers/customers) connect to /ws/monitor and will receive broadcasts
    """

    def __init__(self):
        # driver_id -> websocket
        self._drivers: Dict[str, WebSocket] = {}
        # set of monitor websockets
        self._monitors: Set[WebSocket] = set()
        # simple lock for concurrent access to connection structures
        self._lock = asyncio.Lock()
        # last known positions: driver_id -> {"lat":..., "lng":..., "ts":..., "meta": {...}}
        self.last_known: Dict[str, Dict[str, Any]] = {}

    async def connect_driver(self, driver_id: str, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._drivers[driver_id] = websocket
            LOG.info("Driver WS connected: %s (drivers=%d)", driver_id, len(self._drivers))

    async def disconnect_driver(self, driver_id: str):
        async with self._lock:
            ws = self._drivers.pop(driver_id, None)
        LOG.info("Driver WS disconnected: %s", driver_id)
        # do NOT remove last_known (we retain for queries)

    async def connect_monitor(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._monitors.add(websocket)
            LOG.info("Monitor WS connected (monitors=%d)", len(self._monitors))

    async def disconnect_monitor(self, websocket: WebSocket):
        async with self._lock:
            self._monitors.discard(websocket)
            LOG.info("Monitor WS disconnected (monitors=%d)", len(self._monitors))

    async def receive_from_driver(self, driver_id: str, data: dict):
        """
        Called when a driver sends a location payload over its websocket OR HTTP fallback.
        We update last_known and broadcast to all monitors (non-blocking).
        """
        now_ts = time.time()
        payload = {
            "type": "location",
            "driver_id": str(driver_id),
            "lat": float(data.get("lat")),
            "lng": float(data.get("lng")),
            "ts": data.get("ts", now_ts),
            "meta": data.get("meta", {}),
        }
        # store last-known
        async with self._lock:
            self.last_known[str(driver_id)] = {
                "lat": payload["lat"],
                "lng": payload["lng"],
                "ts": payload["ts"],
                "meta": payload["meta"],
                "_received_at": now_ts
            }

        # broadcast to monitors (do not await every monitor synchronously)
        asyncio.create_task(self._broadcast_to_monitors_safe(payload))

    async def _broadcast_to_monitors_safe(self, data: dict):
        """
        Iterate monitors and send data; remove dead sockets.
        """
        async with self._lock:
            monitors = list(self._monitors)

        if not monitors:
            return

        send_tasks = []
        for ws in monitors:
            send_tasks.append(self._safe_send(ws, data))

        # schedule and wait, but don't raise on individual failures
        if send_tasks:
            await asyncio.gather(*send_tasks, return_exceptions=True)

    async def _safe_send(self, ws: WebSocket, data: dict):
        try:
            await ws.send_json(data)
        except Exception as e:
            # connection likely dead — remove it
            LOG.debug("Failed to send to monitor, removing: %s", e)
            try:
                await self.disconnect_monitor(ws)
            except Exception:
                pass

    # HTTP helper: return last knowns (optionally filter)
    def get_last_known(self, driver_id: Optional[str] = None):
        if driver_id:
            return self.last_known.get(str(driver_id))
        return self.last_known.copy()

    # Cleanup stale last-known entries older than retention (optional background)
    async def cleanup_last_known(self):
        cutoff = time.time() - LOCATION_RETENTION_SECONDS
        async with self._lock:
            to_remove = [k for k, v in self.last_known.items() if v.get("_received_at", 0) < cutoff]
            for k in to_remove:
                self.last_known.pop(k, None)
                LOG.debug("Pruned last_known for driver %s", k)


manager = ConnectionManager()


# Pydantic models
class LocationPayload(BaseModel):
    driver_id: str
    lat: float
    lng: float
    ts: Optional[float] = None
    meta: Optional[dict] = None


# WebSocket endpoint for drivers to stream location
@app.websocket("/ws/driver/{driver_id}")
async def ws_driver(websocket: WebSocket, driver_id: str):
    await websocket.accept()
    """
    Drivers open this socket and send JSON messages:
      {"lat": <float>, "lng": <float>, "ts": <unix seconds maybe>, "meta": {...}}
    The server updates last-known and broadcasts to all monitor clients.
    """
    await manager.connect_driver(driver_id, websocket)
    try:
        while True:
            try:
                msg = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                LOG.debug("Invalid JSON from driver %s: %s", driver_id, e)
                continue

            # Expect at least lat/lng; ignore bad messages
            if not ("lat" in msg and "lng" in msg):
                LOG.debug("Driver %s sent message without lat/lng: %s", driver_id, msg)
                continue

            # normalize
            payload = {
                "lat": float(msg["lat"]),
                "lng": float(msg["lng"]),
                "ts": msg.get("ts", time.time()),
                "meta": msg.get("meta", {})
            }
            
            await manager.receive_from_driver(driver_id, payload)

    except WebSocketDisconnect:
        await manager.disconnect_driver(driver_id)
    except Exception as e:
        LOG.exception("Driver WS error for %s: %s", driver_id, e)
        await manager.disconnect_driver(driver_id)


# WebSocket endpoint for monitors / dispatchers / customers
@app.websocket("/ws/monitor")
async def ws_monitor(websocket: WebSocket, filter_driver: Optional[str] = Query(None)):
    """
    Monitor clients connect here. They will receive every broadcast location message.
    Optional query param ?filter_driver=ID is available on connect but server currently broadcasts all locations;
    client can filter locally if desired.
    """
    await manager.connect_monitor(websocket)
    try:
        # simple keepalive loop: monitors may send pings or control messages
        while True:
            msg = await websocket.receive_text()  # we don't expect heavy incoming messages
            # a monitor may send "ping" or "subscribe driver_id"
            try:
                if not msg:
                    continue
                # optional simple protocol: "subscribe:<driver_id>"
                if msg.startswith("subscribe:"):
                    # client asked for a specific driver subscription; we still broadcast everything,
                    # but the client can rely on server-side filtering in future extension.
                    # acknowledging:
                    await websocket.send_text("subscribed")
            except Exception:
                pass
    except WebSocketDisconnect:
        await manager.disconnect_monitor(websocket)
    except Exception as e:
        LOG.exception("Monitor WS error: %s", e)
        await manager.disconnect_monitor(websocket)


# HTTP fallback: driver posts location JSON to this endpoint if WebSocket not available
@app.post("/location")
async def post_location(payload: LocationPayload = Body(...)):
    try:
        data = payload.dict()
        # normalize ts
        if data.get("ts") is None:
            data["ts"] = time.time()
        # update manager and broadcast
        await manager.receive_from_driver(data["driver_id"], {
            "lat": data["lat"],
            "lng": data["lng"],
            "ts": data["ts"],
            "meta": data.get("meta") or {}
        })
        return {"ok": True}
    except Exception as e:
        LOG.exception("Failed to accept posted location: %s", e)
        raise HTTPException(status_code=500, detail="Location handling failed.")


# HTTP endpoints to query last-known positions
@app.get("/locations")
def get_locations():
    return {"count": len(manager.get_last_known()), "locations": manager.get_last_known()}


@app.get("/locations/{driver_id}")
def get_location_driver(driver_id: str):
    lk = manager.get_last_known(driver_id)
    if not lk:
        raise HTTPException(status_code=404, detail="Driver not found or no last-known position")
    return lk


# ---------- Routing endpoints (preserve your existing logic) ----------
@app.on_event("startup")
async def startup_event():
    global _region_index
    LOG.info("Startup: building region index...")
    _region_index = build_region_index()
    LOG.info("Region index ready with %d entries", len(_region_index or {}))
    # preload common areas in background
    asyncio.create_task(_background_preload_points(PRELOAD_POINTS))
    # optional: schedule periodic cleanup of last-known positions
    asyncio.create_task(_periodic_cleanup_task())


async def _background_preload_points(points, persist=True):
    try:
        if not _region_index:
            build_region_index()
        regs = find_regions_for_points(points, index=_region_index)
        if not regs:
            LOG.warning("No regions found for preload.")
            return
        LOG.info("Preloading %d regions...", len(regs))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, load_composed_subgraph_for_paths, regs, persist)
        LOG.info("Preload complete.")
    except Exception as e:
        LOG.exception("Preload failed: %s", e)


async def _periodic_cleanup_task():
    while True:
        try:
            await manager.cleanup_last_known()
        except Exception:
            LOG.exception("cleanup_last_known failed")
        await asyncio.sleep(60 * 30)  # every 30 minutes


@app.get("/route")
async def route(
    pickup_lat: float = Query(...),
    pickup_lng: float = Query(...),
    drop_lat: float = Query(...),
    drop_lng: float = Query(...),
    alternatives: int = Query(1, ge=1, le=5),
):
    key = _make_cache_key(pickup_lat, pickup_lng, drop_lat, drop_lng, alternatives)
    cached = _cache_get(key)
    if cached is not None:
        LOG.debug("Route cache hit for key=%s", key)
        return {"from_cache": True, **cached}

    LOG.info("Route cache miss; computing route for key=%s", key)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, get_route_on_roads,
                                            {"lat": pickup_lat, "lng": pickup_lng},
                                            {"lat": drop_lat, "lng": drop_lng},
                                            alternatives)
        _cache_set(key, result)
        return {"from_cache": False, **result}
    except Exception as e:
        LOG.exception("Routing failed: %s", e)
        raise HTTPException(status_code=500, detail="Routing failed.")


@app.get("/regions")
def list_regions():
    idx = _region_index or build_region_index()
    out = [{"name": k, "bbox": v.get("bbox")} for k, v in idx.items()]
    return {"count": len(out), "regions": out}


@app.get("/status")
def status():
    return {"status": "ok", "service": "Routing + Live Location"}


# ---------- Run ----------
if __name__ == "__main__":
    uvicorn.run("routing_service:app", host="0.0.0.0", port=8010, log_level="info")
