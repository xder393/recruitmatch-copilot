# RecruitMatch Copilot 企业化重构冻结设计

- 日期：2026-08-24
- 状态：冻结候选版，等待最终人工审核
- 适用范围：RecruitMatch Copilot 当前仓库
- 部署边界：单机 Docker Compose 演示环境

## 1. 文档地位

本文档把已逐节评审通过的六部分设计合并为 RecruitMatch 下一阶段实现的单一架构基线：

1. 目标架构、领域边界和旧 RAG 清理；
2. PostgreSQL/pgvector 招聘检索；
3. MinIO/S3 Artifact 生命周期；
4. Celery、Lease、恢复与健康检查；
5. OpenTelemetry、Prometheus、Tempo 和 Grafana；
6. non-root Docker、依赖锁定、安全扫描、CI 和性能验收。

本文与旧设计冲突时，以本文为准。特别废止以下旧条款：

- 正式运行使用 SQLite、本地共享 Artifact 目录或本地 JSON/Numpy 向量索引；
- 保留通用聊天、Agent、Conversation 或兼容性 RAG 开关；
- 在普通日志、Metric 标签或 Trace Attribute 中记录租户、用户、简历、岗位、文件名、对象 Key、Checksum 或招聘正文；
- API、Worker 启动时各自执行数据库迁移；
- Redis 保存 RecruitMatch 业务任务结果或业务状态。

旧文档仍可作为项目演进记录，但不能作为实现依据。

## 2. 目标与非目标

### 2.1 目标

将个人 Demo 收紧为具有真实招聘业务边界、可解释 AI、可靠异步处理、可复现运行环境和工程化证据的企业级形态项目。系统支持：

- 多租户岗位库和不可变岗位版本；
- PDF、DOCX、TXT 简历和招聘知识上传；
- 多岗位筛选、Top 3 推荐和规则/语义混合评分；
- 基于授权证据的 Citation 与确定性降级；
- PostgreSQL 权威状态、pgvector 检索、MinIO 私有对象存储；
- Celery at-least-once 投递下的幂等、副作用隔离和故障恢复；
- 可观测、可压测、可安全扫描、可由 GitHub Actions 重复验证的交付链。

系统仍是招聘决策辅助工具，不自动录用、淘汰或决定薪资。性别、年龄、民族、照片、婚育等敏感属性不得进入匹配输入。

### 2.2 非目标

本阶段不引入：

- Kubernetes、微服务拆分、Service Mesh、Kafka 或 Elasticsearch；
- 多 Agent 编排、模型微调、知识图谱或 OCR；
- Loki、cAdvisor、Node Exporter 或 Docker Socket；
- 云部署、多节点高可用、灾备演练或真实 SLA；
- 大并发真实 LLM 压测；
- 真实招聘效果、生产用户量、商业收益或合规认证声明。

## 3. 总体架构

### 3.1 选型

系统保持模块化单体。API、Worker 和 Beat 使用同一份业务代码和同一不可变镜像，但以独立进程运行。领域服务依赖 Port，不直接依赖 SQLAlchemy、MinIO、Celery、OpenAI SDK 或 OpenTelemetry SDK。

```text
Web UI
  |
FastAPI Routes                         Presentation
  |
Application / Domain Services          Business rules
  |
Ports                                  Stable contracts
  |-- Repository
  |-- ArtifactStore
  |-- RecruitingVectorIndex
  |-- TaskDispatcher
  |-- ModelGateway
  `-- Telemetry
  |
Adapters                               Replaceable implementations
  |-- SQLAlchemy / PostgreSQL
  |-- S3 / MinIO
  |-- pgvector
  |-- Celery / Redis
  |-- OpenAI-compatible model
  `-- OpenTelemetry
```

测试提供 Repository、ArtifactStore、RecruitingVectorIndex、TaskDispatcher、ModelGateway 和 Telemetry 的 Fake。业务 Port 不包含健康探测；健康探测属于 Operations。

### 3.2 Docker Compose 服务

完整运行环境以 Docker Compose 为唯一主路径：

```text
postgres          PostgreSQL + pgvector
redis             Celery broker only
minio             private S3-compatible object storage
minio-init        one-shot bucket/policy initialization
bootstrap         one-shot Alembic migration + idempotent seed
api               FastAPI
worker            Celery worker, N instances allowed
beat              Celery Beat, exactly 1 instance
otel-collector    application telemetry gateway
prometheus        metrics storage and alert evaluation
tempo             trace storage
grafana           provisioned dashboards
```

启动顺序为：基础依赖健康后运行 `minio-init`；随后 `bootstrap` 完成迁移和幂等种子数据；成功后启动 API、Worker 和 Beat。业务进程不自行执行迁移。

权威边界：

- PostgreSQL 是业务记录、任务状态、Lease 和索引激活状态的唯一权威；
- MinIO 是 Artifact 二进制内容的权威，PostgreSQL 保存其业务生命周期与位置；
- Redis 仅是 Celery Broker，不是业务状态数据库，也不启用 Result Backend；
- pgvector 是招聘向量检索实现；
- LLM、Embedding Provider 和遥测系统均为软依赖，不得扩大核心业务故障面。

