"""Utility geospasial sederhana: jarak haversine antar dua titik lat/lon."""
import math


def haversine_km(lat1, lon1, lat2, lon2):
    """Jarak great-circle antara dua titik (derajat) dalam kilometer."""
    r = 6371.0088  # radius bumi rata-rata (km)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_city(lat, lon, cities):
    """Kembalikan (city_dict, distance_km) kota terdekat dari titik (lat, lon)."""
    best_city, best_dist = None, float("inf")
    for city in cities:
        d = haversine_km(lat, lon, city["latitude"], city["longitude"])
        if d < best_dist:
            best_city, best_dist = city, d
    return best_city, best_dist
