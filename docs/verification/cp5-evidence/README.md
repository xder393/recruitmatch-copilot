# CP5 分阶段验收记录

更新：2026-09-14。**Task 1、Task 2 已验收；CP5 尚未完成。**

后续确认：用户已批准遥测专用 `service.instance.id` 例外，Task 2 已实现，现在继续 Task 3。首次验收报告与控制记录保留当时“待确认”的历史状态，不表示批准被撤回。

## 已完成：OTel 适配器与隐私策略

- 基线：`f297b0a509fca92b37de1be174e18a601ccfd64e`（已合并的 CP4）。
- 实现：`5abe44f74eab7ed962d41de63da212a8b4f20990`。
- 修复：`d9dc9ab3aeaa935c0bcf2f596bd6c6add511be92`。
- 本地分支：`codex/recruitmatch-cp5-observability`。未推送、未创建 CP5 PR、未合并 main。

提供兼容既有业务接口的事件记录和操作范围、27 个规范指标、安全 Tracer/Meter、有限队列/导出超时、可隔离启停的 SDK 生命周期。默认遥测关闭。Task 1 当时尚未接入业务埋点和真实后端；业务埋点的后续证据见 Task 2，不能以本阶段单元测试替代真实后端验收。

独立审查发现并修复了两项 Important：真实 OTLP 错误日志可能泄露异常内容，以及直接 Meter 调用绕过评分 0–100 范围。修复后定向复核确认均已解决，无新增 Critical/Important。

## Task 1 本地证据

以下结果均针对修复后的实际镜像与源码，未调用真实模型、未下载 Embedding 模型、未读取个人 `.env`。

| 检查 | 结果 |
| --- | --- |
| Docker Compose 完整单元测试主路径 | 365 passed in 21.32s，包括 79 项 observability 测试 |
| Ruff 全仓库 app/tests/scripts | check 通过；format 215 files already formatted |
| 当前 CI 原有 mypy 范围 | 41 个文件通过 |
| observability/config/main 定向类型检查 | 6 个文件通过；observability 更严格的 check-untyped-defs 4 个文件通过 |
| 离线 uv lock --check | 131 个包；无隐式改锁 |
| uv pip check | 127 个安装包兼容 |
| Compose 配置、Git diff --check | 通过 |
| 镜像源码一致性 | 9 个改动代码/测试/依赖文件 SHA256 与工作树逐一相同 |

镜像 OCI index digest：`sha256:7287722c373746429a393b8a0c2ac19654ba324833c05f939d95b97b811a27fa`。它与构建日志中的 config digest 属于不同标识层。此处没有新的远程 CI 通过声明。

可重复的单元测试命令（宿主机只需 Docker，使用仓库演示配置）：

```sh
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 build test-unit
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps test-unit
```

代理、定向测试、RED/GREEN 和详细校验命令见下方完整记录。

## 已完成：Task 2 框架与业务埋点

- 基线：`4648e575e5168d1687b767c1cc13bd216076e5bc`（批准记录）。
- 实现：`9bca50c5b76e54070f10c00b40102456440092e3`。
- 修复：`6d4aa6a035241e816a5ed37ea403e8e023ec52fe`。

接入 FastAPI、SQLAlchemy、Redis、HTTPX、Celery 的 owner 隔离遥测、W3C 传播、业务操作 Span、结构化安全日志，以及 PostgreSQL/Redis 权威聚合指标。独立审查发现并修复 Worker/Beat 启动横幅绕过 JSON 日志和最终解释引用失效漏记指标；定向复审确认两项均已解决，无新增问题。

| 最终镜像检查 | 结果 |
| --- | --- |
| Compose 单元测试 | 395 passed in 36.44s |
| Compose 集成测试 | 520 passed in 26.20s |
| Ruff / format | 通过；223 files already formatted |
| CI / 定向 mypy | 41 / 18 个文件通过 |
| Compose / diff 校验 | 通过 |
| 修复源码与镜像一致性 | matching、Compose、两个回归测试文件 SHA256 相同 |

最终镜像：`sha256:328c13f7092a9b9a09cb876a68840f049624e211e5b3ef3ed2ba35d845ee202a`。未使用真实模型、个人配置或模型下载。真实 CLI 启动测试不是跨 Broker E2E，后者仍在 Task 4 验收。

指标含义和边界见 [指标语义](../../observability-metrics.md)：失败尝试与安排重试可重叠；成功完成只计持久化发布；Gauge 未知不等于零。此处没有新的远程 CI 通过声明。

## 未完成项与既有边界

1. **多进程指标身份已实现，真实后端验证仍待完成。** 系统生成的 Resource-only `service.instance.id` 不关联任何业务身份；不能以强制单 Worker 替代 N Worker 能力。Task 3/4 要验证保留写入者身份、重启聚合和 Gauge 新鲜度。
2. Task 1 的 Histogram advisory Minor 已在 Task 2 修复并覆盖亚秒 Bucket 测试；真实后端 P95 查询仍待验收。
3. Task 3/4：真实后端、四张 Dashboard、告警 Firing、PII 查询、跨 Broker/prefork Trace 与 Collector 停止后的业务隔离测试。
4. 额外扩大 mypy 检查范围发现两个既有类型问题：`app/api/v1/resumes.py:30`、`app/api/v1/knowledge.py:54` 中可空文件名传给非空响应字段。在 CP4 基线镜像中已复现相同问题；本轮未修改这些文件。当前 CI 范围通过不等于整个仓库全量类型检查无问题。

## 记录索引

- [控制记录与实施裁决](controller-ledger.md)
- [实现、RED/GREEN 与修复报告](task-1-report.md)
- [首次独立审查](task-1-review.md)
- [修复后定向复核](task-1-fix-1-review.md)
- [Task 2 接口与统计语义交接](task-2-seams.md)
- [Task 2 实现、RED/GREEN 与修复报告](task-2-report.md)
- [Task 2 首次独立审查](task-2-review.md)
- [Task 2 修复后定向复核](task-2-fix-1-review.md)
- [截至 Task 2 的控制记录与裁决](controller-task-2-ledger.md)

仍须完成后续任务和整个 CP5 的最终审查，才能进入集成选择。此目录保留分阶段证据，不代表企业生产 SLA 或真实业务性能成果。