### 3.3 正式运行与测试边界

正式运行命令为：

```bash
docker compose up --build
```

SQLite 仅用于快速单元测试、Fake Adapter 测试和不依赖 PostgreSQL 特性的部分集成测试。Alembic、pgvector、并发 Lease、Celery、MinIO 和完整 E2E 必须使用真实 Compose 依赖。

本次旧数据允许一次性丢弃：

```bash
docker compose down -v
docker compose up --build
```

这是从旧 Schema 到新基线的破坏性切换，不编写旧 JSON Vector 或本地 Artifact 数据回填。切换完成后，后续升级必须通过 Alembic 保留数据，不能继续依赖 `down -v`。

## 4. 领域和旧 RAG 清理

### 4.1 保留的招聘领域

- Identity & Tenant：企业、用户、JWT、RBAC 和租户上下文；
- Job Catalog：岗位模板、企业岗位、发布状态和不可变岗位版本；
- Resume Intake：Artifact、提取、画像、处理状态和隐私删除；
- Recruiting Knowledge：招聘政策、面试指南、能力模型和评分量表；
- Recruiting Retrieval：Resume、JobVersion、KnowledgeDocument 的授权向量检索；
- Matching：`rules-v1` 与引用约束的 `hybrid-v1`；
- Feedback：确认、驳回、改选与原因；
- Operations：健康、审计、遥测、评测和性能证据。

租户上下文只能由认证后的 `Principal` 提供。Repository 方法必须显式接收 `tenant_id`；其他租户资源与不存在资源返回相同的 404 语义。

### 4.2 删除范围

删除而非废弃：

- `app/rag/`、`app/agents/`；
- 通用 Chat、Agent、Conversation 路由和页面；
- 本地 JSON/Numpy Vector Store 与 Conversation File Store；
- `LEGACY_RAG_ENABLED` 和其他历史兼容配置；
- 仅服务于旧 RAG 的脚本、测试、文档和依赖；
- 不再使用的 LangChain Agent 依赖。

只有能够证明被招聘流程复用的解析、分块或文本规范化小工具才可迁入招聘领域命名空间。不得保留空壳、死导入或双实现。

清理验收至少包含：

```bash
rg "LEGACY_RAG|app\\.rag|app\\.agents|VectorStore|conversation"
```

命令不得返回有效生产引用。外部招聘 API 在重构期间保持版本和语义稳定；明确删除的通用 Chat/RAG API 不提供兼容层。

### 4.3 AI 与匹配不变量

`ModelGateway` 是唯一允许调用外部 Chat Model 的 Port。它负责超时、有限重试、结构化输出、Schema 校验和稳定错误分类，不读取 HTTP Request、数据库 Session 或招聘权限。测试使用 Deterministic Fake。

简历结构化事实必须关联原文 Evidence；Offset 越界、原文不匹配或无法证明的事实直接删除，不能让模型“修补”成新事实。敏感属性在进入匹配前剔除。

匹配保留两个版本：

```text
rules-v1    deterministic baseline

hybrid-v1 final_score
  = 0.80 * normalized rules-v1 score
  + 0.20 * validated semantic-project score
```

语义分必须位于 `[0, 100]`，并同时引用本次授权检索集中的 Resume 和 JobVersion Evidence。缺失、越界、Schema 错误或伪造 Citation 时，语义贡献归零并记录稳定 Fallback Reason；规则结果仍可返回。

生成式说明和面试建议只能使用 Prompt 中提供的 Citation ID。生成后必须再次执行 Citation 白名单和授权解析；无 Citation 的 Claim 被删除，未知 Citation 被拒绝。没有检索结果达到阈值时不调用模型，返回确定性的证据不足说明。

LLM 或 Embedding 未配置、超时、限流、鉴权失败或暂时不可用时，不得使登录、岗位查询、历史结果或 `rules-v1` 不可用。Model 调用数据库审计只保存模型/Prompt 版本、Token、成本估算、延迟、状态和关联对象，不保存完整 Prompt、Resume、Evidence 或 Response。

## 5. PostgreSQL 与 pgvector 检索

### 5.1 数据模型

新增统一的 `recruiting_chunks`：

| 字段 | 约束与语义 |
| --- | --- |
| `id` | 主键 |
| `tenant_id` | 必填，所有检索的第一过滤条件 |
| `document_id` | 可空，关联 Artifact/文档记录 |
| `source_type` | `resume`、`job_version`、`knowledge_document` |
| `source_id` | 必填，必须与 `source_type` 共同解释 |
| `source_version` | 必填，不可变版本或内容版本 |
| `generation` | 必填，正整数 |
| `citation_id` | 必填，租户内唯一 |
| `page_number` / `section` | 可空，安全的定位元数据 |
| `start_offset` / `end_offset` | 必填，规范文本字符区间 |
| `content` | 授权检索所需最小文本 |
| `embedding` | `vector(512)` |
| `embedding_model` | 有限配置值，包含模型及版本身份 |
| `is_active` | 是否参与当前检索 |
| `created_at` | 创建时间 |

