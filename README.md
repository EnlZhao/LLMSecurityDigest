# LLM Security Digest

每天在 headless Linux 服务器上收集最多 10 篇 LLM Security 论文，并生成静态站。数量不足时宁缺毋滥：只有元数据、权威 BibTeX 和下载正文均通过脚本校验的论文才能发布。

## 事实边界

MiniMax/Hermes 只负责搜索策略、关键词、过滤、排序、分类和论文解读。以下事实字段完全由 Python 脚本从权威来源获取并冻结，LLM 无权填写或修改：

- title、authors、abstract 原文
- venue、收录状态、发表日期、DOI
- 论文主页、正文 URL、BibTeX

渲染器离线读取冻结的 `facts.json`。选择文件和分析文件一旦包含事实字段就会被拒绝。

## 数据源与 Key

| 来源 | 用途 | 是否需要 Key |
| --- | --- | --- |
| arXiv Atom API | 预印本候选与权威元数据 | 否 |
| OpenReview API | ICLR、NeurIPS、ICML 等录用论文候选 | 否 |
| Crossref API | DOI/会议论文元数据及 BibTeX | 否，建议配置联系邮箱 |
| Google Scholar via SerpAPI | 精确标题匹配、引用数和跳转链接 | `SERPAPI_API_KEY` |

收集顺序固定为“免费官方源候选 -> LLM 仅按 `paper_id` 排序 -> 脚本下载并校验 BibTeX/正文 -> 对入围短名单调用 SerpAPI”。Google Scholar 不作为 title、authors、abstract、venue 或 BibTeX 的权威来源。每篇论文都会生成 Scholar 搜索链接，即使没有配置 SerpAPI。

可选环境变量：

```bash
SERPAPI_API_KEY=...
LLMSD_CONTACT_EMAIL=research@example.com
LLMSD_DATA_DIR=/persistent/path/llmsd-data
```

`LLMSD_DATA_DIR` must be persistent on the headless server. `facts.json` stores
paper files as paths relative to this directory, so Hermes can resume bounded
reading after the candidate/materialize steps without depending on a temporary
runner path. Older snapshots with absolute paths remain readable only when that
path still exists.

本地放在 `.env`；GitHub Actions 使用同名 Secret。`.env` 和 `.data/` 均不会提交。

## 每日流程

```bash
python scripts/llm_security/run_daily.py init-plan --out RUN/search-plan.json
python scripts/llm_security/run_daily.py collect --plan RUN/search-plan.json --out RUN/candidates.json
# Hermes 只输出 paper_id、score、category、reason 到 selection.json
python scripts/llm_security/run_daily.py materialize \
  --candidates RUN/candidates.json --selection RUN/selection.json \
  --facts RUN/facts.json --manifest RUN/manifest.json
# Hermes 通过 outline/read-section/find 分段阅读后写 analysis.json
# 若 facts.json 与正文数据不在默认目录，三个命令都支持 --data-dir
python scripts/llm_security/render_and_push.py \
  --facts RUN/facts.json --manifest RUN/manifest.json \
  --analysis RUN/analysis.json --date YYYY-MM-DD --build-site
```

Hermes 的完整约束见 `scripts/llm_security/hermes_prompt.md`。运行前可用 `python scripts/llm_security/run_daily.py doctor` 检查网络源、SerpAPI 配置和数据目录。

## GitHub 网络路径

仓库提供两个可选 Actions。`Collect paper candidates` 按日只访问 arXiv 和 OpenReview，并上传候选 artifact；Hermes 可以用 `gh run download <run-id> -n paper-candidates-<run-id> -D RUN` 下载它，仍须按 `paper_id` 排序，再在服务器上运行 `materialize`。`Probe Google Scholar via SerpAPI` 是手动的单标题网络探测，用于确认 GitHub 出口可访问 SerpAPI；它不写入论文事实，每次运行最多消耗一次查询额度。发布流程不会因为 Scholar 不可达而替换或编造论文。

若某个出口（当前本地 OpenReview API 返回 403）不可达，错误会保存在 `source_reports`，可用的 arXiv 候选仍会继续处理；这不是事实兜底，只有重新从权威源获取并通过身份校验的记录才能进入 `facts.json`。

## 站点

- [首页](https://EnlZhao.github.io/LLMSecurityDigest)
- [Archive](https://EnlZhao.github.io/LLMSecurityDigest/archive.html)
- [GitHub](https://github.com/EnlZhao/LLMSecurityDigest)

## License

MIT（待定）
