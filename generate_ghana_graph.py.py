import os
import time
import logging
from pathlib import Path

import osmnx as ox
import networkx as nx
from tqdm import tqdm

# -----------------------------
# Configure logging
# -----------------------------
logging.basicConfig(
    format="%(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)

# -----------------------------
# Config
# -----------------------------
DATA_DIR = Path.cwd() / "data"
DATA_DIR.mkdir(exist_ok=True)
MERGED_FILE = DATA_DIR / "ghana_drive_merged.graphml"

# administrative regions list (same as before)
REGIONS = [
    "Greater Accra Region, Ghana",
    "Ashanti Region, Ghana",
    "Central Region, Ghana",
    "Eastern Region, Ghana",
    "Western Region, Ghana",
    "Western North Region, Ghana",
    "Volta Region, Ghana",
    "Oti Region, Ghana",
    "Northern Region, Ghana",
    "Savannah Region, Ghana",
    "Upper East Region, Ghana",
    "Upper West Region, Ghana",
    "Bono Region, Ghana",
    "Bono East Region, Ghana",
    "Ahafo Region, Ghana",
    "North East Region, Ghana"
]

# Overpass endpoints to try (rotate on failure)
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass.osm.ch/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter"
]

# configuration for attempts
MAX_ATTEMPTS_PER_REGION = 5
INITIAL_BACKOFF_SECONDS = 5

# optional: set global OSMnx config (timeout increased)
ox.settings.timeout = 180
ox.settings.use_cache = True
ox.settings.log_console = False


def safe_name_for_file(region_name: str) -> str:
    """Make a safe filename from region name."""
    return region_name.replace(",", "").replace(" ", "_")


def download_region(region: str) -> bool:
    """
    Attempt to download and save a region graph.
    Returns True on success (file created or already exists), False on failure.
    """
    fname = DATA_DIR / f"{safe_name_for_file(region)}.graphml"
    if fname.exists():
        logging.info(f"[SKIP] {fname} already exists")
        return True

    backoff = INITIAL_BACKOFF_SECONDS
    attempt = 1
    # rotate endpoints across attempts to avoid single-endpoint outages
    while attempt <= MAX_ATTEMPTS_PER_REGION:
        endpoint = OVERPASS_ENDPOINTS[(attempt - 1) % len(OVERPASS_ENDPOINTS)]
        logging.info(f"[TRY] {region} — attempt {attempt} using endpoint {endpoint}")
        ox.settings.overpass_endpoint = endpoint
        ox.settings.timeout = 180
        try:
            G = ox.graph_from_place(region, network_type="drive", simplify=True)
            ox.save_graphml(G, fname)
            logging.info(f"[SAVED] {fname}")
            # be nice to Overpass API
            time.sleep(1.5)
            return True
        except Exception as e:
            logging.warning(f"[ERROR] {region} attempt {attempt} failed: {e}")
            attempt += 1
            time.sleep(backoff)
            backoff *= 2  # exponential backoff

    logging.error(f"[FAIL] All attempts failed for region: {region}")
    return False


def merge_region_files(save_intermediate_every: int = 4):
    """
    Load region files from disk, merge them incrementally and save the final merged graph.
    save_intermediate_every: number of regions to merge before writing an intermediate merged file to disk.
    """
    region_files = sorted(DATA_DIR.glob("*Region*Ghana.graphml"))
    if not region_files:
        logging.error("No region graphml files found to merge.")
        return False

    logging.info(f"[INFO] Found {len(region_files)} region files to merge.")
    merged_graph = None
    counter = 0
    for rf in tqdm(region_files, desc="Merging region files", ncols=100):
        try:
            G = ox.load_graphml(rf)
        except Exception as e:
            logging.warning(f"[WARN] Failed to load {rf}: {e}. Skipping.")
            continue

        if merged_graph is None:
            merged_graph = G
        else:
            # compose incrementally (keeps memory lower than composing all at once)
            merged_graph = nx.compose(merged_graph, G)

        counter += 1
        # optionally save an intermediate result every N merges to reduce rework
        if counter % save_intermediate_every == 0:
            inter_path = DATA_DIR / f"ghana_partial_merged_{counter}.graphml"
            try:
                ox.save_graphml(merged_graph, inter_path)
                logging.info(f"[SAVED] intermediate merged graph: {inter_path}")
            except Exception as e:
                logging.warning(f"[WARN] could not save intermediate graph: {e}")

    if merged_graph is None:
        logging.error("[FAILURE] Nothing merged.")
        return False

    # project final graph then save
    try:
        logging.info("[INFO] Projecting merged graph (for distances)...")
        merged_proj = ox.project_graph(merged_graph)
        ox.save_graphml(merged_proj, MERGED_FILE)
        logging.info(f"[SUCCESS] Merged Ghana graph saved: {MERGED_FILE}")
        return True
    except Exception as e:
        logging.error(f"[ERROR] Failed saving merged graph: {e}")
        return False


def main():
    logging.info("[START] Ghana region download/resume script")
    # download each region (skip existing)
    failed = []
    for region in REGIONS:
        ok = download_region(region)
        if not ok:
            failed.append(region)

    if failed:
        logging.warning(f"[COMPLETE] Some regions failed: {len(failed)}. You can re-run to retry them.")
        logging.warning("Failed regions: " + ", ".join(failed))

    # try merging whatever was successfully downloaded
    merged_ok = merge_region_files()
    if not merged_ok:
        logging.error("[DONE] Merge failed; re-run after you have more region files.")
    else:
        logging.info("[DONE] Merge complete.")

if __name__ == "__main__":
    main()
