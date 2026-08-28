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

    def test_a_region_code_outside_the_us_state_grid_is_dropped_not_misplaced(self):
        # A non-US or malformed region value must never silently land on some
        # unrelated US_STATE_GRID tile.
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


class TestRegionGrouping:
    def test_known_country_codes_group_under_their_real_region(self):
        html = handler._render_region_grid({"RU": 10, "BR": 2})
        assert "Europe" in html
        assert "Americas" in html
        assert "RU" in html and "BR" in html

    def test_an_unmapped_country_code_falls_into_other_instead_of_being_dropped(self):
        html = handler._render_region_grid({"XX": 4})
        assert "Other" in html
        assert "XX" in html

    def test_no_blocked_traffic_at_all_renders_a_message_not_an_empty_string(self):
        assert "No blocked traffic" in handler._render_region_grid({})


class TestStateGrid:
    def test_every_state_in_the_grid_appears_in_the_rendered_svg(self):
        svg = handler._render_state_grid({"CA": 9})
        for code in handler.US_STATE_GRID:
            assert f">{code}<" in svg

    def test_is_valid_enough_svg_to_open_and_close(self):
        svg = handler._render_state_grid({})
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")


class TestLambdaHandler:
    def test_describe_event_returns_markdown_without_touching_aws(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr(handler.boto3, "client", called)
        result = handler.lambda_handler({"describe": True}, None)
        assert "Geo widget" in result
        called.assert_not_called()

    def test_accepted_mode_queries_every_configured_log_group(self, monkeypatch):
        client = _mock_logs_client([{"region": "TX", "requests": 3}])
        monkeypatch.setattr(handler.boto3, "client", lambda service, **kwargs: client)
        monkeypatch.setenv("ACCEPTED_LOG_GROUP_NAMES", "lg1,lg2")
        result = handler.lambda_handler({"mode": "accepted", "widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert ">TX<" in result
        assert client.start_query.call_args.kwargs["logGroupNames"] == ["lg1", "lg2"]

    def test_blocked_mode_queries_the_configured_edge_log_group_in_us_east_1(self, monkeypatch):
        client = _mock_logs_client([{"c-country": "CN", "requests": 6}])
        seen_kwargs = {}

        def _client(service, **kwargs):
            seen_kwargs.update(kwargs)
            return client

        monkeypatch.setattr(handler.boto3, "client", _client)
        monkeypatch.setenv("BLOCKED_LOG_GROUP_NAME", "cf-edge-logs")
        result = handler.lambda_handler({"mode": "blocked", "widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "CN" in result
        # The edge-access log group only ever exists in us-east-1
        # regardless of this Lambda's own region -- a client defaulting to
        # the Lambda's own region would silently find nothing.
        assert seen_kwargs.get("region_name") == "us-east-1"

    def test_missing_mode_defaults_to_accepted(self, monkeypatch):
        client = _mock_logs_client([])
        monkeypatch.setattr(handler.boto3, "client", lambda service, **kwargs: client)
        monkeypatch.setenv("ACCEPTED_LOG_GROUP_NAMES", "lg1")
        result = handler.lambda_handler({"widgetContext": {"timeRange": {"start": 0, "end": 1000}}}, None)
        assert "<svg" in result
