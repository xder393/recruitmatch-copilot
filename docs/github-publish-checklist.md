# GitHub 发布清单

这份清单用于把 RecruitMatch Copilot 作为求职作品集公开。发布前逐项确认，尤其不要把模型 Key、候选人简历和本地数据库推到远程。

## 1. 绝不能提交的内容

- `.env`、API Key、JWT Secret、云厂商密钥或私人 Token。
- `data/*.db`、数据库备份、PostgreSQL 导出文件。
- `data/resumes/` 和 `data/knowledge/` 中的真实或测试上传文件。
- 真实候选人姓名、邮箱、电话、公司内部制度或聊天记录。
- Hugging Face 模型缓存、虚拟环境、IDE 配置和系统临时文件。

仓库只应保留 `.env.example`，其中不能包含可用凭据。

## 2. 本地检查

在仓库根目录执行：

```bash
git status --short
git diff --check
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check app tests scripts
```

确认敏感运行文件被忽略：

```bash
git check-ignore -v .env data/recruitmatch.db
git ls-files .env 'data/*.db' 'data/resumes/*' 'data/knowledge/*'
```

第二条命令正常情况下不应列出私密运行文件；目录占位用的 `.gitkeep` 可以保留。

扫描当前已跟踪内容中的常见秘密格式：

```bash
git grep -n -I -E 'sk-[A-Za-z0-9_-]{16,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|Bearer [A-Za-z0-9._-]{20,}' || true
```

命中后必须人工判断。配置字段名、测试占位符或正则本身可以出现，真实值不可以出现。

## 3. 检查 Git 历史

仅从工作区删除秘密是不够的，旧提交仍可能包含它。至少检查：

```bash
git log --all -- .env 'data/*.db' 'data/resumes' 'data/knowledge'
git log --all -p -- .env
```

如果真实 Key 曾经提交过：

1. 立即在供应商控制台撤销并重新生成，不能只从 Git 删除。
2. 使用 `git filter-repo` 等工具清理历史。
3. 历史重写会影响协作者，执行前先备份并明确通知。

## 4. 推荐的 GitHub 仓库信息

- 仓库名：`recruitmatch-copilot`
- 简介：`Evidence-first multi-tenant recruiting copilot with hybrid rules, RAG and human feedback.`
- Topics：`fastapi`、`rag`、`llm`、`ai-application`、`recruiting`、`celery`、`postgresql`、`multi-tenant`
- 建议展示：登录页、运营概览、Top 3 推荐证据卡、知识库和 AI 降级状态。

截图必须使用虚构企业、虚构候选人和脱敏数据。README 中的指标必须保留“合成基准”限定。

## 5. 确认远程仓库

先查看当前配置，绝不要直接复制覆盖命令：

```bash
git remote -v
git branch --show-current
```

本地当前若仍指向其他旧项目，应为 RecruitMatch 新建空仓库，再切换远程。推荐保留旧地址以便回退：

```bash
git remote rename origin old-origin
git remote add origin https://github.com/<your-account>/recruitmatch-copilot.git
git remote -v
```

确认地址无误后才推送：

```bash
git push -u origin main
```

不要对不确定的远程执行 `--force`。如果 GitHub 新仓库自动创建了 README、License 或 `.gitignore`，远端会比本地多一个提交；最简单的方式是创建真正的空仓库，或先拉取并审查差异再合并。

## 6. License 与公开范围

公开仓库不等于他人自动获得复制、修改和分发许可。应根据目标主动选择：

- 希望招聘方查看，但暂不授予复用权：可以先不放 License。
- 希望开源且允许宽松复用：常见选择是 MIT 或 Apache-2.0。
- 仓库包含公司、学校或第三方代码/资料：先确认你有权公开。

本项目不会自动替你选择 License，因为这是权利授权决定。

## 7. 提交与发布

发布前审查本次文件范围：

```bash
git status --short
git diff --stat
git diff
```

确认无误后提交：

```bash
git add README.md .env.example docs/usage-guide.md docs/github-publish-checklist.md
git commit -m "docs: prepare RecruitMatch for GitHub"
```

推送后在 GitHub 网页再次检查：

- README 链接和 Mermaid 图正常渲染。
- Actions 全部通过。
- 仓库首页没有 Key、真实邮箱、简历或数据库。
- 默认分支是 `main`，Topics 和简介已填写。
- 从全新目录按 README 克隆并启动一次，验证不是只在原电脑可运行。

## 8. 求职展示边界

可以表述已实现多租户、证据约束 Hybrid RAG、模型失败降级、异步管线、评测和审计等仓库内可验证能力。不要宣称已经服务真实企业、达到生产 SLA、通过合规认证或在真实招聘数据上达到 README 的合成指标。
