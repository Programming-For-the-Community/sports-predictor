"""
Sport-agnostic divisional/travel mechanism, extracted out of
library.features.nfl_teams -- the lookups themselves (haversine distance,
"is this venue in the international list", "do these two team_ids share a
division") don't depend on which sport's teams/divisions/venues are being
looked up, only the data dicts do. Each sport's own module (e.g. nfl_teams.py)
supplies its own TEAM_DIVISIONS/TEAM_COORDINATES/INTERNATIONAL_VENUES-shaped
dicts and binds them to these functions, so the mechanism is written once
instead of once per head-to-head sport.
"""
import math


def haversine_km(coord1: tuple[float, float], coord2: tuple[float, float]) -> float:
    earth_radius_km = 6371.0
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def is_divisional_game(home_id: str, away_id: str, team_divisions: dict[str, str]) -> bool | None:
    """None if either team's division isn't known, rather than defaulting
    to False -- an unknown division shouldn't silently read as "not
    divisional"."""
    home_division = team_divisions.get(home_id)
    away_division = team_divisions.get(away_id)
    if home_division is None or away_division is None:
        return None
    return home_division == away_division


def is_international_game(venue_city: str | None, international_venues: dict[str, tuple[float, float]]) -> bool:
    return venue_city in international_venues


def travel_distances_km(
    away_id: str,
    home_id: str,
    venue_city: str | None,
    team_coordinates: dict[str, tuple[float, float]],
    international_venues: dict[str, tuple[float, float]],
) -> tuple[float | None, float | None]:
    """Returns (home_travel_km, away_travel_km) -- the distance each team
    travels from their own home market to the actual game site. For an
    ordinary game the site IS the home team's market, so home travel is
    always 0 and away travel is the distance between the two teams'
    markets. For a game at one of international_venues, neither team is
    at their own market, so both get a real distance computed from that
    venue instead.
    """
    away_coords = team_coordinates.get(away_id)
    home_coords = team_coordinates.get(home_id)
    if away_coords is None or home_coords is None:
        return None, None

    venue_coords = international_venues.get(venue_city) if venue_city else None
    if venue_coords is not None:
        return haversine_km(home_coords, venue_coords), haversine_km(away_coords, venue_coords)
    return 0.0, haversine_km(away_coords, home_coords)
