# CP5 分阶段验收记录

更新：2026-09-14。**Task 1–4 已验收；CP5 整体审查尚未完成。**

后续确认：用户已批准遥测专用 `service.instance.id` 例外，Task 2 已实现、Task 3 后端已接入、Task 4 实际链路已验收，现在进行整体审查。首次验收报告与控制记录保留当时“待确认”的历史状态，不表示批准被撤回。

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

## 已完成：Task 3 监控后端与仪表盘

- 基线：`26f9d9b50fe9449c888db46ba6095544e9c27409`。
- 实现：`863a4ffa25e806c956608742ee47eda3dee3b0c6`，独立审查通过，无 Critical/Important。

实际启动 Collector、Prometheus、Tempo 和 Grafana，验证四张仪表盘、两个数据源、匿名访问被拒绝、27 个业务指标及 3 个标准 HTTP 指标、正向 Trace/Metric 数据和隐私过滤。十条告警的 promtool 测试通过，但不等同于真实运行时 Firing。

最终镜像检查：399 单元测试、520 集成测试、7 项配置/实际服务测试；Ruff/format 225 个文件、CI mypy 41 个文件、离线依赖锁检查通过。15 个配置/测试/构建文件与最终镜像 SHA256 相同。测试镜像为 `sha256:ed4049093c1aef27ed4ad77e3997a32b5235d7385b57ace0d954543f1c4857f0`。

运行和验证命令见 [监控栈说明](../../observability-stack.md)。测试只注入合成遥测，不调用真实模型。后端仍保留这些测试序列，Task 4 必须按本次真实运行的数据核验，不能把已有非零值当作业务埋点成功。

## 已完成：Task 4 实际链路与故障隔离

- 基线：`ccb662824dba93597e251f0d443fa2da4a49df7d`。
- 审查与修复后提交：`e675aafb9fdd1d7a675e53b158b53bb09c21310b`。

真实 Uvicorn → Redis → Celery 双子进程上传链路、Tempo 父子关系、按实际写入实例查询的指标、Worker 重启、日志隐私、Collector 停止时业务继续及恢复均已验证。隔离的真实 Prometheus 使用合成输入触发全部十条告警；这不代表真实容量压测或物理后端故障实验。

首次完整观测测试为 123 通过、1 失败，且出现已关闭日志流错误。独立审查后，修复 Gauge 测试的源观测计时/样本身份与新鲜度，以及日志测试的处理器恢复；定向复核确认两项均已解决。生产 TTL、隐私规则和 Celery 关闭兼容代码未在该轮修改。

最终镜像 `sha256:3e5b9ea447be451796a097ec2a9c926c7fe2c14d505849404200b1a92352a3ae`：39 项定向测试、417 项单元测试、520 项集成测试、135 项完整观测测试通过，完整宿主机验收脚本退出 0。20 个相关文件与镜像 SHA256 相同。Ruff/format 233 个文件、CI mypy 41 个文件通过。控制端再次运行单元测试得到 417 通过，但出现一条既有多线程 `os.fork()` 弃用告警，留待整体审查；不能声称所有运行均无警告。

报告保留逐阶段状态；应以其末尾“Final fix result — complete”和定向复核结论为准。仅本地提交，尚未推送或合并 main。

## 验收边界与待办

1. **多进程指标身份、真实 Worker 重启及 Gauge 新鲜度已验证。** 系统生成的 Resource-only `service.instance.id` 不关联任何业务身份；不能以强制单 Worker 替代 N Worker 能力。Gauge 验证采用真实 SDK 写入实例替换，不宣称执行了真实 Beat 进程重启。
2. Task 1 的 Histogram advisory Minor 已在 Task 2 修复并覆盖亚秒 Bucket 测试；Task 3 已验证真实后端的指标命名、正向 Rate 和 P95 查询。
3. Task 4 已完成真实运行时告警 Firing、端到端 PII、跨 Broker/prefork Trace 与 Collector 停止后的业务隔离测试；仍须整个 CP5 的跨任务审查。
4. 额外扩大 mypy 检查范围发现两个既有类型问题：`app/api/v1/resumes.py:30`、`app/api/v1/knowledge.py:54` 中可空文件名传给非空响应字段。在 CP4 基线镜像中已复现相同问题；本轮未修改这些文件。当前 CI 范围通过不等于整个仓库全量类型检查无问题。
5. Task 3 两项非阻塞展示问题待最终审查处理：图例需匹配各查询保留的维度；有成功流量但无 5xx 序列时，错误率应显示零而不误显未知，真正无观测时仍保留未知。
6. Task 4 两项非阻塞测试建议留待整体审查：按本次运行增量加强指标归因；Tempo 部分链路尚未到达时继续有限重试。控制端独立复跑发现的旧 `fork()` 测试告警也需评估。

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
- [Task 3 实现和实际后端验证](task-3-report.md)
- [Task 3 独立审查](task-3-review.md)
- [截至 Task 3 的控制记录与裁决](controller-task-3-ledger.md)
- [Task 4 实现、失败恢复及最终验证](task-4-report.md)
- [Task 4 独立审查](task-4-review.md)
- [Task 4 修复后定向复核](task-4-fix-1-review.md)
- [截至 Task 4 的控制记录与裁决](controller-task-4-ledger.md)

仍须完成整个 CP5 的最终审查，才能进入集成选择。此目录保留分阶段证据，不代表企业生产 SLA 或真实业务性能成果。
