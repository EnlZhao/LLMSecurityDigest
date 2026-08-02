# LLM Security Digest

每天 06:00（Asia/Shanghai）从 AAAI / IEEE S&P / USENIX Security / CCS / NDSS / NeurIPS / ICML / ICLR / ACL / EMNLP 等顶会和 arXiv 收集 20 篇 LLM Security 论文（10 主方向 + 10 大方向），输出双语摘要（EN 原文 + 中文翻译），构建静态站部署到 GitHub Pages。

## 站点导览

- **[首页](https://EnlZhao.github.io/LLMSecurityDigest)** — 最新摘要与深度笔记
- **[Archive](https://EnlZhao.github.io/LLMSecurityDigest/archive.html)** — 所有日期的摘要
- **RSS** — `https://EnlZhao.github.io/LLMSecurityDigest/rss.xml`
- **GitHub** — https://github.com/EnlZhao/LLMSecurityDigest

## LLM Security 范畴

### 包含

- **LLM 本身的安全**：jailbreak、prompt injection、adversarial attack on LLM、backdoor、data poisoning、privacy（membership inference、model extraction、training data extraction）、alignment、RLHF safety、red-teaming、hallucination safety、refusal training
- **LLM 作为攻击者/防御者**：LLM-as-attacker 用于漏洞挖掘、恶意代码生成；LLM-as-defender 用于漏洞检测、威胁情报、代码审计
- **LLM 智能体安全**：agent hijack、tool misuse、unsafe planning、prompt injection against agents

### 不包含

- 纯 NLP / CV 任务（与安全无关）
- 纯密码学 / 区块链 / IoT 安全（除非明确涉及 LLM）
- 综述类

## 仓库结构

```
.
├── digests/                       每日 20 篇双语摘要
│   └── YYYY-MM-DD/
│       ├── README.md              飞书推送内容
│       ├── papers/                单篇独立卡片
│       └── bibtex.bib             全部 BibTeX
├── notes/                         深度精读笔记（按会议 → 研究方向）
│   ├── usenix-security/
│   ├── ieee-sp/
│   ├── ccs/
│   ├── ndss/
│   ├── aaai/
│   ├── neurips/
│   ├── icml/
│   ├── iclr/
│   ├── acl-emnlp/
│   └── arxiv-preprint/
├── cards/                         每日轻量索引
├── docs/                          GitHub Pages 静态站源
│   ├── index.html
│   ├── archive.html
│   ├── rss.xml
│   ├── digest/
│   ├── notes/
│   └── assets/style.css
├── scripts/
│   ├── llm_security/
│   │   ├── run_daily.py           候选抓取（arxiv API + SS 补全）
│   │   └── render_and_push.py     渲染 + git push
│   └── build_github_pages.py      生成 docs/ 静态站
└── src/llm_security_digest/       (历史) launchd 配置 + Python 包
```

## 数据源

- arxiv `co:` 顶会标注（USENIX Security / S&P / CCS / NDSS / NeurIPS / ICML / ICLR / AAAI / ACL / EMNLP）
- arxiv abs 关键词命中（jailbreak / prompt injection / privacy / backdoor / agent security / alignment）
- Semantic Scholar 补全作者机构

## 主方向

当前主方向：**LLM 作为攻击者时的静态防御方法**（10 篇/天）。主方向配置位于 `~/.hermes/profile_user.json`（私有，不在此仓库）。

## License

MIT（待定）