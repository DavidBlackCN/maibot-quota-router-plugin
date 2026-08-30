import asyncio
import sys
import types
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
    def _plugin(self):
        instance = plugin.QuotaRouterPlugin()
        instance.config = SimpleNamespace(
            plugin=SimpleNamespace(enabled=True),
            model_quotas=SimpleNamespace(
                enabled=True,
                usage_limit=5000,
                items=[plugin.ModelQuotaRuleConfig(model="model-a", daily_token_limit=100)],
            ),
        )
        instance.ctx = SimpleNamespace(logger=_Logger())
        instance._quota_lock = asyncio.Lock()
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
