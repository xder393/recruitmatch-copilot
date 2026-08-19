# RecruitMatch 架构说明

## 领域边界

- Identity：企业、用户、Argon2 密码、JWT 和租户上下文。
- Job Catalog：系统模板、企业岗位、发布状态和不可变岗位版本。
- Resume Intake：文件策略、Artifact Store、文本提取、画像与处理状态。
- Recruiting Knowledge：租户隔离的招聘制度、面试指南、能力模型、评分量表及 generation-safe 向量分块。
- AI Gateway：OpenAI-compatible 结构化输出、Prompt 版本、Schema 校验、Token/费用/延迟追踪。
- Matching：兼容 `rules-v1`，以及固定 80% 规则分 + 最多 20% 引用约束语义分的 `hybrid-v1`。
- Feedback：确认、驳回和改选的追加式记录。
- Operations：健康检查、租户聚合、审计日志和模型调用记录结构。

领域服务不从请求体读取租户 ID。认证依赖从 JWT 构造 `Principal(user_id, tenant_id, role)`，仓储方法显式接收 `tenant_id`。其他租户的资源 ID 与不存在资源使用相同 404 响应，降低 IDOR 枚举风险。

## 推荐数据流

1. 简历上传先执行格式/MIME/大小校验和 SHA-256 租户内去重。
2. 原文件以 `{tenant_id}/{uuid}.{ext}` 保存，数据库记录进入 `queued`。
3. Inline 或 Celery Dispatcher 调用处理服务，状态变为 `running`。
4. 提取器生成规范文本；画像器只保存有原文证据的技能与明确年限。
5. 处理成功后状态变为 `succeeded`，失败保存稳定错误码。
6. 匹配服务只加载同租户、已发布岗位的当前不可变版本。
7. 规则引擎计算技能、经验和项目维度；`hybrid-v1` 额外读取当前简历/JD 分块，模型语义分必须各引用至少一条授权证据。
8. 最终分固定为 `0.8 × rule_score + 0.2 × validated_semantic_score`；缺失、越界或伪造引用使语义贡献归零并记录 fallback reason。
9. 生成式说明只保留引用 ID 可解析到本次授权检索集的结论；无证据时不调用模型，回退确定性说明。
10. 一次运行保存算法、Prompt、规则/语义/最终分、引用、grounding 状态和降级原因。
11. 招聘人员反馈新增记录，不修改原始得分或岗位版本引用。

## RAG 数据流与幻觉边界

招聘知识原文件使用独立 Artifact 命名空间；SHA-256 保证租户内幂等。处理任务提取文本、精确偏移分块、本地 BGE Embedding，并以 generation 切换保证重建过程中旧索引持续可用。检索首先限定租户、激活状态和来源类型，再计算相似度。引用 ID 由租户、来源、版本和偏移稳定派生；模型无法凭空创建授权引用。

## 基础设施取舍

生产式 Compose 使用 PostgreSQL、Redis、Celery 和本地共享 Artifact 卷；测试使用 SQLite、临时目录和内联任务。模块化单体减少个人项目的分布式事务与部署噪声，同时保留任务和存储接口，未来可以按吞吐边界替换。

## 降级原则

确定性规则引擎是始终可用的基线。AI 关闭、无证据、超时、限流、输出 Schema 错误或引用校验失败时均回退规则路径；模型调用只记录指纹、版本、Token、费用、延迟和错误码，不记录简历/知识正文或原始 Prompt。
