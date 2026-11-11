"""
Approximate site data for college games, keyed by the 2-letter venue_state
on each event: a state's geographic centre and the standard-time UTC
offset of where most of its people live. Coarse (a state, not an arena),
used where no per-team coordinates exist.
"""

_EASTERN, _CENTRAL, _MOUNTAIN, _PACIFIC, _ALASKA, _HAWAII = -5, -6, -7, -8, -9, -10

# state: ((latitude, longitude), UTC offset)
_STATES: dict[str, tuple[tuple[float, float], int]] = {
    "AL": ((32.79, -86.83), _CENTRAL), "AK": ((64.73, -152.47), _ALASKA), "AZ": ((34.29, -111.66), _MOUNTAIN),
    "AR": ((34.90, -92.44), _CENTRAL), "CA": ((37.18, -119.47), _PACIFIC), "CO": ((38.99, -105.55), _MOUNTAIN),
    "CT": ((41.62, -72.73), _EASTERN), "DE": ((38.99, -75.51), _EASTERN), "DC": ((38.91, -77.02), _EASTERN),
    "FL": ((28.63, -82.45), _EASTERN), "GA": ((32.64, -83.44), _EASTERN), "HI": ((20.29, -156.37), _HAWAII),
    "ID": ((44.35, -114.61), _MOUNTAIN), "IL": ((40.04, -89.20), _CENTRAL), "IN": ((39.89, -86.28), _EASTERN),
    "IA": ((42.08, -93.50), _CENTRAL), "KS": ((38.49, -98.38), _CENTRAL), "KY": ((37.53, -85.30), _EASTERN),
    "LA": ((31.07, -92.00), _CENTRAL), "ME": ((45.37, -69.24), _EASTERN), "MD": ((39.06, -76.80), _EASTERN),
    "MA": ((42.26, -71.81), _EASTERN), "MI": ((44.35, -85.41), _EASTERN), "MN": ((46.28, -94.31), _CENTRAL),
    "MS": ((32.74, -89.67), _CENTRAL), "MO": ((38.36, -92.46), _CENTRAL), "MT": ((47.05, -109.63), _MOUNTAIN),
    "NE": ((41.54, -99.80), _CENTRAL), "NV": ((39.33, -116.63), _PACIFIC), "NH": ((43.68, -71.58), _EASTERN),
    "NJ": ((40.19, -74.67), _EASTERN), "NM": ((34.41, -106.11), _MOUNTAIN), "NY": ((42.95, -75.53), _EASTERN),
    "NC": ((35.56, -79.39), _EASTERN), "ND": ((47.45, -100.47), _CENTRAL), "OH": ((40.29, -82.79), _EASTERN),
    "OK": ((35.59, -97.49), _CENTRAL), "OR": ((43.93, -120.56), _PACIFIC), "PA": ((40.88, -77.80), _EASTERN),
    "RI": ((41.68, -71.56), _EASTERN), "SC": ((33.92, -80.90), _EASTERN), "SD": ((44.44, -100.23), _CENTRAL),
    "TN": ((35.86, -86.35), _CENTRAL), "TX": ((31.48, -99.33), _CENTRAL), "UT": ((39.31, -111.67), _MOUNTAIN),
    "VT": ((44.07, -72.67), _EASTERN), "VA": ((37.52, -78.85), _EASTERN), "WA": ((47.38, -120.45), _PACIFIC),
    "WV": ((38.64, -80.62), _EASTERN), "WI": ((44.62, -89.99), _CENTRAL), "WY": ((42.99, -107.55), _MOUNTAIN),
    "PR": ((18.22, -66.43), -4),
}


def coordinates(state: str | None) -> tuple[float, float] | None:
    site = _STATES.get((state or "").upper())
    return site[0] if site else None


def utc_offset(state: str | None) -> int | None:
    site = _STATES.get((state or "").upper())
    return site[1] if site else None