Resume、JobVersion 和 KnowledgeDocument 各自保存：

```text
search_index_generation
search_index_status
search_index_error_code
search_indexed_at
```

`search_index_error_code` 必须来自稳定枚举，不保存异常自由文本。
`search_index_status` 只描述某个 Source 的索引子资源是否可检索，不控制 Celery Claim、Retry 或业务处理终态；第 7 节的单一处理状态仍是异步任务的唯一状态权威。

### 5.2 Embedding 契约

首个生产 Adapter 固定：

```text
dimension       = 512
normalization   = L2 normalized
distance        = cosine
pgvector opclass = vector_cosine_ops
query operator  = <=>
similarity      = 1 - cosine_distance
```

索引和查询必须携带完全一致的 `embedding_model` 身份。模型、维度、归一化或距离策略变化时必须创建新的模型身份和 generation，不能在同一检索集合中静默混用。

### 5.3 约束和索引

至少建立：

- 唯一约束：`(tenant_id, citation_id)`；
- 唯一约束：`(tenant_id, source_type, source_id, source_version, generation, start_offset, end_offset)`；
- 当前来源查询索引：`(tenant_id, source_type, source_id, source_version, generation)`，仅 `is_active = true`；
- 文档索引：`(tenant_id, document_id)`，仅当前有效行；
- HNSW：`embedding vector_cosine_ops`，仅 `is_active = true`。

最低 pgvector 版本为 0.8。允许使用 iterative scan，但不能宣称 HNSW 在高选择性过滤下天然保证 Recall。

### 5.4 授权检索

检索 SQL 必须在距离排序前应用：

```text
tenant_id
is_active = true
embedding_model
authorized source_type
authorized source_id/source_version set
```

禁止检索全租户向量后在 Python 中过滤。禁止单独依靠 `source_id` 查询、删除或解析 Citation。

默认策略：授权候选行不超过 `VECTOR_EXACT_SEARCH_MAX_CANDIDATES=10000` 时使用精确过滤检索；超过阈值时使用 HNSW iterative scan。性能报告同时测量精确过滤与 HNSW 路径的延迟和 Recall，后续只能用可重复基准调整阈值。

### 5.5 Generation 切换

重新索引时：

1. 读取当前 generation `N`；
2. 写入 `N+1`，所有新 Chunk 初始 `is_active=false`；
3. 验证数量、Embedding 身份和 Citation 完整性；
4. 在同一 PostgreSQL 事务中停用 `N`、激活 `N+1` 并更新 Source generation/status；
5. 任一步失败时保留 `N` 为活动版本，`N+1` 留待清理，不向读者暴露半成品。

激活事务还必须验证处理任务持有的 Lease fencing token，旧 Worker 不得切换新 generation。

### 5.6 Citation 生命周期

提供两个明确操作：

- `resolve_active_citations`：只解析当前活动 generation，用于新生成和当前 Grounding；
- `resolve_historical_citation`：解析仍被允许保留的历史证据，用于授权审计，不要求 `is_active=true`。

生命周期规则：

- Active Chunk：可用于新检索和审计；
- Inactive Chunk：只允许授权审计，不用于新检索；
- 隐私删除：删除或不可逆清除 Chunk 正文、Embedding、Match Evidence 和 Citation 解析能力，不保留敏感历史证据。

## 6. MinIO/S3 Artifact 生命周期

### 6.1 存储和权限

使用一个私有 Bucket：`recruitmatch-artifacts`。对象 Key 由服务端生成：

```text
tenants/{tenant_id}/resumes/{owner_id}/{artifact_id}
tenants/{tenant_id}/knowledge/{owner_id}/{artifact_id}
```

Key 不包含原始文件名或扩展名。业务层接收不可变、强类型的 `ArtifactLocation`，不接收任意字符串 Key。

`minio-init` 使用管理凭据创建 Bucket 和策略后退出。API/Worker 使用独立的最小权限应用凭据，只能在允许的 Bucket/Prefix 内执行必要的 Put、Head、Get 和 Delete，不能管理 Bucket、用户或策略。

### 6.2 Artifact 状态机

Artifact 是独立数据库实体，状态为：

```text
PENDING
  |-- upload verified --> AVAILABLE
  `-- unrecoverable ----> FAILED

AVAILABLE / FAILED
  `-- privacy delete ---> CLEANUP_PENDING

CLEANUP_PENDING
  |-- S3 delete success -> DELETED
  `-- S3 delete failure -> CLEANUP_FAILED

CLEANUP_FAILED
  `-- retry ------------> CLEANUP_PENDING
```

Artifact 状态不能替代 Resume/Knowledge 的处理状态，两者描述不同生命周期。

### 6.3 上传流程和幂等

最大文件 10 MiB，不使用 multipart upload。流程为：

