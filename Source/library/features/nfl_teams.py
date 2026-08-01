"""
Static NFL team reference data -- division/conference assignments and
approximate home-market coordinates, keyed by ESPN's numeric team_id
(the same id used as entity_id throughout this project). Not fetched from
any API: the last NFL realignment was 2002, well before this project's
data window starts, so a fixed table is more robust than depending on an
ESPN endpoint for something that essentially never changes.

Coordinates are each team's home city/market, not exact stadium
geolocation -- sufficient precision for a travel-distance feature at
this scale (see travel_distance_km).
"""
import math

TEAM_DIVISIONS: dict[str, str] = {
    "2": "AFC East", "15": "AFC East", "17": "AFC East", "20": "AFC East",
    "33": "AFC North", "4": "AFC North", "5": "AFC North", "23": "AFC North",
    "34": "AFC South", "11": "AFC South", "30": "AFC South", "10": "AFC South",
    "7": "AFC West", "12": "AFC West", "13": "AFC West", "24": "AFC West",
    "6": "NFC East", "19": "NFC East", "21": "NFC East", "28": "NFC East",
    "3": "NFC North", "8": "NFC North", "9": "NFC North", "16": "NFC North",
    "1": "NFC South", "29": "NFC South", "18": "NFC South", "27": "NFC South",
    "22": "NFC West", "14": "NFC West", "25": "NFC West", "26": "NFC West",
}

# (latitude, longitude) of each team's home market.
TEAM_COORDINATES: dict[str, tuple[float, float]] = {
    "22": (33.5276, -112.2626),  # ARI -- Glendale
    "1": (33.7490, -84.3880),    # ATL -- Atlanta
    "33": (39.2904, -76.6122),   # BAL -- Baltimore
    "2": (42.8864, -78.8784),    # BUF -- Buffalo
    "29": (35.2271, -80.8431),   # CAR -- Charlotte
    "3": (41.8781, -87.6298),    # CHI -- Chicago
    "4": (39.1031, -84.5120),    # CIN -- Cincinnati
    "5": (41.4993, -81.6944),    # CLE -- Cleveland
    "6": (32.7473, -97.0945),    # DAL -- Arlington
    "7": (39.7392, -104.9903),   # DEN -- Denver
    "8": (42.3314, -83.0458),    # DET -- Detroit
    "9": (44.5133, -88.0133),    # GB -- Green Bay
    "34": (29.7604, -95.3698),   # HOU -- Houston
    "11": (39.7684, -86.1581),   # IND -- Indianapolis
    "30": (30.3322, -81.6557),   # JAX -- Jacksonville
    "12": (39.0997, -94.5786),   # KC -- Kansas City
    "13": (36.1699, -115.1398),  # LV -- Las Vegas
    "24": (33.9535, -118.3392),  # LAC -- Inglewood
    "14": (33.9535, -118.3392),  # LAR -- Inglewood
    "15": (25.9580, -80.2389),   # MIA -- Miami Gardens
    "16": (44.9778, -93.2650),   # MIN -- Minneapolis
    "17": (42.0909, -71.2643),   # NE -- Foxborough
    "18": (29.9511, -90.0715),   # NO -- New Orleans
    "19": (40.8135, -74.0745),   # NYG -- East Rutherford
    "20": (40.8135, -74.0745),   # NYJ -- East Rutherford
    "21": (39.9526, -75.1652),   # PHI -- Philadelphia
    "23": (40.4406, -79.9959),   # PIT -- Pittsburgh
    "25": (37.4030, -121.9700),  # SF -- Santa Clara
    "26": (47.6062, -122.3321),  # SEA -- Seattle
    "27": (27.9506, -82.4572),   # TB -- Tampa
    "10": (36.1627, -86.7816),   # TEN -- Nashville
    "28": (38.9077, -76.8645),   # WSH -- Landover
}


def _haversine_km(coord1: tuple[float, float], coord2: tuple[float, float]) -> float:
    earth_radius_km = 6371.0
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def is_divisional_game(home_id: str, away_id: str) -> bool | None:
    """None if either team's division isn't known, rather than defaulting
    to False -- an unknown division shouldn't silently read as "not
    divisional"."""
    home_division = TEAM_DIVISIONS.get(home_id)
    away_division = TEAM_DIVISIONS.get(away_id)
    if home_division is None or away_division is None:
        return None
    return home_division == away_division


def travel_distance_km(away_id: str, home_id: str) -> float | None:
    """Approximate distance the away team travels for this game, between
    each team's home-market coordinates rather than the exact venue --
    the home team is always treated as playing at their own market (0 km
    of travel), which is wrong for the handful of neutral-site/
    international games per season but otherwise accurate.
    """
    away_coords = TEAM_COORDINATES.get(away_id)
    home_coords = TEAM_COORDINATES.get(home_id)
    if away_coords is None or home_coords is None:
        return None
    return _haversine_km(away_coords, home_coords)
