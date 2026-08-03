# Paper Daily

每天在 headless 服务器上收集最多 10 篇 LLM Security 论文，并生成静态站。数量不足时宁缺毋滥：只有元数据、权威 BibTeX 和下载正文均通过脚本校验的论文才能发布。

## 事实边界

MiniMax/Hermes 只负责搜索策略、关键词、过滤、排序、分类和论文解读。以下事实字段完全由 Python 脚本从权威来源获取并冻结，LLM 无权填写或修改：

- title、authors、abstract 原文
- venue、收录状态、发表日期、DOI
- 论文主页、正文 URL、BibTeX

渲染器离线读取冻结的 `facts.json`。选择文件和分析文件一旦包含事实字段就会被拒绝。

## 数据源与 Key

| 来源 | 用途 | 是否需要 Key |
| --- | --- | --- |
| 官方 proceedings adapters | USENIX、NDSS、ACL/EMNLP、PMLR/ICML、NeurIPS、CVF/CVPR、ECVA/ECCV、AAAI、IJCAI | 否 |
| OpenReview API v2/v1-compatible | ICLR、NeurIPS、ICML 等投稿、venue 与 decision replies | 否 |
| arXiv Atom API | 广泛发现、预印本元数据与正式记录 reconciliation | 否 |
| Crossref REST + DOI content negotiation | IEEE/ACM 注册 venue 的 DOI 元数据与官方 BibTeX | 否，建议配置联系邮箱 |
| IEEE Xplore API | IEEE 注册 venue 的补充发现与 DOI 元数据 | 可选 `IEEE_XPLORE_API_KEY` |
| ACM | 通过 Crossref 查询 CCS/TOPS，不使用未注册私有 API | 否（Crossref 路径） |
| Google Scholar via SerpAPI | 精确标题匹配、引用数和跳转链接 | `SERPAPI_API_KEY` |

收集顺序固定为“注册的顶会顶刊 proceedings/期刊正式源 -> OpenReview accepted
records -> arXiv 广泛发现并与正式记录严格匹配 -> 正式论文不足时才从未匹配
arXiv 补位 -> LLM 仅按 `paper_id` 排序 -> 脚本下载并校验 BibTeX/正文 -> 对
入围短名单调用 SerpAPI”。正式记录优先，未解析证据永不发布。Google Scholar
不作为 title、authors、abstract、venue 或 BibTeX 的权威来源；每篇论文都会
生成 Scholar 搜索链接，即使没有配置 SerpAPI。无 key 源失败时，source report
会保留请求阶段和错误，不以兜底数据掩盖失败。

可选环境变量：

```bash
SERPAPI_API_KEY=...
IEEE_XPLORE_API_KEY=...
LLMSD_CONTACT_EMAIL=research@example.com
LLMSD_DATA_DIR=/persistent/path/llmsd-data
```

`LLMSD_DATA_DIR` must be persistent on the headless server. `facts.json` stores
paper files as paths relative to this directory, so Hermes can resume bounded
reading after the candidate/materialize steps without depending on a temporary
runner path. Older snapshots with absolute paths remain readable only when that
path still exists.

本地放在 `.env`；GitHub Actions 使用同名 Secret。`.env` 和 `.data/` 均不会提交。

无 key 的正式来源会先运行：注册 proceedings 页面、CVF Open Access、
公开 OpenReview notes、arXiv Atom 和 Crossref。OpenReview v2 使用
`https://api2.openreview.net` 的分页 notes/decision 查询，旧 venue 使用
`https://api.openreview.net` 的 v1-compatible client；只认 assigned venue
和 decision reply，不能凭页面上出现的 “accepted” 字样升级状态。arXiv
使用 `https://export.arxiv.org/api/query`，脚本按官方建议至少间隔 3 秒，
BibTeX 由 `https://arxiv.org/bibtex/<id>` 获取；`journal_ref` 只能进入
待验证证据。Crossref 使用 `https://api.crossref.org/works` 的注册
ISSN/container 查询，DOI BibTeX 通过
`Accept: application/x-bibtex` 内容协商获取。IEEE Xplore 的
`https://ieeexploreapi.ieee.org/api/v1/search/articles` 仅在配置
`IEEE_XPLORE_API_KEY` 时启用，缺 key 或 API 失败会写入 source report，
不会由 LLM 或 Scholar 代填。ACM CCS/TOPS 走 Crossref，不依赖 ACM 私有 key。

