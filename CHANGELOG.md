# 更新日志

## 4.1.0

- 验证基线更新为 MaiBot 1.2.4 与 maibot-plugin-sdk 2.8.0；继续要求应用 `patches/maibot-llm-model-before-attempt.patch`。
- 模型每日配额检查改为按模型隔离异步锁，同模型串行、不同模型可并发。
- 修复错误快照已见缓存无序淘汰导致旧文件可能被重复处理的问题，并保持缓存有界。
- 新增只读管理员命令 `/配额`，展示所有已配置模型的当日用量、剩余量、比例、状态及重置时间。
- `/稍等` 与 `/配额` 共用模型配额格式化逻辑；配置结构保持兼容。
- manifest 兼容范围收窄为实际验证的 Host 1.2.4 / SDK 2.8.0。

## 4.0.0

- 新增按模型别名独立统计的自然日 Token 配额。
- 新增 `llm.model.before_attempt` 模型级 Hook：超额模型在 Provider 请求前被跳过，并复用 MaiBot 原有 fallback。
- 配额跳过不触发全局 Hold、不阻断消息入站，也不计入 Provider 重试或模型失败 penalty。
- 保留原有静态限制、动态预算、错误阈值、通知与管理命令配置。
- 插件更名为 `maibot-quota-router-plugin`，保留原 manifest ID 以兼容已有插件配置。
- 新增带完整中文注释的 `config.example.toml`，并说明模型级配额与全局 Hold 配置的区别。

## 3.2.1

- 优化统计文本

## 3.0.1

- 触发限速（含预算超速、时段外停止）时，与错误阈值一样转发通知。
- `/稍等` 增加限速触发次数统计（窗口合计与分目标）。
- 动态预算新增 `off_hours`：`hold` 在指定时段外停止响应；`continue` 时段外不控速，继续花完剩余额度。

## 3.0.0

- 重构为基于 llm_usage 的成功调用、Token、成本监控。
- 成功数据改为宿主 `database.query(ModelUsage)` + 插件内时间窗聚合（兼容 OneKey，不依赖新增 capability）。
- 支持 provider/model/feature 静态限制与每日动态预算。
- 支持 strict、balanced 预算策略；balanced 用 overshoot_time 控制可超前秒数。
- 恢复错误阈值停模：窗口内达限后 LATE abort，停止时长线性增长。
- 错误仅解析 MaiBot schema v3 的 llm_error 快照。
