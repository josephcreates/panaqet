# graphml.py (lazy loader version)
import os
import logging
import json
import math
import time
from pathlib import Path
import xml.etree.ElementTree as ET
from functools import lru_cache

import networkx as nx
import osmnx as ox

LOG = logging.getLogger(__name__)
logging.getLogger("osmnx").setLevel(logging.WARNING)

# Paths
DATA_DIR = Path("data")
REGION_INDEX_PATH = DATA_DIR / "region_index.json"   # generated automatically
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# File paths your app expects
UNPROJ_PATH = str(DATA_DIR / "ghana_drive_unprojected.graphml")
PROJ_PATH   = str(DATA_DIR / "ghana_drive_merged.graphml")

# In-memory caches (simple LRU by count)
MAX_LOADED_COMPOSED = 2  # at most keep N composed subgraphs in memory
_loaded_cache = {}       # key -> (timestamp, (G_unproj, G_proj, G_simple))
# persisted cache files will be in CACHE_DIR with safe names

# tweak osmnx
ox.settings.use_cache = False
ox.settings.log_console = False
ox.settings.timeout = 180

# -------------------------
# Helper: compute bbox from a GraphML file by streaming nodes (low memory)
# -------------------------
def _compute_bbox_from_graphml(path):
    """
    Stream-parse GraphML xml to extract node x/y. Return (minx,miny,maxx,maxy).
    Very lightweight: does not build the graph in memory.
    """
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    try:
        # iterparse 'end' events so we can clear processed elements
        for event, elem in ET.iterparse(path, events=("end",)):
            tag = elem.tag
            # node tags usually end with 'node' (namespace aware)
            if tag.endswith("node"):
                x = y = None
                # GraphML node children are <data key="x">...</data> etc.
                for data in elem.findall("./"):
                    # data.tag endswith 'data'
                    if data.tag.endswith("data"):
                        # key attribute may be 'x' or something mapping to x
                        key = data.get("key")
                        txt = (data.text or "").strip()
                        # crude but effective: look for numeric with decimal
                        if key and txt:
                            # keys used by osmnx are often 'x' and 'y'
                            if key.lower().endswith("x") or key.lower() == "x":
                                try:
                                    x = float(txt)
                                except Exception:
                                    pass
                            if key.lower().endswith("y") or key.lower() == "y":
                                try:
                                    y = float(txt)
                                except Exception:
                                    pass
                # If either x or y are None, try attributes (rare)
                if x is None or y is None:
                    # try to parse data text of children for numbers (fallback)
                    texts = [ (c.text or "") for c in elem.findall(".//") ]
                    for t in texts:
                        t = t.strip()
                        if not t:
                            continue
                        try:
                            v = float(t)
                            # heuristic: lon in [-180,180], lat in [-90,90]
                            if -180.0 <= v <= 180.0 and x is None:
                                x = v
                            elif -90.0 <= v <= 90.0 and y is None:
                                y = v
                        except Exception:
                            pass
                if x is not None and y is not None:
                    minx = min(minx, x)
                    maxx = max(maxx, x)
                    miny = min(miny, y)
                    maxy = max(maxy, y)
                # clear processed node to keep memory low
                elem.clear()
        if minx == float("inf"):
            return None
        return (minx, miny, maxx, maxy)
    except Exception as e:
        LOG.exception("Failed bbox parse for %s: %s", path, e)
        return None

# -------------------------
# Build or load region index
# -------------------------
def build_region_index(force=False):
    """
    Inspect graphml files in data/ and create region_index.json with { filename: bbox } entries.
    This is cheap and should be run once (script will auto-run if index missing).
    """
    if REGION_INDEX_PATH.exists() and not force:
        try:
            with open(REGION_INDEX_PATH, "r", encoding="utf8") as fh:
                idx = json.load(fh)
            LOG.info("Loaded region index with %d entries.", len(idx))
            return idx
        except Exception:
            LOG.warning("Failed to load existing region index, rebuilding.")
    idx = {}
    pattern = "*Region*Ghana.graphml"
    files = sorted(DATA_DIR.glob(pattern))
    if not files:
        # fallback: index any graphml files present
        files = sorted(DATA_DIR.glob("*.graphml"))
    for f in files:
        LOG.info("Indexing region file: %s", f.name)
        bbox = _compute_bbox_from_graphml(str(f))
        if bbox:
            idx[f.name] = {
                "path": str(f),
                "bbox": bbox
            }
            LOG.info("  bbox=%s", bbox)
        else:
            LOG.warning("  Could not compute bbox for %s", f.name)
    # persist
    try:
        with open(REGION_INDEX_PATH, "w", encoding="utf8") as fh:
            json.dump(idx, fh)
        LOG.info("Saved region index with %d entries.", len(idx))
    except Exception as e:
        LOG.exception("Failed to save region index: %s", e)
    return idx

