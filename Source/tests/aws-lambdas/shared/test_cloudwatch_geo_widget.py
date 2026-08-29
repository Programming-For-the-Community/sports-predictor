import sys
from unittest.mock import MagicMock

from PIL import Image

handler = sys.modules["shared_cloudwatch_geo_widget"]


def _mock_logs_client(rows: list[dict]):
    client = MagicMock()
    client.start_query.return_value = {"queryId": "q-1"}
    client.get_query_results.return_value = {
        "status": "Complete",
        "results": [[{"field": k, "value": str(v)} for k, v in row.items()] for row in rows],
    }
    return client


class TestBoundaryData:
    def test_every_state_centroid_has_a_matching_ring_entry(self):
        assert set(handler.US_STATE_CENTROIDS.keys()) <= set(handler.STATE_RINGS.keys())

    def test_state_and_country_rings_are_real_lon_lat_not_pixel_space(self):
        ca_points = [pt for ring in handler.STATE_RINGS["CA"] for pt in ring]
        lons = [p[0] for p in ca_points]
        lats = [p[1] for p in ca_points]
        assert -125 < min(lons) and max(lons) < -113
        assert 32 < min(lats) and max(lats) < 43

    def test_most_countries_resolved_to_a_real_iso_alpha_2_code(self):
        # A handful of disputed/unrecognized territories (no ISO code at
        # all) are expected to be missing -- most of ~177 should resolve.
        assert len(handler.COUNTRY_RINGS) > 150
        assert all(len(code) == 2 for code in handler.COUNTRY_RINGS)


class TestBucket:
    def test_zero_count_is_bucket_zero(self):
        assert handler._bucket(0, 100) == 0

    def test_zero_max_is_bucket_zero_even_with_a_positive_count(self):
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
        assert handler._run_logs_insights_query(client, ["lg"], "filter true", 0, 1000, max_wait_seconds=0) == []

    def test_returns_empty_list_on_a_failed_query_rather_than_raising(self):
        client = MagicMock()
        client.start_query.return_value = {"queryId": "q-1"}
        client.get_query_results.return_value = {"status": "Failed", "results": []}
        assert handler._run_logs_insights_query(client, ["lg"], "filter true", 0, 1000) == []


class TestAcceptedHotspots:
    def test_every_nonzero_state_gets_its_own_hotspot_no_aggregation(self):
        hotspots = handler._accepted_hotspots({"CA": 10, "OR": 5})
        assert len(hotspots) == 2  # not merged into one Pacific-region point

    def test_no_traffic_anywhere_produces_no_hotspots(self):
        assert handler._accepted_hotspots({}) == []

    def test_an_unmapped_state_code_is_dropped_not_misplaced(self):
        assert handler._accepted_hotspots({"ZZ": 100}) == []


class TestCountryBuckets:
    def test_returns_a_bucket_per_country_code_directly_no_region_grouping(self):
        buckets = handler._country_buckets({"RU": 100, "BR": 25})
        assert buckets == {"RU": 4, "BR": 1}

    def test_no_traffic_anywhere_produces_no_buckets(self):
        assert handler._country_buckets({}) == {}


class TestProject:
    def test_top_left_of_the_box_maps_to_the_image_origin(self):
        x, y = handler._project(-125, 49, "-125,24,-67,49", 600, 260)
        assert (x, y) == (0, 0)

    def test_bottom_right_of_the_box_maps_to_the_image_far_corner(self):
        x, y = handler._project(-67, 24, "-125,24,-67,49", 600, 260)
        assert round(x) == 600
        assert round(y) == 260

    def test_center_of_the_box_maps_to_the_image_center(self):
        x, y = handler._project(-96, 36.5, "-125,24,-67,49", 600, 260)
        assert round(x) == 300
        assert round(y) == 130


class TestInterpHeatColor:
    def test_zero_is_fully_transparent(self):
        assert handler._interp_heat_color(0) == (0, 0, 0, 0)

    def test_max_value_is_the_hottest_stop(self):
        assert handler._interp_heat_color(255) == handler._HEAT_COLOR_STOPS[-1][1]

    def test_alpha_increases_with_value(self):
        low = handler._interp_heat_color(50)
        high = handler._interp_heat_color(220)
        assert high[3] > low[3]


class TestPinpointLayer:
    def test_no_hotspots_produces_a_fully_transparent_layer(self):
        layer = handler._pinpoint_layer([], "-125,24,-67,49")
        assert layer.getextrema()[3] == (0, 0)  # alpha channel min == max == 0

    def test_a_top_bucket_hotspot_survives_the_blur_at_close_to_full_color(self):
        # Regression guard: a core radius too small relative to the blur
        # radius dilutes the peak intensity below its own color-stop
        # threshold, silently misrendering a bucket-4 hotspot as the
        # coolest color instead of the hottest.
        lon, lat = handler.US_STATE_CENTROIDS["CA"]
        layer = handler._pinpoint_layer([(lon, lat, 4)], handler._CONUS_BOUNDING_BOX)
        x, y = handler._project(lon, lat, handler._CONUS_BOUNDING_BOX, handler._RENDER_WIDTH, handler._RENDER_HEIGHT)
        pixel = layer.getpixel((round(x), round(y)))
        hottest = handler._HEAT_COLOR_STOPS[-1][1]
        assert all(abs(pixel[i] - hottest[i]) <= 10 for i in range(3))  # close to the hottest color, not diluted toward the coolest

    def test_layer_matches_the_render_canvas_size(self):
        layer = handler._pinpoint_layer([], "-125,24,-67,49")
        assert layer.size == (handler._RENDER_WIDTH, handler._RENDER_HEIGHT)


