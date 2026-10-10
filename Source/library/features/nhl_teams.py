"""
Static NHL team reference data -- division/conference alignment,
home-arena coordinates and UTC offsets, keyed by ESPN's team_id (the same
id used as entity_id throughout this project). Not fetched from any API.

Alignment is season-aware (ESPN labels a season by its ending year):
- 2021 used four temporary divisions and no conferences.
- Arizona played in the Pacific through 2020 and the Central from 2022.
- Arizona ("24") became Utah ("129764") for 2025 under a new ESPN id.
"""
from library.features import geo

# ESPN team id -> abbreviation, for reference:
# 1 BOS, 2 BUF, 3 CGY, 4 CHI, 5 DET, 6 EDM, 7 CAR, 8 LA, 9 DAL, 10 MTL,
# 11 NJ, 12 NYI, 13 NYR, 14 OTT, 15 PHI, 16 PIT, 17 COL, 18 SJ, 19 STL,
# 20 TB, 21 TOR, 22 VAN, 23 WSH, 24 ARI, 25 ANA, 26 FLA, 27 NSH, 28 WPG,
# 29 CBJ, 30 MIN, 37 VGK, 124292 SEA, 129764 UTAH.
ARIZONA_TEAM_ID = "24"
UTAH_TEAM_ID = "129764"

_EASTERN_ATLANTIC = "Eastern Atlantic"
_EASTERN_METROPOLITAN = "Eastern Metropolitan"
_WESTERN_CENTRAL = "Western Central"
_WESTERN_PACIFIC = "Western Pacific"

TEAM_DIVISIONS: dict[str, str] = {
    "1": _EASTERN_ATLANTIC, "2": _EASTERN_ATLANTIC, "5": _EASTERN_ATLANTIC, "26": _EASTERN_ATLANTIC,
    "10": _EASTERN_ATLANTIC, "14": _EASTERN_ATLANTIC, "20": _EASTERN_ATLANTIC, "21": _EASTERN_ATLANTIC,
    "7": _EASTERN_METROPOLITAN, "29": _EASTERN_METROPOLITAN, "11": _EASTERN_METROPOLITAN, "12": _EASTERN_METROPOLITAN,
    "13": _EASTERN_METROPOLITAN, "15": _EASTERN_METROPOLITAN, "16": _EASTERN_METROPOLITAN, "23": _EASTERN_METROPOLITAN,
    "4": _WESTERN_CENTRAL, "17": _WESTERN_CENTRAL, "9": _WESTERN_CENTRAL, "30": _WESTERN_CENTRAL,
    "27": _WESTERN_CENTRAL, "19": _WESTERN_CENTRAL, "28": _WESTERN_CENTRAL,
    ARIZONA_TEAM_ID: _WESTERN_CENTRAL, UTAH_TEAM_ID: _WESTERN_CENTRAL,
    "25": _WESTERN_PACIFIC, "3": _WESTERN_PACIFIC, "6": _WESTERN_PACIFIC, "8": _WESTERN_PACIFIC,
    "18": _WESTERN_PACIFIC, "124292": _WESTERN_PACIFIC, "22": _WESTERN_PACIFIC, "37": _WESTERN_PACIFIC,
}

_TEMPORARY_2021_SEASON = 2021
_NORTH_2021 = "2021 North"
_EAST_2021 = "2021 East"
_CENTRAL_2021 = "2021 Central"
_WEST_2021 = "2021 West"