1. 流式读取到有界临时文件，验证扩展名、MIME、大小及解压后安全边界，并计算 SHA-256；
2. PostgreSQL 在租户、Owner、Artifact 类型和未删除 Checksum 维度建立唯一性，先解决并发幂等；
3. 创建 Owner 和 `PENDING` Artifact，生成 `ArtifactLocation`；
4. 上传 MinIO，并通过 S3 `ChecksumSHA256` 能力及集成测试验证内容；不得把 ETag 当作 SHA-256；
5. 验证成功后将 Artifact 置为 `AVAILABLE`、业务 Source 置为 `QUEUED`，再提交 Celery 消息；
6. Dispatch 失败不回滚业务记录，由 PostgreSQL 状态和 Beat 恢复投递。

删除后的 Artifact 必须清除或替换幂等 Checksum，使相同文件在完成隐私删除后可以重新上传。

### 6.4 Worker 读取

Celery 参数只传 `tenant_id` 和业务 Owner ID。Worker 通过 Repository 解析已授权 `ArtifactLocation`，不从消息接收对象 Key。

读取前执行 Head 并验证大小；读取最多 `limit + 1` 字节，超限立即失败；所有流必须在 `finally`/上下文管理器中关闭。对象不存在、Checksum 不符、权限失败和超限使用稳定错误码。

### 6.5 隐私删除

删除 Resume/Knowledge 时，先在 PostgreSQL 事务中：

- 将业务 Source 标记为已删除且对普通查询不可见；
- 立即清除或不可逆脱敏画像和正文；
- 删除相关 Chunk、Embedding、Match Evidence 和 Citation；
- 将 Artifact 置为 `CLEANUP_PENDING`。

随后删除 MinIO 对象。删除成功后置 `DELETED`；失败时置 `CLEANUP_FAILED`，DELETE 返回 503 和稳定错误码，允许客户端或 Beat 重试。数据库隐私数据清理不能等待对象存储恢复。

### 6.6 对账

定时对账只做有界修复：

- `PENDING + valid object` 可修复为 `AVAILABLE` 并恢复队列状态；
- `PENDING + missing/invalid object` 标记稳定失败；
- `CLEANUP_PENDING/CLEANUP_FAILED` 重试删除；
- 发现无法关联数据库的对象只输出不含敏感标识的候选数量和受控管理报告，不自动删除。

运行期健康探测只验证 Bucket 当前可访问，不查询 `minio-init` 历史状态，也不写测试对象。Put/Get/Delete 由集成测试覆盖。

## 7. Celery、Lease 与恢复

### 7.1 单一业务状态权威

每个异步 Source 只有一个处理状态字段：

```text
QUEUED -> RUNNING -> SUCCEEDED
                  `-> FAILED
```

删除属于独立 `lifecycle_status`，不能复用处理状态或再增加第二套 `processing_status/status`。实际迁移可保留现有字段名，但最终只能存在一个处理状态权威。

`FAILED` 是业务终态。普通重复投递必须 no-op；运营恢复或 Beat 策略必须先在 PostgreSQL 显式执行 `FAILED -> QUEUED`，再 Dispatch。

### 7.2 Lease 字段

Source 保存：

```text
processing_attempts
processing_lease_epoch BIGINT NOT NULL
processing_lease_expires_at
processing_lease_owner
next_retry_at
processing_error_code
queued_at
```

`processing_lease_epoch` 是单调递增 fencing token。

### 7.3 Claim、续租和提交

Claim 在数据库事务中锁定 Source 行。只有符合策略的 `QUEUED` 或 Lease 已过期记录可以被 Claim；成功后：

```text
status = RUNNING
processing_attempts += 1
processing_lease_epoch += 1
processing_lease_owner = bounded worker identity
processing_lease_expires_at = now + lease
```

Worker 保存本次 `claimed_epoch`。续租、结果激活、成功和失败写入都必须匹配：

```sql
WHERE tenant_id = :tenant_id
  AND id = :source_id
  AND processing_lease_epoch = :claimed_epoch
```

`affected_rows=0` 表示 Worker 已失去所有权；它必须停止提交，且不得激活 generation、覆盖状态或产生可见副作用。长任务每 `lease / 3` 续租一次。所有可见派生数据先写入非活动 generation，只有带 fencing 条件的最终事务可以激活。

### 7.4 重复投递

当重复消息发现 `RUNNING + valid lease`：

```text
return -> ACK duplicate
```

不得 `self.retry()`，不得增加 `processing_attempts`，不得创建新 retry message。这样 at-least-once delivery 不会变成 at-least-once side effects。

### 7.5 Celery 参数关系

固定：

```text
task_acks_late = true
task_reject_on_worker_lost = true
worker_prefetch_multiplier = 1
task_ignore_result = true
```

并保持：

```text
soft_time_limit
    < hard_time_limit
    < Redis visibility_timeout

visibility_timeout
    > max(hard_time_limit, maximum Celery countdown)
      + safety margin