安装包固定 `openreview-py>=1.46,<2`；headless 服务器执行
`python -m pip install .` 即可获得 v2 client 与 v1-compatible client。
`doctor` 将缺少的 `SERPAPI_API_KEY` 显示为 `optional_missing`；只要必需的
无 key 来源和运行数据目录正常，它不会因此失败。

## 每日流程

Hermes 的 `selection.json` 每项必须包含非事实字段
`paper_id`、`score`、`category`、`reason` 和 `track`；
`track` 只能是 `core` 或 `broad`。每天每轨最多发布 5 篇，目标最多
10 篇。`search-plan.json` 的 `core_keywords` 定义主方向；标为 `core`
的候选必须由脚本在标题或权威摘要中命中其中至少一个关键词。验证失败、
关键词不匹配或配额超出时保留可见拒绝记录，允许少于 10 篇，不自动凑数。

```bash
python scripts/llm_security/run_daily.py init-plan --out RUN/search-plan.json
python scripts/llm_security/run_daily.py collect --plan RUN/search-plan.json --out RUN/candidates.json
# Hermes 只输出 paper_id、score、category、reason、track 到 selection.json；
# track 必须是 core 或 broad，每轨最多发布 5 篇，失败或超额不凑数
python scripts/llm_security/run_daily.py materialize \
  --candidates RUN/candidates.json --selection RUN/selection.json \
  --facts RUN/facts.json --manifest RUN/manifest.json
# Hermes 通过 outline/read-section/find 分段阅读后写 analysis.json
# 若 facts.json 与正文数据不在默认目录，三个命令都支持 --data-dir
python scripts/llm_security/render_and_push.py \
  --facts RUN/facts.json --manifest RUN/manifest.json \
  --analysis RUN/analysis.json --date YYYY-MM-DD --build-site
```

### Hermes evolution

Hermes 只能提出查询、关键词、venue 分组等策略 overlay，不能写入任何论文事实、单篇 title/DOI/date、URL、HTTP 或 secret。每个候选都必须包含根因、泛化模式、机器可验证的 expected metric、counterexamples，以及至少一个 trigger、两个独立 positive 和一个 negative regression fixture；缺 reflection 或单篇详情页 source request 会被拒绝。候选必须经过 validator 和 shadow 后才能原子激活，下一次运行才会读取；每次激活和显式回滚都会写入 history：

```bash
python scripts/llm_security/run_daily.py reflect --input candidate.json
python scripts/llm_security/run_daily.py validate-evolution --version CANDIDATE_VERSION
python scripts/llm_security/run_daily.py shadow-evolution --version CANDIDATE_VERSION
# Use the report persisted under evolution/shadow/.../report.json by the command above.
python scripts/llm_security/run_daily.py activate-evolution \
  --version CANDIDATE_VERSION --shadow-report /persistent/llmsd-data/evolution/shadow/YYYY-MM-DD/PROPOSAL_ID/report.json
python scripts/llm_security/run_daily.py evolution-status
python scripts/llm_security/run_daily.py rollback-evolution
```

演化数据位于 `LLMSD_DATA_DIR/evolution/`（默认 `.data/evolution/`），包含 `candidates/`、`shadow/`、`active/`、`rejected/`、`history/` 和 `active.json`，不提交私有数据或密钥。

仓库中的 baseline adapters、事实 schema、canonical match 门槛、provenance
和 `facts.json` materializer 是只读边界。active version 不可覆盖；失败的
overlay 会记录回滚事件并恢复上一稳定版本，但 baseline 必须独立完成事实采集，
回滚不能成为事实兜底。

Hermes 的完整约束见 `scripts/llm_security/hermes_prompt.md`。运行前可用 `python scripts/llm_security/run_daily.py doctor` 检查网络源、SerpAPI 配置和数据目录。

## GitHub 网络路径

仓库提供两个可选 Actions。`Collect paper candidates` 在 headless Linux runner
上先运行所有注册的 proceedings/期刊 adapters，再运行 OpenReview 和 arXiv，
并上传候选 artifact；配置 `IEEE_XPLORE_API_KEY` 时额外运行 IEEE Xplore，
未配置时 Crossref 仍可独立提供 IEEE/ACM DOI 元数据。Hermes 可以用
`gh run download <run-id> -n paper-candidates-<run-id> -D RUN` 下载 artifact，
仍须按 `paper_id` 排序，再在服务器上运行 `materialize`。`Probe Google Scholar
via SerpAPI` 的 `workflow_run` 路径只读取这个 artifact，最多查询五个
unresolved/shortlisted 候选并上传独立 `scholar-enrichment.json`；它不会修改
候选或 `facts.json`。手动 dispatch 仍是单标题 smoke test，每次最多消耗一次
额度。发布流程不会因为 Scholar 不可达而替换或编造论文。

