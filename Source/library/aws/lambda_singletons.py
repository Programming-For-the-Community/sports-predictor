"""
Shared lazy-singleton-getter and warmup-ping boilerplate for predict/
handler.py's own module-level singletons (FeatureStorage/S3Manager/
DynamoDBTable), identical across all 6 sports.
"""


def get_or_create(namespace: dict, attr_name: str, factory):
    """namespace is the caller's own globals() -- reads/writes attr_name
    directly in the caller's module dict, which is exactly what module-
    level attribute access (handler._storage) resolves to, so an existing
    external reset (a test fixture doing `handler._storage = None`) stays
    visible here and vice versa, rather than this holding its own
    closure-private cached value the reset can't reach."""
    if namespace.get(attr_name) is None:
        namespace[attr_name] = factory()
    return namespace[attr_name]


def warm(*getters) -> dict:
    """EventBridge Scheduler warmup-ping response -- calls each of the
    caller's own singleton getters once (keeping a container past its own
    slow cold-start import chain) and returns the shared {"status": "warm"}
    shape every sport's handler.py already returned for this branch."""
    for getter in getters:
        getter()
    return {"status": "warm"}