# -------------------------
# Utility: point inside bbox
# -------------------------
def _point_in_bbox(lon, lat, bbox):
    minx, miny, maxx, maxy = bbox
    return (lon >= minx and lon <= maxx and lat >= miny and lat <= maxy)

# -------------------------
# Find region files relevant to a point / two points
# -------------------------
def find_regions_for_points(points, index=None):
    """
    points: iterable of (lat, lon) or {'lat':..., 'lng':...}
    Returns list of region file paths that contain either point. If none contain, chooses nearest region(s).
    """
    if index is None:
        index = build_region_index()
    # normalize points
    pts = []
    for p in points:
        if isinstance(p, dict):
            pts.append((p['lng'], p['lat']))
        else:
            lat, lon = p
            pts.append((lon, lat))
    matched = set()
    for lon, lat in pts:
        for name, meta in index.items():
            bbox = meta.get("bbox")
            if bbox and _point_in_bbox(lon, lat, bbox):
                matched.add(meta["path"])
    if matched:
        return sorted(matched)
    # if nothing matched, choose nearest region(s) by bbox center distance
    # compute bbox centers
    centers = []
    for name, meta in index.items():
        bbox = meta.get("bbox")
        if not bbox:
            continue
        minx, miny, maxx, maxy = bbox
        cx = (minx + maxx) / 2.0
        cy = (miny + maxy) / 2.0
        centers.append((meta["path"], cx, cy))
    chosen = set()
    for lon, lat in pts:
        best = None
        bestd = float("inf")
        for path, cx, cy in centers:
            d = (lon - cx) ** 2 + (lat - cy) ** 2
            if d < bestd:
                bestd = d
                best = path
        if best:
            chosen.add(best)
    return sorted(chosen)

# -------------------------
# Compose & cache small subgraph from region files
# -------------------------
def _cache_key_for_paths(paths):
    # deterministic key for a set of paths
    base = "|".join(sorted([str(p) for p in paths]))
    # safe filename
    key = base.replace(os.sep, "_").replace(":", "_").replace(" ", "_")
    return key

