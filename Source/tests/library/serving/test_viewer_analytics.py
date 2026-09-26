import json
import logging

from library.serving.viewer_analytics import log_viewer_analytics


def test_logs_region_alongside_region_name(caplog):
    with caplog.at_level(logging.INFO):
        log_viewer_analytics(
            logging.getLogger("test"), "pga", "/pga/events", "GET",
            {
                "CloudFront-Viewer-Country": "US",
                "CloudFront-Viewer-Country-Name": "United States",
                "CloudFront-Viewer-Country-Region": "CA",
                "CloudFront-Viewer-Country-Region-Name": "California",
            },
        )
    payload = json.loads(caplog.records[0].message.split("viewer_analytics ", 1)[1])
    assert payload["region"] == "CA"
    assert payload["region_name"] == "California"


def test_region_is_none_when_the_ip_cannot_be_resolved_that_specifically(caplog):
    with caplog.at_level(logging.INFO):
        log_viewer_analytics(logging.getLogger("test"), "pga", "/pga/events", "GET", {"CloudFront-Viewer-Country": "US"})
    payload = json.loads(caplog.records[0].message.split("viewer_analytics ", 1)[1])
    assert payload["region"] is None


def test_never_raises_even_with_no_headers_at_all(caplog):
    with caplog.at_level(logging.INFO):
        log_viewer_analytics(logging.getLogger("test"), "pga", "/pga/events", "GET", None)
    payload = json.loads(caplog.records[0].message.split("viewer_analytics ", 1)[1])
    assert payload["region"] is None


def _payload(caplog):
    return json.loads(caplog.records[0].message.split("viewer_analytics ", 1)[1])


def test_logs_the_signed_in_user_and_concrete_path(caplog):
    context = {"authorizer": {"claims": {"sub": "abc-123", "cognito:username": "chamar"}}}
    with caplog.at_level(logging.INFO):
        log_viewer_analytics(
            logging.getLogger("test"), "nfl", "/nfl/predictions/events/{event_id}", "GET", {},
            request_context=context, path="/nfl/predictions/events/401547417",
        )
    payload = _payload(caplog)
    assert payload["user_id"] == "abc-123"
    assert payload["username"] == "chamar"
    assert payload["path"] == "/nfl/predictions/events/401547417"


def test_falls_back_to_the_access_token_username_claim(caplog):
    context = {"authorizer": {"claims": {"sub": "abc-123", "username": "chamar"}}}
    with caplog.at_level(logging.INFO):
        log_viewer_analytics(logging.getLogger("test"), "nfl", "/nfl/events", "GET", {}, request_context=context)
    assert _payload(caplog)["username"] == "chamar"


def test_user_is_none_when_there_is_no_authorizer_context(caplog):
    with caplog.at_level(logging.INFO):
        log_viewer_analytics(logging.getLogger("test"), "nfl", "/nfl/events", "GET", {}, request_context={})
    payload = _payload(caplog)
    assert payload["user_id"] is None
    assert payload["username"] is None
