"""
Static NFL team reference data -- division/conference assignments and
approximate home-market coordinates, keyed by ESPN's numeric team_id
(the same id used as entity_id throughout this project). Not fetched from
any API: the last NFL realignment was 2002, well before this project's
data window starts, so a fixed table is more robust than depending on an
ESPN endpoint for something that essentially never changes.

Coordinates are each team's home city/market, not exact stadium
geolocation -- sufficient precision for a travel-distance feature at
this scale (see travel_distances_km).

is_divisional_game/is_international_game/travel_distances_km below are
thin wrappers binding library.features.geo's sport-agnostic mechanism to
this module's own NFL-only data, keeping every existing caller's call
signature unchanged.
"""
from library.features import geo

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


def is_real_franchise_matchup(event: dict) -> bool:
    """False for events involving a non-franchise "team" -- the Pro Bowl's
    AFC/NFC all-star squads (ESPN ids 31/32) aren't in TEAM_DIVISIONS,
    since they're exhibition rosters, not real franchises. The Pro Bowl is
    fetched by the same postseason (season_type=3) ingest/backfill path as
    real playoff games, so nothing upstream already excludes it -- this is
    the filter feature engineering applies before building any rolling
    history, so a player's own Pro Bowl appearance (mixed roster, different
    rules, not representative of real performance) never leaks into their
    regular-season rolling stats or a team's Elo rating."""
    return all(p.get("entity_id") in TEAM_DIVISIONS for p in event.get("participants", []))


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

# Recurring NFL international-game host cities (2016-2025), keyed on the
# same venue_city string normalize.py already captures for every event.
# Neither team is at their own market for these -- the designated "home"
# team is just the scheduling/stats convention, not where anyone actually
# is.
INTERNATIONAL_VENUES: dict[str, tuple[float, float]] = {
    "London": (51.5560, -0.2795),        # Wembley / Tottenham Hotspur Stadium
    "Mexico City": (19.3029, -99.1505),  # Estadio Azteca
    "Munich": (48.2188, 11.6247),        # Allianz Arena
    "Frankfurt": (50.0686, 8.6455),      # Deutsche Bank Park
    "Sao Paulo": (-23.5449, -46.4741),   # Neo Química Arena
    "Berlin": (52.5147, 13.2394),        # Olympiastadion Berlin
    "Madrid": (40.4531, -3.6883),        # Santiago Bernabéu
    "Dublin": (53.3607, -6.2512),        # Croke Park
}


def is_divisional_game(home_id: str, away_id: str) -> bool | None:
    return geo.is_divisional_game(home_id, away_id, TEAM_DIVISIONS)


def is_international_game(venue_city: str | None) -> bool:
    return geo.is_international_game(venue_city, INTERNATIONAL_VENUES)


def travel_distances_km(
    away_id: str, home_id: str, venue_city: str | None
) -> tuple[float | None, float | None]:
    return geo.travel_distances_km(away_id, home_id, venue_city, TEAM_COORDINATES, INTERNATIONAL_VENUES)