def load_composed_subgraph_for_paths(paths, persist=True):
    """
    Load region graphml files (list of paths), compose them, project, build G_simple.
    Use in-memory cache with a small capacity.
    Returns (G_unproj, G_proj, G_simple)
    """
    # normalize paths
    paths = sorted([str(p) for p in paths])
    key = _cache_key_for_paths(paths)
    # check in-memory cache first
    if key in _loaded_cache:
        _loaded_cache[key] = (time.time(), _loaded_cache[key][1])  # refresh timestamp
        LOG.info("Using cached composed graph for key %s (in-memory)", key)
        return _loaded_cache[key][1]

    # check persisted cache file
    gpkl_path = CACHE_DIR / f"{key}.gpickle"
    unproj_pkl = CACHE_DIR / f"{key}_unproj.gpickle"
    if gpkl_path.exists() and unproj_pkl.exists():
        try:
            G_simple = nx.read_gpickle(gpkl_path)
            G_unproj = nx.read_gpickle(unproj_pkl)
            # create a projected variant by projecting G_unproj (cheap compared to huge load)
            G_proj = ox.project_graph(G_unproj)
            LOG.info("Loaded composed graphs from disk cache: %s", gpkl_path.name)
            # insert into mem cache
            _loaded_cache[key] = (time.time(), (G_unproj, G_proj, G_simple))
            _evict_if_needed()
            return (G_unproj, G_proj, G_simple)
        except Exception:
            LOG.warning("Failed to load persisted cache for %s, rebuilding.", key)

    # load and compose the region files (this will load only a subset of the national graph)
    graphs = []
    for p in paths:
        try:
            LOG.info("Loading region file: %s", p)
            g = ox.load_graphml(p)
            graphs.append(g)
        except Exception as e:
            LOG.exception("Failed to load region file %s: %s", p, e)
    if not graphs:
        LOG.error("No graphs loaded for paths: %s", paths)
        return (None, None, None)

    # compose
    if len(graphs) == 1:
        G_unproj = graphs[0]
    else:
        G_unproj = nx.compose_all(graphs)

    # Project for metrics
    try:
        G_proj = ox.project_graph(G_unproj)
    except Exception as e:
        LOG.exception("Projecting composed graph failed: %s", e)
        G_proj = None

    # Build simple digraph (may still be memory heavy but smaller than national)
    try:
        G_simple = _multigraph_to_simple_digraph_min(G_proj, weight_key='length')
    except Exception as e:
        LOG.exception("Failed to build simple graph: %s", e)
        G_simple = None

    # persist caches if requested
    if persist:
        try:
            nx.write_gpickle(G_simple, gpkl_path)
            nx.write_gpickle(G_unproj, unproj_pkl)
            LOG.info("Saved composed cache to %s and %s", gpkl_path.name, unproj_pkl.name)
        except Exception as e:
            LOG.warning("Failed to persist composed cache: %s", e)

    # add to in-memory cache and evict oldest if needed
    _loaded_cache[key] = (time.time(), (G_unproj, G_proj, G_simple))
    _evict_if_needed()
    return (G_unproj, G_proj, G_simple)

def _evict_if_needed():
    # simple timestamp-based LRU eviction
    if len(_loaded_cache) <= MAX_LOADED_COMPOSED:
        return
    # evict oldest until under limit
    items = sorted(_loaded_cache.items(), key=lambda kv: kv[1][0])
    while len(_loaded_cache) > MAX_LOADED_COMPOSED:
        k, (ts, val) = items.pop(0)
        try:
            del _loaded_cache[k]
            LOG.info("Evicted cached composed graph: %s", k)
        except KeyError:
            pass

