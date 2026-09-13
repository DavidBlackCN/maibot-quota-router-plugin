# maibot-quota-router-plugin

一个 Maibot 插件，为 MaiBot 提供单模型自然日 Token 配额与自动降级路由，同时保留原 `maibot_plugin_hold_on` 的静态限制、动态预算、错误阈值、通知和管理命令。

开发与验证基线：**MaiBot 1.2.5、maibot-plugin-sdk 2.8.0**

> 本插件是 [maibot_plugin_hold_on](https://github.com/FlandreSatori/maibot_plugin_hold_on) 的分支，使用其 **LLM usage 统计、按 model 区分、Token 计数、预算规则** 等基础，并为插件行为新增 **单模型每日 Token 配额与自动降级路由**

## 功能

- 按 `model_config.toml` 中的模型别名分别统计每日 Token。
- 只限制显式配置了配额的模型；未配置模型保持 MaiBot 原有行为。
- 模型达到配额后，在 Provider 请求发出前跳过该模型。
- 普通任务继续使用 MaiBot 原有 `balance`、`random` 或 `sequential` 策略选择剩余模型。
- 不触发全局 Hold，也不在聊天入站 Hook 中 `abort`，MaiBot 仍会正常处理消息。
- 每个自然日 00:00 按 MaiBot 所在机器的本地时区自动开始新统计周期，无需定时任务；新一天第一次检查时模型自动恢复可用。
- 继续复用 MaiBot 的 `ModelUsage` / `llm_usage` 成功调用记录，以及本插件原有的聚合和 `BudgetRule` 基础设施。

## 安装要求

模型级路由依赖新的 `llm.model.before_attempt` Hook。**MaiBot 1.2.4 仍未原生提供该 Hook，必须保留并应用 `patches/maibot-llm-model-before-attempt.patch`**。请先在 MaiBot 仓库根目录检查并应用随插件提供的补丁：

```bash
PATCH=/path/to/maibot-quota-router-plugin/patches/maibot-llm-model-before-attempt.patch
git apply --check "$PATCH"
git apply "$PATCH"
```

`git apply` 成功时默认没有输出，使用反向检查确认补丁已经存在：

```bash
git apply --check --reverse "$PATCH"
```

反向检查无输出即表示补丁已应用。也可以查看实际改动：

```bash
git diff -- src/llm_models/utils_model.py src/plugin_runtime/hook_catalog.py
```

补丁已在 MaiBot 1.2.4（提交 `21cd74d81d47b6f77ba5ab7116a88916e957d15b`）上通过 `git apply --check` 验证，只修改：

- `src/llm_models/utils_model.py`
- `src/plugin_runtime/hook_catalog.py`

升级 MaiBot 后，应先执行正向检查，再决定是否重新应用：

```bash
git apply --check "$PATCH"
```

正向检查成功说明补丁尚未应用且与当前源码兼容；反向检查成功说明补丁已经存在。只有正向和反向检查都失败时，才可能是新版源码不兼容或文件存在混合改动，此时应按下文“Hook 设计”将同等逻辑移植到新版本，而不是强制应用补丁。未安装 Hook 的 MaiBot 会因无法注册该 HookHandler 而拒绝加载本插件。

## 每日模型配额配置

项目根目录提供了带逐项中文注释的 [`config.example.toml`](./config.example.toml)。该文件仅作参考，不会被插件自动加载；请把需要的字段复制到 MaiBot 已生成的插件配置中，或通过 WebUI 填写。

如果只需要“单模型每日 Token 配额与自动 fallback”，建议保留 `plugin.enabled = true` 和 `model_quotas.enabled = true`，同时关闭 `static_limits`、`budget`、`error_rules`。后三项属于兼容保留的全局 Hold 功能。

可在 WebUI 的“模型每日配额”分组中配置，也可使用对应 TOML：

```toml
[model_quotas]
enabled = true
usage_limit = 5000

[[model_quotas.items]]
model = "model-a"
daily_token_limit = 2000000
input_weight = 1.0
output_weight = 1.0

[[model_quotas.items]]
model = "model-b"
daily_token_limit = 500000
input_weight = 1.0
output_weight = 1.0
```

字段说明：

- `model`：模型别名，必须与 `model_config.toml` 中 `[[models]].name` 一致；统计优先使用 `ModelUsage.model_assign_name`，不会把同一 Provider 的不同模型合并。
- `daily_token_limit`：该模型每个自然日允许的 Token 总数。
- `input_weight` / `output_weight`：输入、输出 Token 权重，均为 `1.0` 时等于 `prompt_tokens + completion_tokens`。
- `usage_limit`：每次检查单个模型最多读取的最近成功记录数。若单模型每天可能超过 5000 次成功调用，应提高此值，最大可配置为 100000。

`model_quotas.items` 为空时不会改变任何模型行为。原有 `[budget]` 与 `[static_limits]` 配置仍保持原来的全局 Hold 语义；新的模型配额不经过该路径。

## 完整路由流程

1. MaiBot 按任务的 `selection_strategy` 从 `model_list` 选择一个候选模型。
2. 在构建并发送 Provider 请求前，MaiBot 调用 `llm.model.before_attempt`。
3. 插件按当前模型别名查询当天的 `llm_usage`，并只汇总该模型的成功 Token。
4. 未配置额度或实际用量小于额度时，Hook 放行，MaiBot 正常发起请求。
5. 实际用量达到额度时，插件返回 `custom_result = {skip_model = true, ...}`。
6. MaiBot 撤销选择阶段的临时 usage penalty，把该模型加入本次请求的排除集合，然后按原策略选择下一个模型。
7. fallback 模型正常完成请求并写入自己的 `llm_usage`；已超额模型不会产生新的 Provider 请求。
8. 次日 00:00 后查询窗口自然切换到新日期，该模型用量从 0 开始并恢复可用。

调用方显式传入 `model_name` 时，配额仍会生效，但不会擅自改用其他模型；该次显式模型请求会返回“所有候选模型均被 Hook 跳过”。普通任务配置的多模型列表才会自动 fallback。

## Hook 设计

`llm.model.before_attempt` 位于 `LLMOrchestrator._select_model()` 之后、上下文构建和 `_attempt_request_on_model_with_timeout()` 之前。这一位置保证插件已经知道具体模型，同时 Provider 请求尚未产生。

Hook 输入仅包含可通过 IPC 安全序列化的值：

- `task_name`、`request_type`、`session_id`
- `model_name`、`model_identifier`、`provider_name`
- `candidate_model_names`、`excluded_model_names`
- `explicit_model`

Hook 不允许修改请求参数，也不使用通用 `abort`。插件通过 `custom_result.skip_model` 请求跳过，主程序只接受与当前 `model_name` 匹配的结果。跳过模型时：

- 不调用 Provider；
- 不消耗 Provider 重试次数；
- 不增加失败 penalty；
- 只影响当前 LLM 请求的候选集合；
- 不写入插件的全局 Hold 状态。

Hook 调度或插件执行异常时主程序采用 fail-open，继续使用当前模型。插件查询 `llm_usage` 失败时同样 fail-open，以免配额插件故障扩大成 MaiBot 全局不可用。

## 并发与计数边界

- Token 只能在模型成功返回后从 `llm_usage` 得知。若某次请求开始时尚未达到额度，但它的最终用量跨过上限，该请求会完成；之后的新请求才被跳过。
- 多个已经在途的并发请求无法被事后取消，因此可能使最终用量超过额度。插件使用按模型隔离的进程内异步锁：同一模型串行执行“查询并判断”，不同模型可并发检查，但仍不能预知在途请求的最终 Token。
- Hook 每次对受控模型重新读取数据库，不使用可能延迟阻断的 TTL 缓存；代价是受控模型每次尝试增加一次 IPC 和一次数据库查询。
- `usage_limit` 小于单模型当日成功调用数时，只能看到最近的记录并可能低估用量。应按实际调用量提高该值。
- 只统计成功写入 `ModelUsage` 的调用；Provider 返回了 Token 但数据库写入失败时会产生少量计数误差。
- 系统本地时间或时区在运行中被调整，会影响自然日边界。建议固定 MaiBot 主机时区。

## 原有配置与命令

原 `maibot_plugin_hold_on` 配置结构继续可用：

- `static_limits`：Provider、模型或功能的滑动窗口请求数、Token、成本限制。
- `budget`：在指定时段内按 `strict` / `balanced` 计划曲线控制消耗。
- `error_rules` / `error_watch`：按失败快照触发全局 Hold。
- `notify`：限速或错误 Hold 通知。
- `permission`：`/稍等`、`/配额`、`/解除` 管理命令白名单。
- `plugin.forward_image_threshold`：保留原合并转发图片数量入站保护。

命令：

- `/稍等`：显示原有限速统计及模型每日配额状态。
- `/配额`：以纯文本列出全部已配置模型的当日用量、剩余额度、使用比例、状态与重置时间；只读，不修改额度。
- `/解除`：解除原有全局 Hold；不会清除 `llm_usage`，因此不能解除已经达到的每日模型配额。

为兼容已有安装和配置，manifest ID 仍保留为 `maibot_plugin.hold_on`，展示名称和仓库名称已改为 `maibot-quota-router-plugin`。

## 参考

- [MaiBot 插件开发指南](https://docs.mai-mai.org/plugin/)
- [MaiBot LLM 模型集成与 fallback](https://docs.mai-mai.org/develop/llm-providers)
- [MaiBot Hook 与事件管线](https://docs.mai-mai.org/develop/event-pipeline-hooks)

## 鸣谢

[FlandreSatori/maibot_plugin_hold_on](https://github.com/FlandreSatori/maibot_plugin_hold_on)：提供了 **LLM usage 统计、按 model 区分、Token 计数、预算规则** 等代码基础

## 协议
[LICENSE](./LICENSE)