```

短/中等退避可以使用有限 Celery countdown；长周期恢复写入 PostgreSQL `next_retry_at`，由 Beat 扫描后重新入队。不能通过极长 visibility timeout 掩盖设计问题，因为它也会延迟 Worker 强杀后的消息恢复。

### 7.6 故障分类

- Permanent：格式不支持、内容超限、不可解析等，写 `FAILED`；
- Transient：网络、MinIO、模型限流或临时外部故障，有限重试；
- AI Soft Failure：保留确定性规则结果并记录 Fallback，不使核心匹配失败；
- PostgreSQL Failure：若数据库不可用，Worker 无法保证写入 `FAILED`，保留旧 Lease；数据库恢复后由 Beat 根据过期 Lease 恢复。

### 7.7 Beat

Beat 只能运行一个实例，只执行：

```text
scan PostgreSQL
  -> find queued stale / expired lease / due retry / cleanup pending
  -> perform guarded state transition
  -> dispatch task
```

Beat 不执行解析、Embedding、对象读取或匹配重活。Worker 可扩展为 N 个实例。Beat 维护 Tick Age 指标；Worker 心跳身份可存 Redis 运维 Key，但不能成为 Metric 标签。

## 8. 健康检查

### 8.1 `/api/v1/health/live`

只检查 API 进程和 Event Loop，成功返回 200。不得访问外部依赖。

### 8.2 `/api/v1/health/ready`

硬依赖：

- PostgreSQL 可查询；
- Alembic revision 等于代码 Head；
- pgvector Extension 存在且版本满足最低要求；
- Redis Broker 可用；
- Artifact Bucket 当前可访问。

任一硬依赖失败返回 503。Worker Stale、Beat Stale、LLM、Embedding、Collector、Prometheus、Tempo 或 Grafana 故障不单独使 API Readiness 失败。

### 8.3 `/api/v1/health/system`

管理员权限接口，用于回答完整工作流能否运行，返回有限状态：

```text
api
database
redis
minio
worker
beat
ai
telemetry
overall = ok | degraded | unavailable
```

不得返回主机名、连接 URL、Bucket 名、凭据、对象 Key或 Stack Trace。Worker 或 Beat 不可用使整体为 `degraded`，但不改变 API `/ready`。

## 9. OpenTelemetry、Prometheus、Tempo 与 Grafana

### 9.1 唯一遥测链路

```text
API / Worker / Beat
        |
       OTLP
        v
OpenTelemetry Collector
        |-- traces via OTLP --> Tempo
        `-- metrics via OTLP/HTTP --> Prometheus OTLP receiver

Prometheus --scrape--> Collector :8888 internal metrics
Grafana --> Prometheus + Tempo only
```

业务代码只使用 OpenTelemetry API，禁止引入 `prometheus_client` 维护第二套应用指标。Prometheus 显式启用 OTLP Receiver。Collector 自身的 `:8888/metrics` 是唯一 Scrape 例外。

本阶段不引入 Loki。应用向 stdout 输出结构化 JSON，包含 `request_id`、`trace_id`、`span_id` 和稳定错误码；不包含业务正文或原始业务身份标识。

### 9.2 标准和自定义 Instrument

HTTP、DB、Redis 和 HTTP Client 优先使用 OpenTelemetry Semantic Conventions。固定：

```text
OTEL_SEMCONV_STABILITY_OPT_IN=http
```

不使用 `http/dup`，不自定义重复的 `recruitmatch_http_*`。锁定 Instrumentation 版本后由 CI 验证实际 Metric 名称。

领域 Instrument 使用 OTel 命名和 UCUM Unit；`_total`、`_seconds` 等只属于 Prometheus 渲染层：

| OTel Instrument | Type | Unit / 说明 |
| --- | --- | --- |
| `recruitmatch.task.started` | Counter | `{task}` |
| `recruitmatch.task.completed` | Counter | `{task}` |
| `recruitmatch.task.failed` | Counter | `{task}` |
| `recruitmatch.task.retry` | Counter | `{task}` |
| `recruitmatch.task.duration` | Histogram | `s` |
| `recruitmatch.queue.depth` | Gauge | PostgreSQL `QUEUED` 数量 |
| `recruitmatch.queue.oldest_age` | Gauge | `s`，来自 PostgreSQL `queued_at` |
| `recruitmatch.lease.takeover` | Counter | `{takeover}` |
| `recruitmatch.lease.renew_failure` | Counter | `{failure}` |
| `recruitmatch.worker.live` | Gauge | 非 Stale Worker 聚合数量 |
| `recruitmatch.worker.oldest_heartbeat_age` | Gauge | `s`，不带 Worker ID |
| `recruitmatch.beat.tick_age` | Gauge | `s`，Beat 单实例 |
| `recruitmatch.artifact.operation` | Counter | `{operation}` |
| `recruitmatch.artifact.operation.duration` | Histogram | `s` |
| `recruitmatch.artifact.cleanup_pending` | Gauge | `{artifact}` |
| `recruitmatch.vector.search.duration` | Histogram | `s` |
| `recruitmatch.vector.search.results` | Histogram | `{result}` |
| `recruitmatch.vector.indexed_chunks` | Counter | `{chunk}` |
| `recruitmatch.model.request` | Counter | `{request}` |
| `recruitmatch.model.duration` | Histogram | `s` |
| `recruitmatch.model.tokens` | Counter | `{token}` |
| `recruitmatch.model.fallback` | Counter | `{fallback}` |
| `recruitmatch.model.schema_failure` | Counter | `{failure}` |
| `recruitmatch.citation.rejection` | Counter | `{rejection}` |
| `recruitmatch.match.completed` | Counter | `{match}` |
| `recruitmatch.match.duration` | Histogram | `s` |
| `recruitmatch.match.score` | Histogram | `1`，0 到 100 分布 |