_TEAM_DIVISIONS_2021: dict[str, str] = {
    "3": _NORTH_2021, "6": _NORTH_2021, "10": _NORTH_2021, "14": _NORTH_2021,
    "21": _NORTH_2021, "22": _NORTH_2021, "28": _NORTH_2021,
    "1": _EAST_2021, "2": _EAST_2021, "11": _EAST_2021, "12": _EAST_2021,
    "13": _EAST_2021, "15": _EAST_2021, "16": _EAST_2021, "23": _EAST_2021,
    "7": _CENTRAL_2021, "4": _CENTRAL_2021, "29": _CENTRAL_2021, "9": _CENTRAL_2021,
    "5": _CENTRAL_2021, "26": _CENTRAL_2021, "27": _CENTRAL_2021, "20": _CENTRAL_2021,
    "25": _WEST_2021, ARIZONA_TEAM_ID: _WEST_2021, "17": _WEST_2021, "8": _WEST_2021,
    "30": _WEST_2021, "18": _WEST_2021, "19": _WEST_2021, "37": _WEST_2021,
}

_ARIZONA_LAST_PACIFIC_SEASON = 2020

# A relocated franchise's old ESPN id -> its current one.
FRANCHISE_SUCCESSORS: dict[str, str] = {ARIZONA_TEAM_ID: UTAH_TEAM_ID}


def franchise_id(team_id: str) -> str:
    """The current ESPN id of team_id's franchise, so a relocated team's
    history carries across its id change."""
    return FRANCHISE_SUCCESSORS.get(team_id, team_id)


def is_franchise_team(team_id: str | None) -> bool:
    """False for an All-Star or international-tournament roster."""
    return team_id in TEAM_DIVISIONS


def is_real_franchise_matchup(event: dict) -> bool:
    return all(is_franchise_team(p.get("entity_id")) for p in event.get("participants", []))


def _divisions_for_season(season: int | None) -> dict[str, str]:
    if season == _TEMPORARY_2021_SEASON:
        return _TEAM_DIVISIONS_2021
    if season is not None and season <= _ARIZONA_LAST_PACIFIC_SEASON:
        return {**TEAM_DIVISIONS, ARIZONA_TEAM_ID: _WESTERN_PACIFIC}
    return TEAM_DIVISIONS


def team_division(team_id: str, season: int | None = None) -> str | None:
    """season=None means the current alignment."""
    return _divisions_for_season(season).get(team_id)


def is_divisional_game(home_id: str, away_id: str, season: int | None = None) -> bool | None:
    return geo.is_divisional_game(home_id, away_id, _divisions_for_season(season))


def is_conference_game(home_id: str, away_id: str, season: int | None = None) -> bool | None:
    """None when either team is unknown, or for 2021 (no conferences)."""
    if season == _TEMPORARY_2021_SEASON:
        return None
    home_division = team_division(home_id, season)
    away_division = team_division(away_id, season)
    if home_division is None or away_division is None:
        return None
    return home_division.split(" ", 1)[0] == away_division.split(" ", 1)[0]


