# LLM Security Daily

每天 06:00（Asia/Shanghai）自动从顶会（AAAI / IEEE S&P / USENIX Security / CCS / NDSS / NeurIPS / ICML / ICLR / ACL / EMNLP）和 arXiv 选取 **20 篇高质量 LLM Security 论文**，生成双语摘要（EN 原文 + 中文翻译），推送到飞书并 push 到本仓库。

## 目录结构

```
.
├── digests/                              # 每日 20 篇双语摘要（按日期）
│   └── YYYY-MM-DD/
│       ├── README.md                     # 飞书推送内容
│       ├── papers/NN_<id>_<slug>.md      # 单篇独立笔记
│       └── bibtex.bib                    # 全部 BibTeX
├── cards/                                # 每日轻量索引卡片
├── notes/                                # 详细精读笔记（DeepPaperNote skill 输出）
│   ├── usenix-security/                  # 按会议 → 研究方向二级目录
│   ├── ieee-sp/
│   ├── ccs/
│   ├── ndss/
│   ├── aaai/
│   ├── neurips/
│   ├── icml/
│   ├── iclr/
│   ├── acl-emnlp/
│   └── arxiv-preprint/
├── cache/                                # 候选抓取中间产物（git ignored）
├── scripts/
│   └── llm_security/
│       └── run_daily.py                  # 候选抓取脚本（cron 调用入口）
├── docs/                                 # 设计文档
├── launchd/                              # (历史) launchd 配置
└── tests/
```

## LLM Security 范畴

### 包含
- **LLM 本身的安全**：jailbreak、prompt injection、adversarial attack on LLM、backdoor、data poisoning、privacy（membership inference、model extraction、training data extraction）、alignment、RLHF safety、red-teaming、hallucination safety、refusal training
- **LLM 作为攻击者/防御者**：LLM-as-attacker 用于漏洞挖掘、恶意代码生成；LLM-as-defender 用于漏洞检测、威胁情报、代码审计
- **LLM 智能体安全**：agent hijack、tool misuse、unsafe planning、prompt injection against agents

### 不包含
- 纯 NLP / CV 任务（与安全无关）
- 纯密码学 / 区块链 / IoT 安全（除非明确涉及 LLM）
- 综述类（除非高质量 mapping study）

## 自动化机制

- **定时**：Hermes cron 每日 06:00 触发（Asia/Shanghai）
- **数据源**：
  - arxiv `co:` 顶会注释（USENIX Security 2026 / S&P 2026 / CCS / NDSS / NeurIPS / ICML / ICLR / AAAI / ACL / EMNLP）
  - arxiv abs 关键词命中（jailbreak / prompt injection / privacy / backdoor / agent security / alignment）
  - Semantic Scholar 补全作者机构
- **筛选**：由 cron 内的 LLM agent（MiniMax-M3 max reasoning）判断质量、聚类、写双语摘要
- **推送**：飞书 DM + GitHub main 分支
- **静态站**：`docs/` 目录（GitHub Pages 启用后自动部署到 https://EnlZhao.github.io/LLMSecurityDigest）

## 手动触发详细笔记

每天摘要推送后，回复论文编号（如 `#3` 或 `#3, #7, #11`）触发单篇深度精读：

```
详细读 #3
```

会调用 `deeppapernote` skill，下载 PDF、提取图表/公式/实验、生成中文深度笔记（含原文配图解读、公式逐项分析、实验表格逐行分析、局限性 + 复现风险），写入 `notes/<venue>/<research-content>/`。

## 历史与设计

- 设计文档：`docs/superpowers/specs/2026-08-02-llm-security-daily-design.md`
- 实施计划：`docs/superpowers/plans/2026-08-02-llm-security-daily-impl.md`
- 当前重构 plan：`/home/ubuntu/.hermes/plans/llm-security-digest.md`