`queue.depth` 和 `queue.oldest_age` 描述 PostgreSQL 中等待处理的业务 Source，不表示 Redis 内部消息数量。

### 9.3 Trace

自动采集 FastAPI、SQLAlchemy、Redis、HTTP Client 和 Celery。显式业务 Span：

```text
resume.upload
artifact.put / artifact.get / artifact.delete
resume.process / resume.parse
embedding.generate
vector.index / vector.search
matching.run
model.generate
citation.validate
lease.claim / lease.renew / lease.finalize
beat.recover
```

不为单个 Chunk 创建 Span；Embedding 使用批次级 Span。LLM 和 Embedding 使用显式业务 Span，外部模型调用可在其下包含自动 HTTP Client Span。本地 Embedding 不要求 HTTP Span。

API 到 Celery 只传播 `traceparent` 和 `tracestate`。禁止使用 RecruitMatch 业务 Baggage。Beat 恢复任务创建新的 Root Trace，`recovery_reason` 使用稳定枚举。

### 9.4 PII 与低基数

允许的 Attribute/Label 必须来自有限集合，例如：

```text
service.name
deployment.environment
http.route template
http.request.method
http.response.status_code
task.type
source.type
operation
outcome
error.code
model.provider
model.name
match.mode
retrieval.strategy
recovery.reason
```

禁止写入普通日志、Metric Label、Trace Attribute 或 Baggage：

```text
tenant_id / user_id / resume_id / job_id / artifact_id
姓名 / 邮箱 / 电话 / 文件名
S3 Key / checksum
简历正文 / JD 正文 / evidence / citation 原文
prompt / model response
SQL 参数 / token / credential
```

`error.code`、`model.name`、`recovery.reason` 只能取配置或代码中的稳定枚举，不能使用异常文本或客户端自由输入。需要身份关联的安全审计写入受授权的 PostgreSQL `audit_logs`，它不是 OTel Trace，也不向 Grafana 暴露。

应用层遵守“不采集”；Collector 作为第二道防线，顺序固定为：

```text
memory_limiter
  -> filter / redaction
  -> attributes / transform
  -> batch
  -> exporter
```

### 9.5 软依赖与采样

应用使用 `BatchSpanProcessor` 和 `PeriodicExportingMetricReader`，配置有界队列、有限 Batch 和短 Export Timeout。Collector 网络 Exporter 配置有限 `sending_queue`、有限重试和非阻塞 Overflow。

原则为：

```text
business availability > telemetry completeness
```

Backend 不可用时允许丢失遥测并通过 Collector 内部指标告警，不允许阻塞 API、Worker 或 Beat，也不影响 Readiness。

Compose 演示使用 `ParentBased(TraceIdRatioBased(1.0))`；正式压测默认 0.1。Metrics 不采样。Beat 新 Root Trace 独立采样。

### 9.6 Grafana 与网络边界

Provision 以下 Dashboard：

1. System Overview：HTTP RED、依赖状态、Worker 数、队列、Collector/Backend 健康；
2. Async Reliability：任务结果、重试、Lease、Beat、Cleanup；
3. AI & RAG Pipeline：模型、Embedding、检索、Fallback、Schema、Citation；
4. Recruiting Business：聚合处理量、匹配量、评分和推荐量。

Grafana 只配置 Prometheus 和 Tempo Datasource，不连接招聘 PostgreSQL。System Overview 不承诺容器 CPU/RAM 指标，也不挂载 Docker Socket。

只有 Grafana 发布到 `127.0.0.1:3000`；Prometheus、Tempo 和 Collector 管理端口只存在于内部网络。Grafana 禁止匿名访问，凭据来自 `.env`。API 同样只绑定本机演示端口。

告警覆盖 HTTP 5xx、API P95、Queue Oldest Age、Worker 为零、Beat Stale、Artifact Cleanup、AI Fallback、Collector Export Failure 和 Lease Takeover。AI Fallback 告警表示 Pipeline Degraded，不表示系统 Down。不声明虚构的 SLA。

## 10. Docker、依赖与供应链

### 10.1 依赖权威

`pyproject.toml` 是唯一声明，提交 `uv.lock` 和 `.python-version`，删除手写 `requirements.txt`。统一 Python 3.12：

```text
[project.dependencies]       runtime
[dependency-groups].dev      test/lint/type/security helpers
[dependency-groups].eval     offline evaluation
[dependency-groups].load     report helpers
```

CI 执行 `uv lock --check` 和 `uv sync --frozen`；Runtime 构建执行 `uv sync --frozen --no-dev`。任何构建或测试都不得隐式更新 Lock。

### 10.2 多阶段 non-root 镜像

Builder 安装锁定依赖并构建虚拟环境；Runtime 只复制虚拟环境、应用、迁移和必要静态资源。API、Worker、Beat 和 Bootstrap 使用同一镜像。

