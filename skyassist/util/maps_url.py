import math


def maps_pin_url(lat: float, lon: float) -> str:
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("coordinates must be finite")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordinates out of range")
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f}%2C{lon:.6f}"
