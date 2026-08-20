"""
Static NBA team reference data -- division/conference assignments and
approximate home-market coordinates, keyed by ESPN's numeric team_id (the
same id used as entity_id throughout this project). Not fetched from any
API: NBA's 6-division alignment has been stable since the 2004
Bobcats/Hornets realignment, and franchise relocations are rare enough
events to update this table by hand when they happen.

Coordinates are each team's home city/downtown arena, not exact
geolocation -- sufficient precision for a travel-distance feature at this
scale (see travel_distances_km). Distinct from nfl_teams.py's own
coordinates for shared markets where the arenas are in a different part
of the metro (Miami's Kaseya Center is downtown, the Dolphins' stadium is
in Miami Gardens; Washington's Capital One Arena is downtown DC, the
Commanders' stadium is in Landover, MD).
"""
from library.features import geo

# {team_id: "<Conference> <Division>"} -- ESPN team id -> abbreviation,
# for reference:
# 1 ATL, 2 BOS, 3 NO, 4 CHI, 5 CLE, 6 DAL, 7 DEN, 8 DET, 9 GS, 10 HOU,
# 11 IND, 12 LAC, 13 LAL, 14 MIA, 15 MIL, 16 MIN, 17 BKN, 18 NY, 19 ORL,
# 20 PHI, 21 PHX, 22 POR, 23 SAC, 24 SA, 25 OKC, 26 UTAH, 27 WSH, 28 TOR,
# 29 MEM, 30 CHA.
TEAM_DIVISIONS: dict[str, str] = {
    # Eastern Conference
    "2": "Eastern Atlantic", "17": "Eastern Atlantic", "18": "Eastern Atlantic",
    "20": "Eastern Atlantic", "28": "Eastern Atlantic",
    "4": "Eastern Central", "5": "Eastern Central", "8": "Eastern Central",
    "11": "Eastern Central", "15": "Eastern Central",
    "1": "Eastern Southeast", "30": "Eastern Southeast", "14": "Eastern Southeast",
    "19": "Eastern Southeast", "27": "Eastern Southeast",
    # Western Conference
    "7": "Western Northwest", "16": "Western Northwest", "25": "Western Northwest",
    "22": "Western Northwest", "26": "Western Northwest",
    "9": "Western Pacific", "12": "Western Pacific", "13": "Western Pacific",
    "21": "Western Pacific", "23": "Western Pacific",
    "6": "Western Southwest", "10": "Western Southwest", "29": "Western Southwest",
    "3": "Western Southwest", "24": "Western Southwest",
}


def is_real_franchise_matchup(event: dict) -> bool:
    """False for an event involving a non-franchise "team" -- an NBA
    All-Star Game roster (Team [Captain] vs Team [Captain], or a
    conference-vs-conference format depending on the season) isn't in
    TEAM_DIVISIONS, since it's an exhibition roster, not a real
    franchise."""
    return all(p.get("entity_id") in TEAM_DIVISIONS for p in event.get("participants", []))


# (latitude, longitude) of each team's home arena/downtown market.
TEAM_COORDINATES: dict[str, tuple[float, float]] = {
    "1": (33.7573, -84.3963),    # ATL -- Atlanta (State Farm Arena)
    "2": (42.3662, -71.0621),    # BOS -- Boston (TD Garden)
    "3": (29.9490, -90.0821),    # NO -- New Orleans (Smoothie King Center)
    "4": (41.8807, -87.6742),    # CHI -- Chicago (United Center)
    "5": (41.4965, -81.6882),    # CLE -- Cleveland (Rocket Arena)
    "6": (32.7905, -96.8103),    # DAL -- Dallas (American Airlines Center)
    "7": (39.7487, -105.0077),   # DEN -- Denver (Ball Arena)
    "8": (42.3410, -83.0550),    # DET -- Detroit (Little Caesars Arena)
    "9": (37.7680, -122.3877),   # GS -- San Francisco (Chase Center)
    "10": (29.7508, -95.3621),   # HOU -- Houston (Toyota Center)
    "11": (39.7640, -86.1555),   # IND -- Indianapolis (Gainbridge Fieldhouse)
    "12": (33.9535, -118.3392),  # LAC -- Inglewood (Intuit Dome)
    "13": (34.0430, -118.2673),  # LAL -- Los Angeles (Crypto.com Arena)
    "14": (25.7814, -80.1870),   # MIA -- Miami (Kaseya Center)
    "15": (43.0389, -87.9172),   # MIL -- Milwaukee (Fiserv Forum)
    "16": (44.9795, -93.2760),   # MIN -- Minneapolis (Target Center)
    "17": (40.6826, -73.9754),   # BKN -- Brooklyn (Barclays Center)
    "18": (40.7505, -73.9934),   # NY -- New York (Madison Square Garden)
    "19": (28.5392, -81.3839),   # ORL -- Orlando (Kia Center)
    "20": (39.9012, -75.1720),   # PHI -- Philadelphia (Wells Fargo Center)
    "21": (33.4457, -112.0712),  # PHX -- Phoenix (Footprint Center)
    "22": (45.5316, -122.6668),  # POR -- Portland (Moda Center)
    "23": (38.5802, -121.4997),  # SAC -- Sacramento (Golden 1 Center)
    "24": (29.4269, -98.4375),   # SA -- San Antonio (Frost Bank Center)
    "25": (35.4634, -97.5151),   # OKC -- Oklahoma City (Paycom Center)
    "26": (40.7683, -111.9011),  # UTAH -- Salt Lake City (Delta Center)
    "27": (38.8981, -77.0209),   # WSH -- Washington DC (Capital One Arena)
    "28": (43.6435, -79.3791),   # TOR -- Toronto (Scotiabank Arena)
    "29": (35.1382, -90.0505),   # MEM -- Memphis (FedExForum)
    "30": (35.2251, -80.8392),   # CHA -- Charlotte (Spectrum Center)
}

# Recurring NBA international/neutral-site game host cities. The
# designated home team isn't actually at their own market for these.
INTERNATIONAL_VENUES: dict[str, tuple[float, float]] = {
    "Mexico City": (19.4022, -99.0956),  # Arena CDMX / Palacio de los Deportes
    "Paris": (48.8397, 2.3775),          # Accor Arena
    "London": (51.5560, -0.2795),        # The O2 Arena
}


def is_divisional_game(home_id: str, away_id: str) -> bool | None:
    return geo.is_divisional_game(home_id, away_id, TEAM_DIVISIONS)


def is_international_game(venue_city: str | None) -> bool:
    return geo.is_international_game(venue_city, INTERNATIONAL_VENUES)


def travel_distances_km(
    away_id: str, home_id: str, venue_city: str | None
) -> tuple[float | None, float | None]:
    return geo.travel_distances_km(away_id, home_id, venue_city, TEAM_COORDINATES, INTERNATIONAL_VENUES)
