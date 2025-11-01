import osmnx as ox

proj_path = "data/ghana_drive_merged.graphml"
unproj_path = "data/ghana_drive_unprojected.graphml"

# Load your projected merged graph
G_proj = ox.load_graphml(proj_path)

# Reproject it to lat/lon WGS84
G_unproj = ox.project_graph(G_proj, to_crs="EPSG:4326")

# Save it
ox.save_graphml(G_unproj, unproj_path)

print("Recreated unprojected graph:", unproj_path)
