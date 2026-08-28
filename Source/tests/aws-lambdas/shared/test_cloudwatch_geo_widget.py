import sys
from unittest.mock import MagicMock

handler = sys.modules["shared_cloudwatch_geo_widget"]


def _mock_logs_client(rows: list[dict]):
    client = MagicMock()
    client.start_query.return_value = {"queryId": "q-1"}
    client.get_query_results.return_value = {
        "status": "Complete",
        "results": [[{"field": k, "value": str(v)} for k, v in row.items()] for row in rows],
    }
    return client


def _mock_geo_maps_client(blob: bytes = b"fake-jpeg-bytes"):
    client = MagicMock()
    stream = MagicMock()
    stream.read.return_value = blob
    client.get_static_map.return_value = {"Blob": stream, "ContentType": "image/jpeg"}
    return client


class TestBucket:
    def test_zero_count_is_bucket_zero(self):
        assert handler._bucket(0, 100) == 0

    def test_zero_max_is_bucket_zero_even_with_a_positive_count(self):
        # Never divides by zero, regardless of how count/max_count end up here.
        assert handler._bucket(5, 0) == 0

    def test_highest_share_gets_the_top_bucket(self):
        assert handler._bucket(100, 100) == 4

    def test_a_small_share_gets_the_bottom_nonzero_bucket(self):
        assert handler._bucket(1, 100) == 1


class TestAcceptedCountsByState:
    def test_parses_logs_insights_rows_into_a_state_count_map(self):
        client = _mock_logs_client([{"region": "CA", "requests": 42}, {"region": "NY", "requests": 7}])
        counts = handler._accepted_counts_by_state(client, ["lg1", "lg2"], 0, 1000)
        assert counts == {"CA": 42, "NY": 7}

    def test_a_region_code_outside_the_known_states_is_dropped_not_misplaced(self):
        # A non-US or malformed region value must never silently land on
        # some unrelated state's marker.
        client = _mock_logs_client([{"region": "CA", "requests": 5}, {"region": "ON", "requests": 3}])
        counts = handler._accepted_counts_by_state(client, ["lg1"], 0, 1000)
        assert counts == {"CA": 5}

    def test_empty_results_produce_an_empty_map_not_an_error(self):
        client = _mock_logs_client([])
        assert handler._accepted_counts_by_state(client, ["lg1"], 0, 1000) == {}

    def test_passes_every_log_group_name_to_start_query_not_embedded_in_the_query_text(self):
        client = _mock_logs_client([])
        handler._accepted_counts_by_state(client, ["lg1", "lg2"], 0, 1000)
        call_kwargs = client.start_query.call_args.kwargs
        assert call_kwargs["logGroupNames"] == ["lg1", "lg2"]
        assert "SOURCE" not in call_kwargs["queryString"]


class TestBlockedCountsByCountry:
    def test_parses_logs_insights_rows_into_a_country_count_map(self):
        client = _mock_logs_client([{"c-country": "RU", "requests": 12}, {"c-country": "CN", "requests": 3}])
        counts = handler._blocked_counts_by_country(client, "cf-edge-logs", 0, 1000)
        assert counts == {"RU": 12, "CN": 3}

    def test_passes_the_single_log_group_name_to_start_query(self):
        client = _mock_logs_client([])
        handler._blocked_counts_by_country(client, "cf-edge-logs", 0, 1000)
        assert client.start_query.call_args.kwargs["logGroupNames"] == ["cf-edge-logs"]


class TestRunLogsInsightsQuery:
    def test_returns_empty_list_when_the_query_never_completes_in_time(self):
        client = MagicMock()
        client.start_query.return_value = {"queryId": "q-1"}
        client.get_query_results.return_value = {"status": "Running", "results": []}
        # max_wait_seconds=0 -- the poll loop must exit immediately rather
        # than hang, and must not raise.
        assert handler._run_logs_insights_query(client, ["lg"], "filter true", 0, 1000, max_wait_seconds=0) == []

    def test_returns_empty_list_on_a_failed_query_rather_than_raising(self):
        client = MagicMock()
        client.start_query.return_value = {"queryId": "q-1"}
        client.get_query_results.return_value = {"status": "Failed", "results": []}
        assert handler._run_logs_insights_query(client, ["lg"], "filter true", 0, 1000) == []


class TestAcceptedMapOverlay:
    def test_every_state_appears_as_a_labeled_point(self):
        overlay = handler._accepted_map_overlay({"CA": 9})
        for code in handler.US_STATE_CENTROIDS:
            assert f"label={code}" in overlay

    def test_is_pipe_delimited_point_terms(self):
        overlay = handler._accepted_map_overlay({})
        terms = overlay.split("|")
        assert len(terms) == len(handler.US_STATE_CENTROIDS)
        assert all(term.startswith("point:") for term in terms)

    def test_highest_count_state_gets_the_top_bucket_color(self):
        overlay = handler._accepted_map_overlay({"CA": 100, "NY": 1})
        assert f"label=CA;color={handler._BUCKET_COLORS[4]}" in overlay
        assert f"label=NY;color={handler._BUCKET_COLORS[1]}" in overlay


class TestBlockedMapOverlay:
    def test_known_country_codes_group_under_their_real_region(self):
        overlay = handler._blocked_map_overlay({"RU": 10, "BR": 2})
        assert "label=Europe" in overlay
        assert "label=Americas" in overlay

    def test_an_unmapped_country_code_is_dropped_from_the_map_not_misplaced(self):
        # "Other" has no real fixed location -- its own count is folded
        # into by_region but never gets a marker.
        overlay = handler._blocked_map_overlay({"XX": 4})
        assert "label=Other" not in overlay
        # Every known region still renders (at bucket 0 -- no real data).
        for region in handler.REGION_CENTROIDS:
            assert f"label={region}" in overlay

    def test_no_blocked_traffic_at_all_still_renders_every_region_at_bucket_zero(self):
        overlay = handler._blocked_map_overlay({})
        for region in handler.REGION_CENTROIDS:
            assert f"label={region};color={handler._BUCKET_COLORS[0]}" in overlay


