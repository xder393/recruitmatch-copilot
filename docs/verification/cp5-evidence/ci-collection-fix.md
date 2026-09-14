# CI collection 回归修复（本地验证）

日期：2026-09-14。此记录补充 CP5 初始验收，不倒写早期失败或替代当前提交的远程 CI 结果。

## 失败与根因

CP5 分支已成功推送至 [PR #2](https://github.com/xder393/recruitmatch-copilot/pull/2)，当时远程 HEAD 为 `9b007b33d8f1e6e3d932bb0ecf312c2ec203ecee`。失败的是 Actions 的单元测试，而非 Git 上传：

- [PR 运行](https://github.com/xder393/recruitmatch-copilot/actions/runs/34838290733)：`validate` 和 `postgres-integration` 通过，`unit` 失败。
- [push 运行](https://github.com/xder393/recruitmatch-copilot/actions/runs/34838260723)：相同失败。

`pytest.ini` 设置了 `addopts = -q`，验收测试的子进程又传入 `-q`。CI 的收集输出因而是“文件名: 用例数量”，不是原断言期待的“文件名::用例名”。测试镜像未复制 `pytest.ini`，实际读取了 `pyproject.toml`，使本地验证漏掉了这一差异。

只读对照复现：原镜像的单项测试通过；仅挂载仓库 `pytest.ini` 后，该测试出现与远程相同的断言失败。没有改变业务代码或调用真实模型。

## 修复与回归覆盖

1. 仅在 Docker 的 test 阶段复制同一份 `pytest.ini`，生产 runtime 阶段不变。
2. 使用 pytest 公共 `pytest_collection_finish` Hook 获取实际 `item.nodeid` 列表，写入本次独立的临时 JSON；不解析终端文本。
3. 保留普通运行不收集 live E2E、显式 `TELEMETRY_E2E=1` 收集 live E2E 的检查，并要求一项已知普通测试存在，防止空集合误过。
4. 覆盖空选项、`-q`、`-qq`、`-v`；两种 E2E 开关均实际执行子进程收集。保留 20 秒超时。
5. 通过运行中的 `pytestconfig.inipath` 验证配置来源，防止镜像再次静默退回其他配置文件。

## RED / GREEN

在实现前加入回归：**4 failed, 2 passed in 12.94s**。失败分别来自实际加载错误配置，以及三种输出模式下的旧文本断言。

修复后，挂载测试与配置的定向验证退出 0。随后重新构建测试镜像，不使用源码替换挂载执行完整单元测试：

- **439 tests，0 failures，0 errors，0 skipped，57.522s**。
- 使用 JUnit XML 的结构化属性核对结果，未依赖被 quiet 模式抑制的文字摘要。
- Ruff、238 个文件格式检查、当前 CI 范围 mypy 41 个文件通过。
- Dockerfile、pytest.ini 和两个变更测试文件与最终镜像 SHA256 一致。

最终测试镜像：`sha256:e6761384be8b0a23080d30000e7b932acbaa85c9cc5515937601b54653ebd88c`。

## 复现命令

宿主机只使用 Docker；演示配置是仓库公开的 `.env.example`。所有命令使用同一个专用项目名称：

```sh
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 build test-unit
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps test-unit
docker compose --env-file .env.example -p recruitmatch-cp5-sep14 run --rm --no-deps test-unit pytest tests/foundation/test_runtime_contract.py tests/observability/test_telemetry_acceptance.py -q
```

构建代理仅按命令传入；不修改用户全局代理或个人配置。测试不读取个人模型 Key，不下载模型。

## 范围与远程确认

本轮仅修改测试镜像打包和测试验证，不改变工作流任务/忽略列表、应用逻辑、遥测策略、告警阈值或 R19 图例待办。没有为这次局部修复重跑完整 Collector 故障演练；原 CP5 的 153 项运行时观测结果仍是对应旧验收镜像的历史证据。

提交前已完成独立只读审查：本轮修复未发现 Critical、Important 或 Minor，结论为可提交/推送；此前 CP5 的 R19 不在本轮范围内。审查核对了实际收集语义、配置加载边界、非空对照断言、超时及文档中的证据归属，未替代完整测试执行。

修复推送后应检查 PR 当前 HEAD 的新 `validate`、`unit`、`postgres-integration`，不能把本文的本地通过或旧提交结果当作远程绿灯。最终远程结果会记录在 PR 中。
