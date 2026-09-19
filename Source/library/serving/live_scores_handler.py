"""
Shared `lambda_handler` body for every sport's live-scores cache Lambda --
confirmed byte-for-byte identical shape across all 6 sports (nfl/nba/
ncaafb/ncaambb/pga/f1) except SPORT, the ESPN/Jolpica client class, and
(F1 only) an extra `season` argument passed to `refresh`.

Each per-sport handler.py still owns its own `_get_storage`/`_s3`/
`_response`/`_CORS_HEADERS` (small, and already covered by
library.aws.lambda_singletons for the singleton-getter part) -- only the
routing/dispatch body below is shared. `live_scores_module` is passed as
the actual per-sport `live_scores` module object (not a specific function
reference) so `patch.object(live_scores, "refresh", ...)` in tests --
which mutates that module's own __dict__ -- is still visible here: this
resolves `.refresh`/`.get_live_scores` via attribute lookup at CALL time,
same as every per-sport handler.py's own prior `live_scores.refresh(...)`
call did.
"""


def make_lambda_handler(
    sport: str, client_factory, get_storage, s3, raw_bucket: str, live_scores_module, logger, response_fn,
    refresh_extra_args=lambda: (),
):
    resource = f"/{sport}/live-scores"

    def lambda_handler(event, context):
        if event.get("detail-type") == "LiveScoreRefresh":
            return live_scores_module.refresh(
                get_storage(), s3, raw_bucket, client_factory(), sport, *refresh_extra_args(),
            )

        event_resource = event.get("resource", "")

        try:
            if event_resource == resource:
                return response_fn(200, live_scores_module.get_live_scores(s3, raw_bucket))

            return response_fn(404, {"error": f"No route for resource {event_resource!r}"})

        except Exception:
            logger.exception("Unhandled error serving %s", event_resource)
            return response_fn(500, {"error": "Internal server error"})

    return lambda_handler
