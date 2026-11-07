from datetime import date

from library.assistant import cost_snapshot
from library.assistant.settings import ProducerSettings

SETTINGS = ProducerSettings(cost_restated_days=3, cost_backfill_months=12, cost_month_repull_day=5, traffic_lookback_hours=2)


def _period(day, groups, estimated=False):
    return {
        "TimePeriod": {"Start": day},
        "Estimated": estimated,
        "Groups": [{"Keys": keys, "Metrics": {"UnblendedCost": {"Amount": str(amount)}}} for keys, amount in groups],
    }


class FakeCostExplorer:
    def __init__(self, by_service=None, by_component=None, forecast="100.0", pages=1):
        self.calls = []
        self.forecast_calls = []
        self.by_service = by_service or []
        self.by_component = by_component or []
        self.forecast = forecast
        self.pages = pages

    def get_cost_and_usage(self, **kwargs):
        self.calls.append(kwargs)
        data = self.by_service if kwargs["GroupBy"][0]["Key"] == "SERVICE" else self.by_component
        page = kwargs.get("NextPageToken")
        if self.pages == 2 and page is None:
            return {"ResultsByTime": data[:1], "NextPageToken": "p2"}
        return {"ResultsByTime": data[1:] if page else data}

    def get_cost_forecast(self, **kwargs):
        self.forecast_calls.append(kwargs)
        if self.forecast is None:
            raise RuntimeError("DataUnavailableException")
        return {"Total": {"Amount": self.forecast}}


class FakeBudgets:
    def describe_budget(self, AccountId, BudgetName):
        return {"Budget": {"BudgetLimit": {"Amount": "120.0"}}}


SERVICE = [
    _period("2026-11-03", [(["AWS Lambda", "Sport$nfl"], 1.5), (["Amazon S3", "Sport$"], 0.5)], estimated=True),
    _period("2026-11-04", [(["AWS Lambda", "Sport$nba"], 2.0)]),
]
COMPONENT = [
    _period("2026-11-03", [(["Component$serving", "Sport$nfl"], 1.5), (["Component$", "Sport$"], 0.5)]),
    _period("2026-11-04", [(["Component$training", "Sport$nba"], 2.0)]),
]


def test_every_request_carries_the_project_filter(fake_s3):
    ce = FakeCostExplorer(SERVICE, COMPONENT)
    cost_snapshot.run(ce, FakeBudgets(), fake_s3({cost_snapshot.meta_key(): {"data_from": "2026-01-01"}}), "123", "b", date(2026, 11, 6), SETTINGS)

    assert ce.calls
    assert ce.forecast_calls
    for call in ce.calls + ce.forecast_calls:
        assert call["Filter"] == cost_snapshot.PROJECT_FILTER
    tags = {t["Tags"]["Key"]: t["Tags"]["Values"] for t in cost_snapshot.PROJECT_FILTER["Or"]}
    assert tags == {"Project": ["sports-predictor"], "iamPrincipal/Project": ["sports-predictor"]}


def test_untagged_spend_is_grouped_as_misc_and_totals_add_up():
    days = cost_snapshot.build_days(SERVICE, COMPONENT, "now")
    first = days["2026-11-03"]

    assert first["total"] == 2.0
    assert first["by_sport"] == {"nfl": 1.5, "Misc": 0.5}
    assert first["by_component"] == {"serving": 1.5, "Misc": 0.5}
    assert first["by_sport_component"] == {"nfl/serving": 1.5, "Misc/Misc": 0.5}
    assert first["estimated"] is True
    assert sum(first["by_sport"].values()) == first["total"]


def test_daily_run_rewrites_the_last_three_days_and_rolls_up_the_month(fake_s3):
    s3 = fake_s3({cost_snapshot.meta_key(): {"data_from": "2026-01-01"}})
    ce = FakeCostExplorer(SERVICE, COMPONENT)
    summary = cost_snapshot.run(ce, FakeBudgets(), s3, "123", "b", date(2026, 11, 6), SETTINGS)

    assert ce.calls[0]["TimePeriod"] == {"Start": "2026-11-03", "End": "2026-11-06"}
    assert summary["backfill"] is False
    month = s3.objects[cost_snapshot.monthly_key(2026, 11)]
    assert month["total"] == 4.0
    assert month["days"] == 2
    meta = s3.objects[cost_snapshot.meta_key()]
    assert meta["budget_limit"] == 120.0
    assert meta["month_forecast"] == 100.0
    assert meta["data_from"] == "2026-01-01"


def test_first_run_backfills_twelve_months_and_records_where_tagged_spend_starts(fake_s3):
    s3 = fake_s3()
    ce = FakeCostExplorer(SERVICE, COMPONENT)
    summary = cost_snapshot.run(ce, FakeBudgets(), s3, "123", "b", date(2026, 11, 6), SETTINGS)

    assert summary["backfill"] is True
    assert ce.calls[0]["TimePeriod"] == {"Start": "2025-11-01", "End": "2026-11-06"}
    assert s3.objects[cost_snapshot.meta_key()]["data_from"] == "2026-11-03"


def test_the_fifth_also_repulls_the_previous_month():
    windows = cost_snapshot.windows(date(2026, 11, 5), False, SETTINGS)
    assert windows == [(date(2026, 11, 2), date(2026, 11, 5)), (date(2026, 10, 1), date(2026, 11, 1))]


def test_pages_are_followed(fake_s3):
    ce = FakeCostExplorer(SERVICE, COMPONENT, pages=2)
    s3 = fake_s3({cost_snapshot.meta_key(): {"data_from": "2026-01-01"}})
    cost_snapshot.run(ce, FakeBudgets(), s3, "123", "b", date(2026, 11, 6), SETTINGS)

    assert any(call.get("NextPageToken") == "p2" for call in ce.calls)
    assert cost_snapshot.daily_key(date(2026, 11, 4)) in s3.objects


def test_a_missing_forecast_does_not_fail_the_run(fake_s3):
    s3 = fake_s3({cost_snapshot.meta_key(): {"data_from": "2026-01-01"}})
    cost_snapshot.run(FakeCostExplorer(SERVICE, COMPONENT, forecast=None), FakeBudgets(), s3, "123", "b", date(2026, 11, 6), SETTINGS)

    assert s3.objects[cost_snapshot.meta_key()]["month_forecast"] is None
