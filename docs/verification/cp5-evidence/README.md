# CP5 分阶段验收记录

更新：2026-09-14。**仅 Task 1 已验收；CP5 尚未完成。**

## 已完成：OTel 适配器与隐私策略

- 基线：`f297b0a509fca92b37de1be174e18a601ccfd64e`（已合并的 CP4）。
- 实现：`5abe44f74eab7ed962d41de63da212a8b4f20990`。
- 修复：`d9dc9ab3aeaa935c0bcf2f596bd6c6add511be92`。
- 本地分支：`codex/recruitmatch-cp5-observability`。未推送、未创建 CP5 PR、未合并 main。

提供兼容既有业务接口的事件记录和操作范围、27 个规范指标、安全 Tracer/Meter、有限队列/导出超时、可隔离启停的 SDK 生命周期。默认遥测关闭。业务埋点、实际 Collector/Prometheus/Tempo/Grafana 和跨 Worker E2E 尚未接入；这些是后续任务，不能以本阶段单元测试替代验收。

独立审查发现并修复了两项 Important：真实 OTLP 错误日志可能泄露异常内容，以及直接 Meter 调用绕过评分 0–100 范围。修复后定向复核确认均已解决，无新增 Critical/Important。

## 最终本地证据

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

## 待确认与未完成项

1. **设计确认：多进程指标身份。** 相同资源标识的多个累计指标写入者会发生冲突；单纯改为 Delta 也不能证明正确。已请求允许系统生成的遥测专用 `service.instance.id`，不关联租户、用户、简历、任务或 Worker 心跳 ID。尚未获批，尚未修改白名单；不能以强制单 Worker 替代冻结的 N Worker 能力。
2. Task 2：实际框架/业务埋点、W3C 传播、JSON 日志、权威聚合指标；验证启用遥测时的 prefork 子进程和 API 中间件初始化顺序。
3. 已登记 Minor：Histogram advisory 当前被丢弃，HTTP 埋点需要明确的亚秒 Bucket，之后才能验收有意义的 P95 图表。
4. Task 3/4：真实后端、四张 Dashboard、告警 Firing、PII 查询检查与 Collector 停止后的业务隔离测试。
5. 额外扩大 mypy 检查范围发现两个既有类型问题：`app/api/v1/resumes.py:30`、`app/api/v1/knowledge.py:54` 中可空文件名传给非空响应字段。在 CP4 基线镜像中已复现相同问题；本轮未修改这些文件。当前 CI 范围通过不等于整个仓库全量类型检查无问题。

## 记录索引

- [控制记录与实施裁决](controller-ledger.md)
- [实现、RED/GREEN 与修复报告](task-1-report.md)
- [首次独立审查](task-1-review.md)
- [修复后定向复核](task-1-fix-1-review.md)
- [Task 2 接口与统计语义交接](task-2-seams.md)

仍须完成后续任务和整个 CP5 的最终审查，才能进入集成选择。此目录保留分阶段证据，不代表企业生产 SLA 或真实业务性能成果。
