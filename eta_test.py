from graphml import get_route_on_roads

pickup = {"lat": 5.6037, "lng": -0.1870}   # Accra
dropoff = {"lat": 5.5600, "lng": -0.2050}  # nearby point

result = get_route_on_roads(pickup, dropoff)
print(result["eta_min"], "minutes")
print("First 5 coords:", result["route_coords"][:5])
