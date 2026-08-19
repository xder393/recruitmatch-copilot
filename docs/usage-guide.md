# RecruitMatch Copilot 使用与运行手册

本文说明当前仓库实际支持的功能、操作顺序、运行模式和故障处理。若本文与接口实现不一致，以当前版本的 OpenAPI `/docs` 为准。

## 1. 产品边界

RecruitMatch Copilot 是招聘人员的岗位适配辅助系统，不是自动招聘决策系统。

- 输入：企业岗位、候选人简历、企业招聘制度或能力模型。
- 输出：候选人对应岗位的 Top 3 建议、评分、原文证据、缺失项、风险提示和面试问题。
- 人工环节：招聘人员确认或驳回推荐，最终录用决定始终由人作出。
- 禁止用途：不得根据性别、年龄、照片、民族、婚育等敏感属性筛选候选人。

系统有 `admin`、`recruiter`、`lead` 三种领域角色和租户级数据隔离。当前 Web 界面只提供首位管理员的企业空间初始化与登录，没有用户邀请、账号管理或密码找回页面；多角色能力主要体现在后端权限模型与接口。

## 2. 两种运行方式

| 场景 | 数据库 | 任务执行 | 访问地址 | 适合用途 |
|---|---|---|---|---|
| 本机开发 | SQLite | inline，同一进程执行 | `http://127.0.0.1:8765` | 调试、演示、面试 |
| Docker Compose | PostgreSQL | Redis + Celery Worker | `http://localhost:8000` | 展示生产式部署结构 |

不要让两种方式共用同一个数据库来期待数据自动同步。SQLite 和 Compose 中的 PostgreSQL 是两套独立数据。

## 3. 本机开发启动

推荐 Python 3.10+（CI 使用 3.11，Docker 镜像使用 3.10）。以下命令均在仓库根目录执行：

```bash
cd /Users/xder393/Desktop/agent
python3 -m venv .venv
source .venv/bin/activate
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，至少替换 `JWT_SECRET`。可生成随机值：

```bash
openssl rand -hex 32
```

本机开发的关键配置应为：

```dotenv
DATABASE_URL=sqlite:///data/recruitmatch.db
TASK_MODE=inline
AI_ENABLED=false
```

初始化数据库和岗位模板：

```bash
./.venv/bin/python -m alembic upgrade head
./.venv/bin/python scripts/seed_job_templates.py
```

启动服务：

```bash
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

打开：

- 工作台：<http://127.0.0.1:8765/>
- OpenAPI：<http://127.0.0.1:8765/docs>
- 存活检查：<http://127.0.0.1:8765/api/v1/health/live>
- 就绪检查：<http://127.0.0.1:8765/api/v1/health/ready>

以后再次运行通常只需要：

```bash
cd /Users/xder393/Desktop/agent
source .venv/bin/activate
./.venv/bin/python -m alembic upgrade head
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

## 4. Docker Compose 启动

先安装并启动 Docker Desktop，然后执行：

```bash
cp .env.example .env
# 编辑 .env，替换 JWT_SECRET；需要 AI 时再填写模型配置
docker compose up --build
```

Compose 会覆盖 `.env.example` 中的本机默认值，在容器内使用 PostgreSQL、Redis 和 Celery。API 容器会自动执行 Alembic 迁移并初始化岗位模板。

常用命令：

```bash
docker compose ps
docker compose logs -f recruitmatch-api recruitmatch-worker
docker compose down
```

`docker compose down -v` 会删除 PostgreSQL、Redis 和模型缓存卷，可能造成数据丢失；除非明确需要重置环境，否则不要使用。

## 5. 首次账号与登录

项目没有写死的默认账号和密码。

1. 首次打开工作台，点击“首次使用？创建企业空间”。
2. 输入企业名称、工作邮箱和至少 12 位密码。
3. 创建者成为该企业空间的首位管理员。
4. 以后使用同一邮箱和密码从“登录企业空间”进入。

登录令牌保存在浏览器的 Local Storage。更换浏览器、清除站点数据、切换数据库或删除 `data/recruitmatch.db` 后，原登录状态或账号数据可能消失。

当前版本没有邮件找回密码功能。忘记密码时：

- 演示数据可丢弃：先备份，再重建本地数据库并重新创建企业空间。
- 数据必须保留：不要直接改数据库密码字段；应实现受审计的管理员重置流程后再用于真实环境。

## 6. 推荐的完整业务演示

### 6.1 创建并发布岗位

进入“岗位管理”，填写岗位名称、岗位描述、必备技能和加分技能。Web 界面的“创建并发布”会创建岗位及首个不可变版本，并将其激活。

匹配只考虑当前租户内已激活的岗位。若没有激活岗位，系统无法给出有效 Top 3。

后端还支持追加岗位版本和停用岗位；旧版本保留，历史匹配结果不会被新版本覆盖。

### 6.2 上传简历

进入“简历推荐”，上传 PDF、DOCX 或 TXT，单文件最大 10 MiB。

状态含义：

- `uploaded`：文件已接收。
- `processing`：正在解析或提取画像。
- `succeeded`：解析完成，可以推荐岗位。
- `failed`：解析失败，查看接口错误或服务日志。

同一租户重复上传相同内容时使用 SHA-256 做幂等识别。原文件名只用于展示，物理存储使用系统生成的 UUID 路径。

### 6.3 生成岗位推荐

简历状态为 `succeeded` 后点击“推荐岗位”。可选择：

| 模式 | 行为 | 是否需要模型 Key |
|---|---|---|
| `rules-v1` | 使用技能、经验、教育等确定性规则评分 | 否 |
| `hybrid-v1` | 规则分占 80%，语义证据最多贡献 20% | 是；不可用时安全降级 |

每个结果包含排序、总分、维度分、简历证据、缺失项和待确认项。Hybrid 可额外展示 RAG 引用、证据化解释和面试问题。

语义分只有在同时找到当前简历和当前岗位版本的合法引用时才成立。引用不完整、检索失败或模型异常时，系统保留规则结果并记录降级原因，不让生成内容任意改变核心排序。

### 6.4 人工反馈

招聘人员可以对结果选择“确认合适”或“不合适”。反馈是追加记录，不会覆盖历史推荐。接口还支持 `override` 改选岗位，但当前 Web 界面只展示确认和驳回。

### 6.5 查看运营状态

“运营概览”显示当前租户的岗位、简历、匹配任务和反馈数量，以及 AI 状态。

- “运行正常”：AI 已启用，且最近没有导致租户降级的模型或索引异常。
- “已降级”：AI 已启用，但最近调用被拒绝/失败或索引异常，业务回退到规则模式。
- “规则降级模式”：AI 未启用，核心业务仍可运行。

## 7. 启用 AI、Embedding 与 RAG

在 `.env` 中配置 OpenAI-compatible 服务：

```dotenv
AI_ENABLED=true
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
OPENAI_EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
```

修改 `.env` 后必须重启 API；Compose 模式还要重启 Worker：

```bash
docker compose up -d --build --force-recreate recruitmatch-api recruitmatch-worker
```

注意事项：

- 模型接口必须兼容 OpenAI 风格的结构化生成请求，并能按指定 Schema 返回 JSON。
- Embedding 默认在本地运行，首次使用会下载约 95 MiB 模型文件；网络受限时可设置 `HF_ENDPOINT=https://hf-mirror.com`。
- `AI_ENABLED=true` 只表示允许调用，不保证供应商 Key、余额、网络和模型名有效。
- 只有发生过一次真实 AI 调用后，`/api/v1/ai/status` 才能反映最近调用状态。
- 模型费用取决于供应商；可配置每百万 Token 单价用于 trace 成本估算。