class TestChoroplethLayer:
    def test_no_buckets_produces_a_fully_transparent_layer(self):
        layer = handler._choropleth_layer({}, handler._WORLD_BOUNDING_BOX)
        assert layer.getextrema()[3] == (0, 0)

    def test_a_country_with_no_ring_data_is_skipped_not_an_error(self):
        layer = handler._choropleth_layer({"ZZ": 4}, handler._WORLD_BOUNDING_BOX)
        assert layer.getextrema()[3] == (0, 0)

    def test_a_bucketed_country_paints_its_own_shape(self):
        layer = handler._choropleth_layer({"US": 4}, handler._WORLD_BOUNDING_BOX)
        assert layer.getextrema()[3][1] > 0  # something opaque got drawn


class TestLegend:
    def test_gradient_has_no_dead_black_zone_at_the_low_end(self):
        # _HEAT_COLOR_STOPS' own bucket-0 stop is transparent black --
        # the legend must skip it or "Low" would fade to black instead
        # of showing the real lowest-bucket color.
        gradient = handler._legend_gradient(120, 10)
        assert gradient.getpixel((0, 0)) != (0, 0, 0)

    def test_gradient_runs_cool_to_hot_left_to_right(self):
        gradient = handler._legend_gradient(120, 10)
        low = gradient.getpixel((0, 5))
        high = gradient.getpixel((119, 5))
        assert high[0] > low[0]  # red channel rises toward the hot end

    def test_draw_legend_does_not_raise_on_a_normal_final_image(self):
        image = Image.new("RGB", (handler._MAP_WIDTH, handler._MAP_HEIGHT), (13, 20, 32))
        handler._draw_legend(image)  # no exception


class TestRenderImages:
    def test_render_accepted_image_produces_a_valid_jpeg_of_the_configured_size(self):
        image_bytes = handler._render_accepted_image({"CA": 10, "TX": 5})
        image = Image.open(handler.io.BytesIO(image_bytes))
        assert image.format == "JPEG"
        assert image.size == (handler._MAP_WIDTH, handler._MAP_HEIGHT)

    def test_render_blocked_image_produces_a_valid_jpeg_of_the_configured_size(self):
        image_bytes = handler._render_blocked_image({"RU": 10, "BR": 5})
        image = Image.open(handler.io.BytesIO(image_bytes))
        assert image.format == "JPEG"
        assert image.size == (handler._MAP_WIDTH, handler._MAP_HEIGHT)

    def test_render_functions_work_with_no_traffic_at_all(self):
        assert handler._render_accepted_image({})
        assert handler._render_blocked_image({})


class TestMapHtml:
    def test_embeds_the_image_as_a_base64_img_tag(self):
        html = handler._map_html(lambda counts: b"real-bytes", {"CA": 5})
        assert "<img src=\"data:image/jpeg;base64," in html
        import base64
        assert base64.b64encode(b"real-bytes").decode("ascii") in html

    def test_falls_back_to_a_text_summary_instead_of_raising_when_rendering_fails(self):
        def _boom(counts):
            raise RuntimeError("boom")
        html = handler._map_html(_boom, {"CA": 5, "TX": 2})
        assert "Map unavailable" in html
        assert "CA: 5" in html

    def test_falls_back_gracefully_with_no_data_either(self):
        def _boom(counts):
            raise RuntimeError("boom")
        html = handler._map_html(_boom, {})
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
        monkeypatch.setattr(handler.boto3, "client", lambda service, **kwargs: logs_client)
        monkeypatch.setenv("ACCEPTED_LOG_GROUP_NAMES", "lg1,lg2")
        result = handler.lambda_handler({"mode": "accepted", "widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "<img src=\"data:image/jpeg;base64," in result
        assert logs_client.start_query.call_args.kwargs["logGroupNames"] == ["lg1", "lg2"]

    def test_blocked_mode_queries_the_configured_edge_log_group_in_us_east_1(self, monkeypatch):
        logs_client = _mock_logs_client([{"c-country": "CN", "requests": 6}])
        seen_kwargs = {}

        def _client(service, **kwargs):
            seen_kwargs.update(kwargs)
            return logs_client

        monkeypatch.setattr(handler.boto3, "client", _client)
        monkeypatch.setenv("BLOCKED_LOG_GROUP_NAME", "cf-edge-logs")
        result = handler.lambda_handler({"mode": "blocked", "widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "<img src=\"data:image/jpeg;base64," in result
        # The edge-access log group only ever exists in us-east-1
        # regardless of this Lambda's own region.
        assert seen_kwargs.get("region_name") == "us-east-1"

    def test_missing_mode_defaults_to_accepted(self, monkeypatch):
        logs_client = _mock_logs_client([])
        monkeypatch.setattr(handler.boto3, "client", lambda service, **kwargs: logs_client)
        monkeypatch.setenv("ACCEPTED_LOG_GROUP_NAMES", "lg1")
        result = handler.lambda_handler({"widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "<img src=\"data:image/jpeg;base64," in result

    def test_never_calls_out_to_a_geo_maps_or_location_service_client(self, monkeypatch):
        # Everything is rendered locally now -- no external mapping
        # service call at all.
        logs_client = _mock_logs_client([])
        seen_services = []

        def _client(service, **kwargs):
            seen_services.append(service)
            return logs_client

        monkeypatch.setattr(handler.boto3, "client", _client)
        monkeypatch.setenv("ACCEPTED_LOG_GROUP_NAMES", "lg1")
        handler.lambda_handler({"mode": "accepted", "widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert seen_services == ["logs"]