若需要从 JavaScript proceedings 页面发现候选链接，可在 headless Linux 上运行
`scripts/llm_security/headless_discover.py`。该浏览器层只返回 allowlisted
URL 的标题、短文本和相对链接 evidence，最多十个 URL，明确写入
`facts_written: false`；它不能访问 secret、构造 `PaperFacts` 或写入
`facts.json`。所有候选必须重新进入确定性的官方 adapter/materializer。
直接 HTTP 被注册来源返回 403/429/5xx 或网络超时时，可显式设置
`LLMSD_HEADLESS_FALLBACK=1` 启用可选的 Playwright raw-response fallback：
浏览器只访问显式 allowlisted HTTPS host，校验每次 redirect，限制总超时和
响应字节，并返回原始 HTML/JSON/PDF bytes、最终 URL、状态和 SHA-256
provenance。默认仍关闭，OpenReview 仍只走官方 client；浏览器 bytes 会重新
交给现有 deterministic adapter/parser/validator，永远不会直接生成
`PaperFacts` 或 `facts.json`。独立导出 raw artifact 可使用：
`python scripts/llm_security/headless_discover.py --raw --input RUN/browser-request.json --out RUN/browser-raw.json`。
`paper-search-mcp` may be used only as a discovery/reference pattern; it is
never a fact authority for title, authors, abstract, venue, URLs, or BibTeX.

若某个出口（当前本地 OpenReview API 返回 403）不可达，错误会保存在 `source_reports`，可用的 arXiv 候选仍会继续处理；这不是事实兜底，只有重新从权威源获取并通过身份校验的记录才能进入 `facts.json`。

## Changelog

### 2026-08-04

- Shared daily queries now retain arXiv field syntax for the Atom API while
  removing those field prefixes for Crossref and IEEE Xplore. This keeps the
  same keywords portable across the registered formal discovery APIs.
- OpenReview no longer treats a generic `Conference` venue label as an
  acceptance decision. It requires an explicit decision reply, or the narrow
  legacy final-venue form with a verified final track.
- OpenReview withdrawn and desk-rejected child venues now remain visible as
  terminal incomplete records instead of being silently filtered. Legacy v1
  notes can use only the registered queried venue, or a unique registered
  final venue label, when `venueid` is absent. The requested conference year
  is exact rather than family-wide, and v2 unified author profiles are
  normalized from explicit names rather than serialized as JSON-like author
  text.
- Formal-source records no longer fabricate `YYYY-01-01` from a proceedings
  year. An unknown authoritative date remains unknown, while the baseline
  source ID retains only the year needed to rebuild an official route.
- PMLR/ICML full text accepts only the official `mlresearch` raw GitHub path
  whose version and repeated paper key match the refreshed PMLR identifier.
  NeurIPS supports its official no-DOI BibTeX export with an equally strict
  host and path check.
- Discovery plans and evolution overlays now accept OpenReview venue IDs only
  when they exactly match the registered catalog (with Unicode/casefold
  normalization). Official proceedings adapters also reject absolute links
  that leave their registered HTTPS host.
- Crossref and IEEE Xplore treat a missing top-level result array as a schema
  error rather than a successful empty response. HTTP failures, incomplete
  abstract/PDF records, and missing materialization BibTeX remain visible and
  cannot become facts.
- Hermes full-text reading is bounded to a 6,000-character section, three
  search matches, 300 characters of context, and a 500-character query. The
  daily CLI has an isolated-data-directory end-to-end regression covering the
  ten-paper and five-per-track limits without allowing LLM fields into facts.
- The `core` track is tied to plan-owned `core_keywords`: an unrelated
  candidate cannot be promoted to the five-paper core quota by an LLM label.

## 站点

- [首页](https://EnlZhao.github.io/LLMSecurityDigest)
- [Archive](https://EnlZhao.github.io/LLMSecurityDigest/archive.html)
- [GitHub](https://github.com/EnlZhao/LLMSecurityDigest)

## License

MIT（待定）
