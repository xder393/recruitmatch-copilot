# RecruitMatch 架构说明

## 领域边界

- Identity：企业、用户、Argon2 密码、JWT 和租户上下文。
- Job Catalog：系统模板、企业岗位、发布状态和不可变岗位版本。
- Resume Intake：文件策略、Artifact Store、文本提取、画像与处理状态。
- Matching：纯 `rules-v1` 引擎、版本化运行和 Top 3 结果。
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
7. 引擎计算技能、经验和项目维度，生成缺失/待确认/风险项并排序 Top 3。
8. 一次运行及结果在同一事务保存，记录算法版本 `rules-v1` 和 Prompt 版本 `none`。
9. 招聘人员反馈新增记录，不修改原始得分或岗位版本引用。

## 基础设施取舍

生产式 Compose 使用 PostgreSQL、Redis、Celery 和本地共享 Artifact 卷；测试使用 SQLite、临时目录和内联任务。模块化单体减少个人项目的分布式事务与部署噪声，同时保留任务和存储接口，未来可以按吞吐边界替换。

## 降级原则

确定性规则引擎是始终可用的基线。未来增加 LLM 解析或语义重排时，输出必须通过现有 Pydantic Schema，失败时回退 `rules-v1`，并在 `model_traces` 记录模型、Prompt、Token、费用、延迟和错误码。
