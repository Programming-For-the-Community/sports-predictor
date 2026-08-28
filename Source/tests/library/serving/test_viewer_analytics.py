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
