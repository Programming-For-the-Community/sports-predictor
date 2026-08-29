import json
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
        # some unrelated state's division.
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


class TestStateDivision:
    def test_every_state_centroid_maps_to_exactly_one_division(self):
        assert set(handler.STATE_DIVISION.keys()) == set(handler.US_STATE_CENTROIDS.keys())
        assert set(handler.STATE_DIVISION.values()) == set(handler.DIVISION_CENTROIDS.keys())

    def test_pacific_centroid_stays_within_the_conus_view_despite_ak_hi_membership(self):
        # AK/HI belong to Pacific for count-grouping purposes, but the
        # plotted centroid must stay near the CONUS Pacific states so it
        # doesn't drift off the CONUS-only map view.
        lon, lat = handler.DIVISION_CENTROIDS["Pacific"]
        west, south, east, north = (float(v) for v in handler._CONUS_BOUNDING_BOX.split(","))
        assert west < lon < east
        assert south < lat < north


class TestAcceptedHotspots:
    def test_groups_state_counts_into_their_census_division(self):
        counts = {"CA": 10, "OR": 5}  # both Pacific
        hotspots = handler._accepted_hotspots(counts)
        assert len(hotspots) == 1
        assert hotspots[0][:2] == handler.DIVISION_CENTROIDS["Pacific"]

    def test_zero_traffic_divisions_produce_no_hotspot(self):
        hotspots = handler._accepted_hotspots({"CA": 10})
        assert len(hotspots) == 1

    def test_no_traffic_anywhere_produces_no_hotspots(self):
        assert handler._accepted_hotspots({}) == []

    def test_an_unmapped_state_code_is_dropped_not_misplaced(self):
        hotspots = handler._accepted_hotspots({"ZZ": 100})
        assert hotspots == []


class TestBlockedHotspots:
    def test_known_country_codes_group_under_their_real_region(self):
        hotspots = handler._blocked_hotspots({"RU": 10, "BR": 2})
        assert len(hotspots) == 2

    def test_an_unmapped_country_code_produces_no_hotspot(self):
        # "Other" has no real fixed location -- its own count is folded
        # into by_region but never gets a glow.
        assert handler._blocked_hotspots({"XX": 4}) == []

    def test_no_blocked_traffic_at_all_produces_no_hotspots(self):
        assert handler._blocked_hotspots({}) == []


class TestGlowFeatures:
    def test_higher_bucket_gets_a_hotter_color(self):
        low = handler._glow_features(-100.0, 40.0, 1, 5.0, 2)
        high = handler._glow_features(-100.0, 40.0, 4, 5.0, 2)
        assert low[0]["properties"]["color"][:7] == handler._HEAT_COLORS[0]
        assert high[0]["properties"]["color"][:7] == handler._HEAT_COLORS[3]

    def test_rings_shrink_from_outer_to_inner(self):
        features = handler._glow_features(-100.0, 40.0, 4, 10.0, 3)
        assert len(features) == 3

        def _max_x(feature):
            return max(pt[0] for pt in feature["geometry"]["coordinates"][0])

        radii = [_max_x(f) for f in features]
        assert radii == sorted(radii, reverse=True)  # outermost (index 0) is largest

    def test_alpha_increases_from_outer_to_inner(self):
        features = handler._glow_features(-100.0, 40.0, 4, 10.0, 3)
        alphas = [int(f["properties"]["color"][7:], 16) for f in features]
        assert alphas == sorted(alphas)

    def test_every_ring_is_a_closed_polygon(self):
        for feature in handler._glow_features(-100.0, 40.0, 2, 5.0, 2):
            ring = feature["geometry"]["coordinates"][0]
            assert ring[0] == ring[-1]
            assert len(ring) == handler._RING_SIDES + 1


class TestAcceptedMapOverlay:
    def test_is_a_valid_geojson_feature_collection_of_polygons_only(self):
        overlay = json.loads(handler._accepted_map_overlay({"CA": 9}))
        assert overlay["type"] == "FeatureCollection"
        assert overlay["features"]
        for feature in overlay["features"]:
            assert feature["geometry"]["type"] == "Polygon"
            assert "label" not in feature["properties"]

    def test_no_traffic_anywhere_produces_an_empty_feature_collection(self):
        overlay = json.loads(handler._accepted_map_overlay({}))
        assert overlay["features"] == []

    def test_every_division_active_stays_within_the_geojson_overlay_length_limit(self):
        counts = {code: (i + 1) * 137 for i, code in enumerate(handler.US_STATE_CENTROIDS)}
        overlay = handler._accepted_map_overlay(counts)
        assert len(overlay) <= 4200


class TestBlockedMapOverlay:
    def test_no_blocked_traffic_at_all_produces_an_empty_feature_collection(self):
        overlay = json.loads(handler._blocked_map_overlay({}))
        assert overlay["features"] == []

    def test_every_region_active_stays_within_the_geojson_overlay_length_limit(self):
        by_country = {"RU": 500, "BR": 400, "CN": 300, "AU": 200, "NG": 100}
        overlay = handler._blocked_map_overlay(by_country)
        assert len(overlay) <= 4200


class TestGetStaticMap:
    def test_requests_a_satellite_jpeg_composited_with_the_given_bounding_box_and_overlay(self):
        client = _mock_geo_maps_client(b"real-bytes")
        result = handler._get_static_map(client, "-125,24,-67,49", '{"type":"FeatureCollection","features":[]}')
        assert result == b"real-bytes"
        call_kwargs = client.get_static_map.call_args.kwargs
        assert call_kwargs["BoundingBox"] == "-125,24,-67,49"
        assert call_kwargs["GeoJsonOverlay"] == '{"type":"FeatureCollection","features":[]}'
        assert call_kwargs["Style"] == "Satellite"

    def test_never_sends_colorscheme(self):
        # GetStaticMap rejects ColorScheme outright when Style=Satellite.
        client = _mock_geo_maps_client()
        handler._get_static_map(client, "-125,24,-67,49", "overlay")
        assert "ColorScheme" not in client.get_static_map.call_args.kwargs


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