运行用户固定 UID/GID 10001，名字 `recruitmatch`。禁止把密钥或 `.env` Bake 进镜像；Hugging Face Cache 位于 `/home/recruitmatch/.cache/huggingface` 并使用具备正确 Ownership 的 Volume。

Compose 对业务容器启用：

```text
read_only: true
tmpfs: /tmp
security_opt: no-new-privileges:true
cap_drop: ALL
```

只为明确需要的目录挂载可写 Volume。基础镜像、uv 镜像和关键基础设施镜像使用版本加 Manifest Digest；第三方 GitHub Actions 固定完整 Commit SHA。仓库包含严格 `.dockerignore`。

CI 通过 `docker run --rm IMAGE id -u` 证明 UID 不为 0，并验证镜像内没有开发依赖、源码外秘密或意外可写系统目录。

### 10.3 安全门禁

- OSV-Scanner：`uv.lock`/依赖漏洞；
- Bandit：Python 静态安全；
- Gitleaks：当前提交与 Git 历史秘密；
- Trivy：最终 Runtime Image 的 OS 和 Python 依赖；
- CycloneDX SBOM：作为 CI Artifact。

存在可修复的 High/Critical 时 CI 失败。例外只能写入版本控制的忽略文件，并包含漏洞编号、影响判断、原因、负责人和到期日期；禁止全局忽略 High/Critical。

本阶段不引入 CodeQL、Cosign、私有镜像仓库或制品签名。

## 11. 测试与 GitHub Actions

### 11.1 LLM 测试矩阵

| 测试类型 | 模型 | 目的 |
| --- | --- | --- |
| 单元测试 | Fake/Mock | 业务逻辑 |
| CI 集成测试 | Fake/Stub | 稳定、重复、零费用 |
| 正式性能压测 | Fake/Stub | API、DB、Redis、Celery 容量 |
| AI Pipeline 回归 | Fake Model | Fallback、Schema、Citation |
| 真实 LLM Smoke | 真实模型，手动 | 供应商接口可用性 |
| 真实 LLM 小规模延迟 | 真实模型，可选、手动 | 端到端体验 |
| 大并发真实 LLM 压测 | 不执行 | 成本高且结论价值低 |

普通 CI 不访问真实 LLM、不读取个人 `.env`，也不要求模型 Key。

### 11.2 测试层级

必须覆盖：

- 单元：状态机、Schema、规则、Citation、PII、Metric 属性和 Fake Ports；
- PostgreSQL 集成：Alembic、pgvector、Generation、授权查询、精确/HNSW 策略；
- MinIO 集成：Put/Head/Get/Delete、Checksum、超限、幂等、隐私删除和对账；
- Celery 集成：late ACK、重投、恢复、Time Limit、Beat 和 DB 故障；
- 并发：Lease Fencing Race、Valid-Lease Duplicate ACK、Generation 原子切换；
- API E2E：认证、上传、状态、推荐、Citation、删除和租户隔离；
- AI 回归：Fallback、Schema Repair、Citation 拒绝和确定性结果；
- 遥测 E2E：API 到 Worker 同一 Trace、Prometheus 实际查询、Tempo 实际查询、PII Negative Test、Collector Down Business Survives；
- Dashboard/Config：Collector 校验、Grafana Provision 和 Compose Config。

### 11.3 CI Jobs

```text
validate
  uv lock / ruff check / ruff format / mypy / compose config

unit
  SQLite + Fake Model + Fake Adapters

integration
  PostgreSQL/pgvector + Redis + MinIO + Celery + Alembic + E2E

ai-regression
  deterministic Fake Model golden dataset

telemetry-e2e
  Prometheus + Tempo + context propagation + outage behavior

security
  OSV-Scanner + Bandit + Gitleaks

image
  multi-stage build + non-root assertion + Trivy + SBOM

performance-smoke
  60-second low-concurrency Fake-AI k6 thresholds
```

Push 和 Pull Request 均运行；同一分支的新提交取消旧运行；GitHub Token 使用最小权限。失败日志和测试/扫描报告作为 Artifact 保存。

README Badge 指向 `xder393/recruitmatch-copilot` 的 `main` 分支和实际 Workflow。只有远程 GitHub Actions 全部成功后才能声明 CI 绿灯；本地通过不能替代远程证据。

## 12. 性能压测与报告

### 12.1 运行方式

k6 通过 Compose `loadtest` Profile 运行：

```bash
docker compose --profile loadtest run --rm k6
```

宿主机只要求 Docker。正式压测使用真实 FastAPI、PostgreSQL/pgvector、Redis/Celery、MinIO、Worker/Beat 和 OTel 栈，使用 512 维 Deterministic Fake Embedding 和 Fake LLM。

### 12.2 场景

1. Read：登录、岗位列表、筛选、详情和已有匹配；
2. Async Workflow：上传测试简历、轮询处理、岗位推荐和 Citation；
3. Mixed：70% 查询、20% 筛选/匹配、10% 上传和异步处理。

正式阶段：

