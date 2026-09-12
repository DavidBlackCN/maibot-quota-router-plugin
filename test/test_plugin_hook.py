import asyncio
import sys
import types
from datetime import datetime
from types import MethodType, SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from pydantic import BaseModel, Field


def _identity_decorator(*args, **kwargs):
    del args, kwargs

    def decorate(func):
        return func

    return decorate


sdk = types.ModuleType("maibot_sdk")
sdk.Command = _identity_decorator
sdk.Field = Field
sdk.HookHandler = _identity_decorator
sdk.MaiBotPlugin = object
sdk.PluginConfigBase = BaseModel
sdk_types = types.ModuleType("maibot_sdk.types")
sdk_types.HookMode = SimpleNamespace(BLOCKING="blocking")
sdk_types.HookOrder = SimpleNamespace(EARLY="early", LATE="late")
sys.modules.setdefault("maibot_sdk", sdk)
sys.modules.setdefault("maibot_sdk.types", sdk_types)

import plugin
from modules.controller import Decision


class _Logger:
    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass


class ModelQuotaHookTests(IsolatedAsyncioTestCase):
    def _plugin(self, models=("model-a",)):
        instance = plugin.QuotaRouterPlugin()
        instance.config = SimpleNamespace(
            plugin=SimpleNamespace(enabled=True),
            model_quotas=SimpleNamespace(
                enabled=True,
                usage_limit=5000,
                items=[
                    plugin.ModelQuotaRuleConfig(model=model, daily_token_limit=100)
                    for model in models
                ],
            ),
            permission=SimpleNamespace(whitelist=["admin"]),
        )
        instance.ctx = SimpleNamespace(logger=_Logger())
        instance._quota_locks = {}
        instance._sync_quota_locks()
        return instance

    async def test_over_quota_returns_skip_custom_result(self) -> None:
        instance = self._plugin()

        async def fake_check(self, model_name, *, now=None):
            del self, now
            return Decision(
                blocked=True,
                reason=f"model:{model_name} tokens 每日配额 100/100",
                scope="model",
                target=model_name,
                metric="tokens",
                actual=100,
                limit=100,
                remaining=0,
                kind="quota",
                hold_seconds=60,
            )

        instance._check_model_quota = MethodType(fake_check, instance)

        result = await instance.handle_before_model_attempt(model_name="model-a")

        self.assertEqual(result["action"], "continue")
        self.assertTrue(result["custom_result"]["skip_model"])
        self.assertEqual(result["custom_result"]["model_name"], "model-a")

    async def test_unconfigured_model_is_unchanged(self) -> None:
        instance = self._plugin()

        result = await instance.handle_before_model_attempt(model_name="model-b")

        self.assertEqual(result, {"action": "continue"})

    async def test_quota_query_failure_is_fail_open(self) -> None:
        instance = self._plugin()

        async def fake_check(self, model_name, *, now=None):
            del self, model_name, now
            raise RuntimeError("database unavailable")

        instance._check_model_quota = MethodType(fake_check, instance)

        result = await instance.handle_before_model_attempt(model_name="model-a")

        self.assertEqual(result, {"action": "continue"})

    async def test_database_query_failure_is_fail_open(self) -> None:
        instance = self._plugin()

        class FailingDatabase:
            async def query(self, **kwargs):
                del kwargs
                raise RuntimeError("database unavailable")

        instance.ctx.db = FailingDatabase()
        result = await instance.handle_before_model_attempt(model_name="model-a")
        self.assertEqual(result, {"action": "continue"})

    async def test_different_models_use_different_locks(self) -> None:
        instance = self._plugin(("model-a", "model-b"))
        entered = set()
        both_entered = asyncio.Event()
        release = asyncio.Event()

        async def fake_check(self, model_name, *, now=None):
            del self, now
            entered.add(model_name)
            if len(entered) == 2:
                both_entered.set()
            await release.wait()
            return None

        instance._check_model_quota = MethodType(fake_check, instance)
        tasks = [
            asyncio.create_task(instance.handle_before_model_attempt(model_name=name))
            for name in ("model-a", "model-b")
        ]
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        self.assertIsNot(instance._quota_locks["model-a"], instance._quota_locks["model-b"])
        release.set()
        await asyncio.gather(*tasks)

    async def test_same_model_checks_are_serialized(self) -> None:
        instance = self._plugin()
        active = 0
        maximum_active = 0

        async def fake_check(self, model_name, *, now=None):
            nonlocal active, maximum_active
            del self, model_name, now
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return None

        instance._check_model_quota = MethodType(fake_check, instance)
        await asyncio.gather(
            instance.handle_before_model_attempt(model_name="model-a"),
            instance.handle_before_model_attempt(model_name="model-a"),
        )
        self.assertEqual(maximum_active, 1)


class ModelQuotaCommandTests(IsolatedAsyncioTestCase):
    def _plugin(self):
        instance = ModelQuotaHookTests()._plugin(("model-a", "model-b", "model-c"))
        return instance

    async def test_outputs_multiple_models_and_all_status_ranges(self) -> None:
        instance = self._plugin()
        reset_at = datetime(2026, 9, 13)

        async def fake_rows(self, now):
            del self, now
            return [
                {
                    "model": "model-a",
                    "actual": 0,
                    "limit": 2_000_000,
                    "remaining": 2_000_000,
                    "blocked": False,
                    "reset_at": reset_at,
                },
                {
                    "model": "model-b",
                    "actual": 1_324_000,
                    "limit": 2_000_000,
                    "remaining": 676_000,
                    "blocked": False,
                    "reset_at": reset_at,
                },
                {
                    "model": "model-c",
                    "actual": 600_000,
                    "limit": 500_000,
                    "remaining": 0,
                    "blocked": True,
                    "reset_at": reset_at,
                },
            ]

        instance._model_quota_status_rows = MethodType(fake_rows, instance)
        result = await instance.cmd_quota(user_id="admin", platform="qq")
        text = result[1]
        self.assertIn("【模型每日配额】", text)
        self.assertIn("已使用 0 / 2.000M（0.0%）", text)
        self.assertIn("1.324M / 2.000M（66.2%）", text)
        self.assertIn("0.600M / 0.500M（120.0%）", text)
        self.assertIn("状态：已达限额", text)
        self.assertEqual(text.count("重置：09-13 00:00"), 1)

    async def test_empty_quota_configuration(self) -> None:
        instance = self._plugin()

        async def fake_rows(self, now):
            del self, now
            return []

        instance._model_quota_status_rows = MethodType(fake_rows, instance)
        result = await instance.cmd_quota(user_id="admin", platform="qq")
        self.assertEqual(result[1], "当前未配置模型每日配额")

    async def test_unauthorized_user(self) -> None:
        instance = self._plugin()
        result = await instance.cmd_quota(user_id="guest", platform="qq")
        self.assertEqual(result[1], "权限不足。")