### 招聘知识库

“招聘知识库”支持上传招聘制度、面试指南、能力模型和评分量表。文档按租户隔离，经过校验、分块、Embedding 和版本化索引后参与解释检索。

知识文档状态可能为 `uploaded`、`processing`、`ready`、`failed`、`inactive`。重新索引会生成新 generation；停用后不再参与检索。旧数据库在启用 AI 后，可由管理员调用：

```text
POST /api/v1/knowledge-documents/rebuild-sources
```

为已有简历和岗位版本补建语义索引。

## 8. 数据、备份与删除

本机模式的重要目录：

```text
data/recruitmatch.db        SQLite 数据库
data/resumes/               简历原始文件
data/knowledge/             招聘知识原始文件
data/index/                 旧兼容 RAG 索引
```

这些运行数据和 `.env` 均不应提交 Git。备份 SQLite 前建议停止写入：

```bash
cp data/recruitmatch.db data/recruitmatch.backup.db
```

恢复前先停止服务并保留当前数据库副本。数据库结构升级必须运行 Alembic，不能只拉取代码后直接使用旧库。

删除简历时，系统先软删除数据库记录，再清理原始 Artifact。若文件清理失败，接口返回 `503 artifact_cleanup_pending`；修复存储问题后，对同一简历 ID 再次发送 DELETE 可重试清理。

## 9. 测试与发布前验证

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check app tests scripts
DATABASE_URL=sqlite:////tmp/recruitmatch-ci.db ./.venv/bin/python -m alembic upgrade head
./.venv/bin/python scripts/evaluate_recruitment.py
./.venv/bin/python scripts/evaluate_ai_pipeline.py --mode hybrid-v1 --fake-model
```

评测数据是确定性合成样本，只能作为回归基准，不能宣传为真实招聘准确率。详细定义见 [evaluation.md](evaluation.md)。

## 10. 常见问题

### `zsh: command not found: python`

macOS 常只有 `python3`。创建虚拟环境后优先使用明确路径：

```bash
./.venv/bin/python -m alembic upgrade head
```

### 登录后接口返回 500 或数据库提示缺列

先停止服务，备份数据库，再执行：

```bash
./.venv/bin/python -m alembic upgrade head
```

确认启动服务和迁移命令读取的是同一个 `DATABASE_URL`。

### 页面显示 `Failed to fetch`

通常是 API 未启动、端口错误或进程已退出。检查：

```bash
curl -i http://127.0.0.1:8765/api/v1/health/live
```

如果使用 Compose，应访问 `http://localhost:8000`，并查看 `docker compose ps` 与容器日志。

### AI 一直显示降级

依次确认 `AI_ENABLED`、Key、Base URL、模型名、供应商余额、网络和本地 Embedding 下载。随后重新生成一次 Hybrid 推荐，再查看 `/api/v1/ai/status` 和服务日志。规则匹配不依赖模型，可用于区分业务问题和模型问题。

### 上传后一直处于处理中

- 本机模式确认 `TASK_MODE=inline`。
- Compose 模式确认 Redis 和 `recruitmatch-worker` 都是 healthy/running。
- 查看 Worker 日志中是否有解析、模型或文件权限错误。

## 11. 上线前必须补齐

当前项目适合作品集和受控演示，不应直接处理真实生产简历。正式上线至少需要：企业 SSO/MFA、用户邀请与回收、密码重置、密钥托管、对象存储、备份恢复演练、恶意文件扫描、保留期与删除策略、真实标注评测、公平性审查、告警、容量测试和隐私/劳动合规评审。

GitHub 发布步骤见 [github-publish-checklist.md](github-publish-checklist.md)。