```text
warm-up   2 minutes
steady   10 minutes
step     10 -> 25 -> 50 VUs
drain    wait until business queue drains
```

CI 只执行 60 秒低并发 Smoke；正式压测由手动 Workflow 或本地 Compose 执行。

### 12.3 初始性能预算

- HTTP 错误率 `< 1%`；
- 同步查询 P95 `< 500 ms`；
- 同步查询 P99 `< 1000 ms`；
- 上传受理 P95 `< 750 ms`；
- Fake-AI 异步端到端 P95 `< 15 s`；
- 压测停止后业务 Queue 在 60 秒内排空；
- 不残留无有效 Lease 的 `RUNNING`；
- 不出现重复副作用、Fencing 覆盖、Generation 半切换或 Artifact Cleanup 积压；
- Collector 故障实验中业务错误率仍满足预算。

这些是单机 Compose 回归预算，不是生产 SLA。若实际首次基准不能满足，不得篡改报告；先记录瓶颈，再通过经评审的设计变更调整预算或实现。

### 12.4 报告

机器执行生成：

```text
docs/performance/latest.md
artifacts/performance-summary.json
```

报告包含 Git SHA、时间、CPU/内存、Docker 版本、服务副本数、数据规模、文件大小、Fake Model、Trace Sampling、VU/阶段、RPS、错误率、P50/P95/P99、异步延迟、Queue Depth、Drain Time、pgvector 路径、Collector 开销、达标项和瓶颈。不得手工填写虚构数字，也不得把一次本机结果描述成生产容量。

## 13. 运维和文档交付

README 和使用指南必须说明：

- 唯一 Compose 启动路径；
- `.env.example` 中每个变量及安全默认值；
- 首次启动、停止、查看日志、迁移、幂等 Seed；
- 破坏性 `docker compose down -v` 的影响；
- Fake AI、关闭 AI、真实 LLM Smoke 三种模式；
- Grafana 访问和 Dashboard 含义；
- `/live`、`/ready`、管理员 `/health/system` 的区别；
- 单元、集成、AI 回归、Telemetry、Security、Load Test 命令；
- Artifact Cleanup、Lease、Beat、Queue 和模型降级排障；
- 隐私删除和数据保留边界；
- 本项目没有真实生产 SLA、用户量或招聘效果证明。

可提供 Makefile 作为 Compose 命令的便捷封装，但不得形成第二条运行路径；每个 Make Target 必须只调用 Docker Compose。

## 14. 实施边界与验收

实现完成必须同时满足：

1. 旧通用 RAG、Agent、Conversation、Local Vector 和相关依赖已删除；
2. `docker compose up --build` 从空 Volume 启动完整环境；
3. PostgreSQL 是业务状态权威，pgvector 是招聘向量实现，MinIO 是二进制权威；
4. API、Worker、Beat、Bootstrap 以 non-root 运行；
5. Lease Fencing、Duplicate ACK、Beat Recovery 和 Generation Switch 并发测试通过；
6. Readiness、System Health 和 Soft Dependency 边界符合本文；
7. Prometheus 与 Tempo 能查询真实业务 Metric/Trace，遥测故障不阻塞业务；
8. SQLite 未出现在正式 Compose 运行链；
9. `uv.lock`、安全扫描、SBOM 和 Image Scan 进入 CI；
10. Fake-AI 回归和正式 Compose 性能报告由实际命令生成；
11. GitHub Actions 远程全绿，README Badge 显示 Passing；
12. README 不夸大单机演示为真实生产系统。

冻结后只允许通过显式 Architecture Decision 或设计修订改变以下核心不变量：

- 单机 Docker Compose 主路径；
- PostgreSQL 单一业务状态权威；
- Redis Broker-only；
- MinIO 私有 Artifact；
- pgvector 授权前置过滤；
- Fencing Token 防止旧 Worker 提交；
- AI/Telemetry Soft Dependency；
- 业务可用性高于遥测完整性；
- Fake/Stub 承担 CI 和正式并发压测；
- 不声明没有证据支持的企业 SLA 或业务结果。

## 15. 参考资料

- [Celery Redis broker visibility timeout](https://docs.celeryq.dev/en/v5.6.2/getting-started/backends-and-brokers/redis.html)
- [pgvector filtering and iterative index scans](https://github.com/pgvector/pgvector#filtering)
- [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/)
- [OpenTelemetry sensitive-data guidance](https://opentelemetry.io/docs/security/handling-sensitive-data/)
- [OpenTelemetry HTTP semantic conventions](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/)
- [Prometheus OTLP ingestion](https://prometheus.io/docs/guides/opentelemetry/)
- [Grafana Tempo through OpenTelemetry Collector](https://grafana.com/docs/tempo/latest/set-up-for-tracing/instrument-send/set-up-collector/)
- [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
- [uv in Docker](https://docs.astral.sh/uv/guides/integration/docker/)
- [Docker build best practices](https://docs.docker.com/build/building/best-practices/)
- [OSV-Scanner source scanning](https://google.github.io/osv-scanner/usage/scan-source)
- [k6 thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/)