# -------------------------
# Public route function (uses lazy loading)
# -------------------------
def get_route_on_roads(pickup, dropoff, num_alternatives=1):
    """
    Lazy routing: determines which region files to load for given pickup/dropoff,
    composes a subgraph, and runs shortest-path on that subgraph.
    pickup/dropoff are dicts: {'lat': .., 'lng': ..}
    """
    if not pickup or not dropoff:
        return {'route_coords': [[pickup['lng'], pickup['lat']], [dropoff['lng'], dropoff['lat']]], 'eta_min': None, 'alt_routes': []}

    # build index if needed
    idx = build_region_index()

    # select regions that likely contain the points
    selected = find_regions_for_points([pickup, dropoff], index=idx)
    if not selected:
        LOG.warning("No region files found for points; falling back to straight line.")
        return {'route_coords': [[pickup['lng'], pickup['lat']], [dropoff['lng'], dropoff['lat']]], 'eta_min': None, 'alt_routes': []}

    # load composed subgraph for these region files
    G_unproj, G_proj, G_simple = load_composed_subgraph_for_paths(selected, persist=True)
    if G_unproj is None or G_proj is None or G_simple is None:
        LOG.error("Could not load composed graph for routing; falling back.")
        return {'route_coords': [[pickup['lng'], pickup['lat']], [dropoff['lng'], dropoff['lat']]], 'eta_min': None, 'alt_routes': []}

    # now perform routing similar to your previous implementation
    try:
        orig_node = ox.distance.nearest_nodes(G_unproj, X=pickup['lng'], Y=pickup['lat'])
        dest_node = ox.distance.nearest_nodes(G_unproj, X=dropoff['lng'], Y=dropoff['lat'])

        if orig_node not in G_proj.nodes or dest_node not in G_proj.nodes:
            LOG.warning("orig/dest nodes not in composed projected graph; falling back.")
            return {'route_coords': [[pickup['lng'], pickup['lat']], [dropoff['lng'], dropoff['lat']]], 'eta_min': None, 'alt_routes': []}

        k = max(1, int(num_alternatives))
        gen = nx.shortest_simple_paths(G_simple, orig_node, dest_node, weight='weight')
        paths = []
        for p in gen:
            paths.append(p)
            if len(paths) >= k:
                break

        if not paths:
            LOG.warning("No paths found in composed graph")
            return {'route_coords': [[pickup['lng'], pickup['lat']], [dropoff['lng'], dropoff['lat']]], 'eta_min': None, 'alt_routes': []}

        def nodes_to_lonlat(nodes):
            coords = []
            for n in nodes:
                nd = G_unproj.nodes.get(n, {})
                lon = nd.get('x')
                lat = nd.get('y')
                coords.append([float(lon), float(lat)])
            return coords

        main_nodes = paths[0]
        route_coords = nodes_to_lonlat(main_nodes)
        total_m = 0.0
        eta_min = 0.0
        # compute stats using projected graph
        for u, v in zip(main_nodes[:-1], main_nodes[1:]):
            attr = _edge_first_data(G_proj, u, v)
            ln = attr.get('length', 0.0)
            try:
                ln = float(ln)
            except Exception:
                ln = 0.0
            total_m += ln
            sp = attr.get('speed_kph') or _parse_maxspeed(attr.get('maxspeed'))
            if sp is None:
                sp = 40.0
            try:
                sp = float(sp)
                if sp <= 0:
                    sp = 40.0
            except Exception:
                sp = 40.0
            if ln > 0:
                eta_min += (ln / 1000.0) / sp * 60.0

        alt_routes = []
        for alt_nodes in paths[1:]:
            alt_routes.append(nodes_to_lonlat(alt_nodes))

        return {'route_coords': route_coords, 'eta_min': float(eta_min), 'alt_routes': alt_routes}
    except Exception as exc:
        LOG.exception("Routing error on composed graph: %s", exc)
        return {'route_coords': [[pickup['lng'], pickup['lat']], [dropoff['lng'], dropoff['lat']]], 'eta_min': None, 'alt_routes': []}

# -------------------------
# Helpers reused from your original file (edge pick and multigraph->simple)
# -------------------------
def _parse_maxspeed(ms):
    if ms is None:
        return None
    if isinstance(ms, (int, float)):
        return float(ms)
    if isinstance(ms, (list, tuple)):
        ms = ms[0] if ms else None
    if not isinstance(ms, str):
        return None
    s = ms.lower().strip()
    m = __import__("re").search(r'(\d+(?:\.\d+)?)', s)
    if not m:
        return None
    val = float(m.group(1))
    if 'mph' in s:
        return val * 1.60934
    return val

def _edge_first_data(G, u, v):
    try:
        eds = G.get_edge_data(u, v)
        if not eds:
            return {}
        if isinstance(eds, dict):
            best = None
            best_len = float('inf')
            for k, attr in eds.items():
                ln = attr.get('length', None)
                try:
                    lnf = float(ln) if ln is not None else None
                except Exception:
                    lnf = None
                if lnf is not None and lnf < best_len:
                    best_len = lnf
                    best = attr
            if best is None:
                best = next(iter(eds.values()))
            return best if isinstance(best, dict) else {}
        return {}
    except Exception:
        return {}

def _multigraph_to_simple_digraph_min(G_multi, weight_key='length'):
    Gs = nx.DiGraph()
    Gs.add_nodes_from(G_multi.nodes(data=True))
    for u, v, key, data in G_multi.edges(keys=True, data=True):
        w = data.get(weight_key, None)
        try:
            wnum = float(w) if w is not None else None
        except Exception:
            wnum = None
        if wnum is None:
            wnum = 1.0
        if Gs.has_edge(u, v):
            if wnum < Gs[u][v].get('weight', float('inf')):
                Gs[u][v]['weight'] = wnum
                Gs[u][v]['orig_edge_sample'] = data
        else:
            Gs.add_edge(u, v, weight=wnum, orig_edge_sample=data)
    return Gs