class TestGetStaticMap:
    def test_requests_a_jpeg_composited_with_the_given_bounding_box_and_overlay(self):
        client = _mock_geo_maps_client(b"real-bytes")
        result = handler._get_static_map(client, "-125,24,-67,49", "point:1,2;label=CA;color=#fff")
        assert result == b"real-bytes"
        call_kwargs = client.get_static_map.call_args.kwargs
        assert call_kwargs["BoundingBox"] == "-125,24,-67,49"
        assert call_kwargs["CompactOverlay"] == "point:1,2;label=CA;color=#fff"
        assert call_kwargs["Style"] == "Standard"
        assert call_kwargs["ColorScheme"] == "Dark"


class TestMapHtml:
    def test_embeds_the_image_as_a_base64_img_tag(self):
        client = _mock_geo_maps_client(b"real-bytes")
        html = handler._map_html(client, "-125,24,-67,49", "overlay", {"CA": 5})
        assert "<img src=\"data:image/jpeg;base64," in html
        import base64
        assert base64.b64encode(b"real-bytes").decode("ascii") in html

    def test_falls_back_to_a_text_summary_instead_of_raising_when_get_static_map_fails(self):
        client = MagicMock()
        client.get_static_map.side_effect = RuntimeError("boom")
        html = handler._map_html(client, "-125,24,-67,49", "overlay", {"CA": 5, "TX": 2})
        assert "Map unavailable" in html
        assert "CA: 5" in html

    def test_falls_back_gracefully_with_no_data_either(self):
        client = MagicMock()
        client.get_static_map.side_effect = RuntimeError("boom")
        html = handler._map_html(client, "-125,24,-67,49", "overlay", {})
        assert "no data in this time range" in html


class TestLambdaHandler:
    def test_describe_event_returns_markdown_without_touching_aws(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr(handler.boto3, "client", called)
        result = handler.lambda_handler({"describe": True}, None)
        assert "Geo widget" in result
        called.assert_not_called()

    def test_accepted_mode_queries_every_configured_log_group_and_embeds_a_map(self, monkeypatch):
        logs_client = _mock_logs_client([{"region": "TX", "requests": 3}])
        geo_maps_client = _mock_geo_maps_client()

        def _client(service, **kwargs):
            return geo_maps_client if service == "geo-maps" else logs_client

        monkeypatch.setattr(handler.boto3, "client", _client)
        monkeypatch.setenv("ACCEPTED_LOG_GROUP_NAMES", "lg1,lg2")
        result = handler.lambda_handler({"mode": "accepted", "widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "<img src=\"data:image/jpeg;base64," in result
        assert logs_client.start_query.call_args.kwargs["logGroupNames"] == ["lg1", "lg2"]
        assert geo_maps_client.get_static_map.call_args.kwargs["BoundingBox"] == handler._CONUS_BOUNDING_BOX

    def test_blocked_mode_queries_the_configured_edge_log_group_in_us_east_1(self, monkeypatch):
        logs_client = _mock_logs_client([{"c-country": "CN", "requests": 6}])
        geo_maps_client = _mock_geo_maps_client()
        seen_kwargs = {}

        def _client(service, **kwargs):
            if service == "geo-maps":
                return geo_maps_client
            seen_kwargs.update(kwargs)
            return logs_client

        monkeypatch.setattr(handler.boto3, "client", _client)
        monkeypatch.setenv("BLOCKED_LOG_GROUP_NAME", "cf-edge-logs")
        result = handler.lambda_handler({"mode": "blocked", "widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "<img src=\"data:image/jpeg;base64," in result
        # The edge-access log group only ever exists in us-east-1
        # regardless of this Lambda's own region -- a client defaulting to
        # the Lambda's own region would silently find nothing.
        assert seen_kwargs.get("region_name") == "us-east-1"
        assert geo_maps_client.get_static_map.call_args.kwargs["BoundingBox"] == handler._WORLD_BOUNDING_BOX

    def test_missing_mode_defaults_to_accepted(self, monkeypatch):
        logs_client = _mock_logs_client([])
        geo_maps_client = _mock_geo_maps_client()
        monkeypatch.setattr(handler.boto3, "client", lambda service, **kwargs: geo_maps_client if service == "geo-maps" else logs_client)
        monkeypatch.setenv("ACCEPTED_LOG_GROUP_NAMES", "lg1")
        result = handler.lambda_handler({"widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "<img src=\"data:image/jpeg;base64," in result
        assert geo_maps_client.get_static_map.call_args.kwargs["BoundingBox"] == handler._CONUS_BOUNDING_BOX

    def test_a_get_static_map_failure_still_returns_a_usable_widget_not_an_exception(self, monkeypatch):
        logs_client = _mock_logs_client([{"region": "TX", "requests": 3}])
        geo_maps_client = MagicMock()
        geo_maps_client.get_static_map.side_effect = RuntimeError("boom")
        monkeypatch.setattr(handler.boto3, "client", lambda service, **kwargs: geo_maps_client if service == "geo-maps" else logs_client)
        monkeypatch.setenv("ACCEPTED_LOG_GROUP_NAMES", "lg1")
        result = handler.lambda_handler({"mode": "accepted", "widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "Map unavailable" in result
