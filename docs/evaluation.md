# RecruitMatch 评测说明

## 数据集

`recruitmatch-v1` 包含 150 条确定性合成画像，覆盖 10 个技术岗位族，每个岗位族 15 个强匹配、核心技能、部分技能和年限缺失变体。标签来源固定为 `synthetic_heuristic`。

该数据用于防止权重、规范化、排序和证据链回归，不用于证明真实招聘有效性，也没有冒充人工专家标注。

## 指标

- Top-1 Accuracy：第一推荐是否属于期望岗位族。
- Top-3 Recall：前三推荐是否包含期望岗位族。
- Evidence Coverage：推荐结果中匹配项具有结构化原文证据的比例。

当前机器生成结果位于 `evaluation/recruitmatch-v1-results.json`。复现命令：

```bash
python scripts/generate_recruitment_eval.py --seed 20260819
python scripts/evaluate_recruitment.py
```

## 上线前缺口

1. 由招聘专家对脱敏真实简历—岗位对进行双人标注和冲突仲裁。
2. 单独构建转岗、同义技能、简历缺失、跨级别和负样本集合。
3. 按岗位族报告指标，不能只看总体平均值。
4. 对敏感群体做公平性审查，但敏感属性不得进入推荐特征。
5. 人工复核错误样本后再决定是否引入 Embedding、Rerank 或 LLM 增强。
