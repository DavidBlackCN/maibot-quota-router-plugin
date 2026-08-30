from datetime import datetime, timedelta
from unittest import IsolatedAsyncioTestCase, TestCase

from modules.controller import BudgetRule, check_daily_quota, daily_quota_progress
from modules.usage_sync import aggregate_usage, aggregate_usage_rows


class DailyQuotaControllerTests(TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 8, 30)
        self.end = self.start + timedelta(days=1)

    def _rule(self, limit: float = 100) -> BudgetRule:
        return BudgetRule(
            scope="model",
            target="model-a",
            metric="tokens",
            amount=limit,
            start=self.start,
            end=self.end,
        )

    def test_aggregates_only_the_target_model(self) -> None:
        groups = [
            {"model": "model-a", "input_tokens": 30, "output_tokens": 20},
            {"model": "model-a", "input_tokens": 10, "output_tokens": 5},
            {"model": "model-b", "input_tokens": 999, "output_tokens": 999},
        ]

        progress = daily_quota_progress(groups, self._rule())

        self.assertEqual(progress["actual"], 65)
        self.assertEqual(progress["remaining"], 35)

    def test_blocks_at_limit_and_resets_at_period_end(self) -> None:
        groups = [{"model": "model-a", "input_tokens": 60, "output_tokens": 40}]
        now = self.start + timedelta(hours=12)

        decision = check_daily_quota(groups, self._rule(), now)

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.kind, "quota")
        self.assertEqual(decision.actual, 100)
        self.assertEqual(decision.hold_seconds, 12 * 60 * 60)

    def test_allows_when_below_limit(self) -> None:
        groups = [{"model": "model-a", "input_tokens": 59, "output_tokens": 40}]

        self.assertIsNone(check_daily_quota(groups, self._rule(), self.start + timedelta(hours=1)))


class UsageAggregationTests(TestCase):
    def test_model_aliases_are_counted_independently(self) -> None:
        rows = [
            {
                "timestamp": "2026-08-30T10:00:00",
                "model_assign_name": "model-a",
                "model_name": "same-upstream-model",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            {
                "timestamp": "2026-08-30T10:01:00",
                "model_assign_name": "model-b",
                "model_name": "same-upstream-model",
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
        ]

        result = aggregate_usage_rows(
            rows,
            start_ts=datetime(2026, 8, 30).timestamp(),
            end_ts=datetime(2026, 8, 31).timestamp(),
        )
        tokens_by_model = {item["model"]: item["tokens"] for item in result["groups"]}

        self.assertEqual(tokens_by_model, {"model-a": 15.0, "model-b": 30.0})

    def test_previous_natural_day_is_not_counted(self) -> None:
        rows = [
            {
                "timestamp": "2026-08-29T23:59:59",
                "model_assign_name": "model-a",
                "prompt_tokens": 500,
                "completion_tokens": 500,
                "total_tokens": 1000,
            },
            {
                "timestamp": "2026-08-30T00:00:01",
                "model_assign_name": "model-a",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        ]

        result = aggregate_usage_rows(
            rows,
            start_ts=datetime(2026, 8, 30).timestamp(),
            end_ts=datetime(2026, 8, 31).timestamp(),
        )

        self.assertEqual(result["total"]["tokens"], 15.0)


class _FakeDatabase:
    def __init__(self) -> None:
        self.kwargs = {}

    async def query(self, **kwargs):
        self.kwargs = kwargs
        return []


class _FakeContext:
    def __init__(self) -> None:
        self.db = _FakeDatabase()
        self.logger = None


class UsageQueryTests(IsolatedAsyncioTestCase):
    async def test_model_query_is_filtered_by_assign_name(self) -> None:
        context = _FakeContext()
        start = datetime(2026, 8, 30)

        await aggregate_usage(context, start, start + timedelta(hours=1), 5000, model_name="model-a")

        self.assertEqual(context.db.kwargs["filters"], {"model_assign_name": "model-a"})
        self.assertEqual(context.db.kwargs["order_by"], ["-id"])
        self.assertEqual(context.db.kwargs["limit"], 5000)


if __name__ == "__main__":
    import unittest

    unittest.main()
