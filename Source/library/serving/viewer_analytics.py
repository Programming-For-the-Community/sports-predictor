"""
Extracts CloudFront's viewer-location/device-type headers and logs them as
one structured JSON line per request -- the data source for the CloudWatch
dashboard built from Logs Insights queries across every sport's
predict-read log group (see Terraform/cloudwatch-dashboard-viewer-
analytics.tf). There's no separate CloudFront access-log delivery
pipeline for this; each predict-read Lambda's own CloudWatch log group
(created automatically by Lambda, no new delivery infrastructure) IS the
log source.

These headers arrive already forwarded, not something this module (or any
Terraform change) has to enable -- api-gateway-nfl-predict.tf's/
api-gateway-ncaafb-predict.tf's/api-gateway-nba-predict.tf's origin request
policy (AWS-managed AllViewerExceptHostHeader) documents that it forwards
"all device type and viewer location headers" alongside everything it was
already carrying for Cognito auth + the player-prop ?stat= query string --
confirmed against AWS's own docs for that policy, 2026-08-14.

City/region/postal code aren't available for every IP (AWS's own caveat,
some IPs can't be geolocated that specifically) -- logged as None rather
than omitted, so the field is always present and a "null rate" is visible
in the aggregated data rather than silently missing.
"""
import json
import logging


def log_viewer_analytics(logger: logging.Logger, sport: str, resource: str, method: str | None, headers: dict | None) -> None:
    """Never raises -- a logging failure here must not turn a working
    request into a 500. Call once per invocation, before routing, so every
    request is represented regardless of which route it hits or whether
    that route errors.

    `resource` + `method` together identify the API endpoint (e.g. GET
    /nfl/predictions/events/{event_id}) -- passed separately from headers
    since API Gateway carries the HTTP method as its own top-level event
    field (`httpMethod`), not a header."""
    try:
        # API Gateway's REST API proxy integration preserves header casing
        # as received; CloudFront always sends these in CloudFront-Viewer-*
        # / CloudFront-Is-*-Viewer form, but a case-insensitive lookup is
        # one line cheaper than trusting that never varies.
        lower_headers = {k.lower(): v for k, v in (headers or {}).items()}

        def h(name: str) -> str | None:
            return lower_headers.get(name.lower())

        logger.info("viewer_analytics %s", json.dumps({
            "sport": sport,
            "resource": resource,
            "method": method,
            "country": h("CloudFront-Viewer-Country"),
            "country_name": h("CloudFront-Viewer-Country-Name"),
            # 2-letter ISO 3166-2 subdivision code (e.g. "CA") -- distinct
            # from region_name's full name below. Used by the geo-widget
            # Lambda to match a count onto a state tile without a
            # name-to-abbreviation lookup.
            "region": h("CloudFront-Viewer-Country-Region"),
            "region_name": h("CloudFront-Viewer-Country-Region-Name"),
            "city": h("CloudFront-Viewer-City"),
            "is_mobile": h("CloudFront-Is-Mobile-Viewer"),
            "is_tablet": h("CloudFront-Is-Tablet-Viewer"),
            "is_desktop": h("CloudFront-Is-Desktop-Viewer"),
            "is_ios": h("CloudFront-Is-IOS-Viewer"),
            "is_android": h("CloudFront-Is-Android-Viewer"),
            "is_smarttv": h("CloudFront-Is-SmartTV-Viewer"),
            "user_agent": h("User-Agent"),
        }))
    except Exception:
        logger.warning("Failed to log viewer analytics", exc_info=True)