# (latitude, longitude) of each team's home arena.
TEAM_COORDINATES: dict[str, tuple[float, float]] = {
    "1": (42.3662, -71.0621),         # BOS -- TD Garden
    "2": (42.8750, -78.8764),         # BUF -- KeyBank Center
    "3": (51.0374, -114.0519),        # CGY -- Scotiabank Saddledome
    "4": (41.8807, -87.6742),         # CHI -- United Center
    "5": (42.3410, -83.0550),         # DET -- Little Caesars Arena
    "6": (53.5469, -113.4979),        # EDM -- Rogers Place
    "7": (35.8033, -78.7220),         # CAR -- Lenovo Center (Raleigh)
    "8": (34.0430, -118.2673),        # LA -- Crypto.com Arena
    "9": (32.7905, -96.8103),         # DAL -- American Airlines Center
    "10": (45.4961, -73.5693),        # MTL -- Bell Centre
    "11": (40.7335, -74.1711),        # NJ -- Prudential Center (Newark)
    "12": (40.7117, -73.7260),        # NYI -- UBS Arena (Elmont)
    "13": (40.7505, -73.9934),        # NYR -- Madison Square Garden
    "14": (45.2969, -75.9272),        # OTT -- Canadian Tire Centre (Kanata)
    "15": (39.9012, -75.1720),        # PHI -- Wells Fargo Center
    "16": (40.4395, -79.9892),        # PIT -- PPG Paints Arena
    "17": (39.7487, -105.0077),       # COL -- Ball Arena (Denver)
    "18": (37.3328, -121.9012),       # SJ -- SAP Center
    "19": (38.6268, -90.2026),        # STL -- Enterprise Center
    "20": (27.9427, -82.4518),        # TB -- Amalie Arena (Tampa)
    "21": (43.6435, -79.3791),        # TOR -- Scotiabank Arena
    "22": (49.2778, -123.1089),       # VAN -- Rogers Arena
    "23": (38.8981, -77.0209),        # WSH -- Capital One Arena
    ARIZONA_TEAM_ID: (33.5319, -112.2611),  # ARI -- Glendale
    "25": (33.8078, -117.8765),       # ANA -- Honda Center
    "26": (26.1584, -80.3256),        # FLA -- Amerant Bank Arena (Sunrise)
    "27": (36.1592, -86.7785),        # NSH -- Bridgestone Arena
    "28": (49.8927, -97.1436),        # WPG -- Canada Life Centre
    "29": (39.9693, -83.0061),        # CBJ -- Nationwide Arena
    "30": (44.9448, -93.1010),        # MIN -- Xcel Energy Center (St. Paul)
    "37": (36.1029, -115.1785),       # VGK -- T-Mobile Arena
    "124292": (47.6221, -122.3540),   # SEA -- Climate Pledge Arena
    UTAH_TEAM_ID: (40.7683, -111.9011),  # UTAH -- Delta Center
}

# Standard-time UTC offset (hours) of each team's home market.
_EASTERN, _CENTRAL, _MOUNTAIN, _PACIFIC = -5, -6, -7, -8

TEAM_UTC_OFFSETS: dict[str, int] = {
    "1": _EASTERN, "2": _EASTERN, "5": _EASTERN, "7": _EASTERN, "10": _EASTERN, "11": _EASTERN,
    "12": _EASTERN, "13": _EASTERN, "14": _EASTERN, "15": _EASTERN, "16": _EASTERN, "20": _EASTERN,
    "21": _EASTERN, "23": _EASTERN, "26": _EASTERN, "29": _EASTERN,
    "4": _CENTRAL, "9": _CENTRAL, "19": _CENTRAL, "27": _CENTRAL, "28": _CENTRAL, "30": _CENTRAL,
    "3": _MOUNTAIN, "6": _MOUNTAIN, "17": _MOUNTAIN, ARIZONA_TEAM_ID: _MOUNTAIN, UTAH_TEAM_ID: _MOUNTAIN,
    "8": _PACIFIC, "18": _PACIFIC, "22": _PACIFIC, "25": _PACIFIC, "37": _PACIFIC, "124292": _PACIFIC,
}

# Recurring NHL Global Series host cities. The designated home team isn't
# at its own market for these.
INTERNATIONAL_VENUES: dict[str, tuple[float, float]] = {
    "Stockholm": (59.2936, 18.0832),  # Avicii Arena
    "Prague": (50.1047, 14.4936),     # O2 Arena
    "Helsinki": (60.2056, 24.9290),   # Helsinki Halli
    "Tampere": (61.5040, 23.7780),    # Nokia Arena
}


def is_international_game(venue_city: str | None) -> bool:
    return geo.is_international_game(venue_city, INTERNATIONAL_VENUES)


def travel_distances_km(away_id: str, home_id: str, venue_city: str | None) -> tuple[float | None, float | None]:
    return geo.travel_distances_km(away_id, home_id, venue_city, TEAM_COORDINATES, INTERNATIONAL_VENUES)


def timezone_shift_hours(from_team_id: str, to_team_id: str) -> int | None:
    """Hours gained (positive, travelling east) or lost going from one
    team's home market to another's."""
    origin = TEAM_UTC_OFFSETS.get(from_team_id)
    destination = TEAM_UTC_OFFSETS.get(to_team_id)
    if origin is None or destination is None:
        return None
    return destination - origin
