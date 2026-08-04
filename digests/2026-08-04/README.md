# LLM Security Daily — 2026-08-04

> 10 篇通过元数据、BibTeX 与正文身份校验的论文
> 事实字段由确定性脚本生成；翻译与解读由 LLM 生成并明确标注
> 生成时间：2026-08-04 11:30:06 UTC

## 分类索引

- **Other**：#1, #2, #3, #4, #5, #6, #7, #8, #9, #10

---

### [1]. Securing the AI Agent: A Unified Framework for Multi-Layer Agent Red Teaming

**作者**：Yong Yang, Xing Zheng, Huiyu Wu, Huangsheng Cheng, Xiaorong Shi, Jing Guo, Bo Yang, Yi Zhou, Xiangfan Wu, Zonghao Ying
**会议/来源**：arXiv preprint (2026-06-30)
**链接**：[论文主页](https://arxiv.org/abs/2606.31227) | [正文](https://arxiv.org/pdf/2606.31227)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> The fast growth of open-source AI infrastructure, from model serving engines and agent platforms to the Model Context Protocol (MCP) ecosystem and the language models themselves, has outpaced the security tooling available to defend it. We present AI-Infra-Guard, an open-source framework that organizes AI red teaming around a single observation: the attack surface of an AI agent is stratified across layers (infrastructure, protocol/tool, agent behavior, and model), and no single detection paradigm fits all of them. The framework therefore matches a paradigm to each layer, from deterministic rule matching over 75+ AI components and 1{,}400+ vulnerability rules, through LLM-driven agentic auditing of MCP servers and agent-skill packages and multi-turn black-box agent red teaming, to a jailbreak harness with 26+ attack operators over sixteen datasets. To our knowledge it is the only open-source framework to span all of these, including supply-chain auditing of the agent skills that increasingly extend AI agents. We release AI-Infra-Guard as open source so that \emph{layer-paradigm matching} can serve as a practical foundation for agent security and a shared base for the community to build on.

**摘要 (中文，LLM 生成)**：

The fast growth of open-source AI infrastructure, from model serving engines and agent platforms to the Model Context Protocol (MCP) ecosystem and the language models themselves, has outpaced the secu

**问题（LLM 解读）**：

研究场景与问题：The fast growth of open-source AI infrastructure, from model serving engines and agent platforms to the Model Context Pr……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：rity tooling available to defend it. We present AI-Infra-Guard, an open-source framework that organizes AI red teaming around a single observation: the attack surface of an AI agent is stratified acro

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：ss layers (infrastructure, protocol/tool, agent behavior, and model), and no single detection paradigm fits all of them. The framework therefore matches a paradigm to each layer, from deterministic ru

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{yang2026securingaiagentunified,
      title={Securing the AI Agent: A Unified Framework for Multi-Layer Agent Red Teaming}, 
      author={Yong Yang and Xing Zheng and Huiyu Wu and Huangsheng Cheng and Xiaorong Shi and Jing Guo and Bo Yang and Yi Zhou and Xiangfan Wu and Zonghao Ying},
      year={2026},
      eprint={2606.31227},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.31227}, 
}
```

---

### [2]. Security--Fidelity Tradeoffs: The Hidden Cost of Prompt Injection Defense

**作者**：Mitchell Hermon, Rahul Gupta, Weitong Ruan, Ekraam Sabir, Haohan Wang
**会议/来源**：arXiv preprint (2026-06-29)
**链接**：[论文主页](https://arxiv.org/abs/2606.30783) | [正文](https://arxiv.org/pdf/2606.30783)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> We identify a security-fidelity tradeoff in defending LLMs against indirect prompt injection: defenses resist injected instructions largely by suppressing untrusted text, which corrupts tasks that must preserve it, such as translation and document editing. Attack-success metrics cannot see this, because a model that ignores an injection and one that faithfully processes it as data score identically. We introduce SecFid, a benchmark built so that executing an injection, processing it as data, and ignoring it produce distinguishable outputs. This makes fidelity measurable and exposes a frontier: across 1,168 examples and 48 configurations, no model or defense achieves both objectives. The highest-fidelity model reaches 96.5% fidelity at 47.8% security, while the most secure defenses invert this, at 99.3% security but only 71.0%-73.9% fidelity. Even defenses with identical security differ in how they earn it: some repair hijacks into faithful processing, others simply suppress benign content. A decision-theoretic analysis shows why no fixed choice can be right everywhere: the correct behavior is not a property of the defense but of the deployment, set by its relative cost of a hijack versus a dropped span. Security alone therefore measures only half of robustness, and reporting it without fidelity hides the price at which it was bought.

**摘要 (中文，LLM 生成)**：

We identify a security-fidelity tradeoff in defending LLMs against indirect prompt injection: defenses resist injected instructions largely by suppressing untrusted text, which corrupts tasks that mus

**问题（LLM 解读）**：

研究场景与问题：We identify a security-fidelity tradeoff in defending LLMs against indirect prompt injection: defenses resist injected i……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：t preserve it, such as translation and document editing. Attack-success metrics cannot see this, because a model that ignores an injection and one that faithfully processes it as data score identicall

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：y. We introduce SecFid, a benchmark built so that executing an injection, processing it as data, and ignoring it produce distinguishable outputs. This makes fidelity measurable and exposes a frontier:

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{hermon2026securityfidelitytradeoffshiddencost,
      title={Security--Fidelity Tradeoffs: The Hidden Cost of Prompt Injection Defense}, 
      author={Mitchell Hermon and Rahul Gupta and Weitong Ruan and Ekraam Sabir and Haohan Wang},
      year={2026},
      eprint={2606.30783},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.30783}, 
}
```

---

### [3]. Understanding and Evaluating Claw-like Agent Security Through a Computer-Systems Lens

**作者**：Peizhi Niu, Wenjie Qu, Shangding Gu, Tianneng Shi, Yuankai Li, Ahmad Tawaha, Hend Alzahrani, Vincent Siu, Boyi Li, Chenguang Wang, Jiaheng Zhang, Basel Alomair, Ming Jin, Muhao Chen, Chi Wang, Costas Spanos, Dawn Song
**会议/来源**：arXiv preprint (2026-06-29)
**链接**：[论文主页](https://arxiv.org/abs/2606.30755) | [正文](https://arxiv.org/pdf/2606.30755)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> Claw-like AI agents (e.g., OpenClaw) are always-on processes with persistent access to credentials, files, tools, and external services. They take on system-level responsibilities -- installing packages, maintaining state, scheduling subtasks, and mediating I/O -- making security failures far more severe than in other agents. Yet existing benchmarks focus on model responses and tool calls, leaving cross-component failure modes largely unmeasured. We adopt a computer-system analogy: treating a Claw-like agent as an agentic computer system whose gateway runtime plays an OS-like mediation role, whose Skills resemble user-installed applications, and whose Plugins resemble loadable extensions with runtime privileges. Each component has a classical counterpart whose protection mechanisms -- refined over decades of cybersecurity research -- are absent on the agent side. From this perspective, we develop SafeClawArena, a benchmark of 406 adversarial tasks across four attack surfaces (Skill Supply-Chain Integrity, Persistent State Exploitation, Cross-Boundary Data Flow, and Indirect Prompt Injection), executed in containerized replicas of real agent platforms with canary-marked credentials and evaluated via automated taint tracking across nine output channels. We evaluate three platforms (OpenClaw, NemoClaw, SeClaw) and five frontier LLMs. The highest attack success rate reaches 70%; malicious Plugins succeed in 100% of cases regardless of the LLM. SeClaw cuts GPT-5.4's attack success rate from 70% to 22%, partly through utility-security tradeoffs rather than active defenses, while Claude-Opus-4.6 already sits near a 22% floor on every platform. These results expose the inadequacy of current defenses and suggest directions for future hardening. Code and data: https://github.com/sunblaze-ucb/SafeClawArena.

**摘要 (中文，LLM 生成)**：

Claw-like AI agents (e.g., OpenClaw) are always-on processes with persistent access to credentials, files, tools, and external services. They take on system-level responsibilities -- installing packag

**问题（LLM 解读）**：

研究场景与问题：Claw-like AI agents (e.g., OpenClaw) are always-on processes with persistent access to credentials, files, tools, and ex……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：es, maintaining state, scheduling subtasks, and mediating I/O -- making security failures far more severe than in other agents. Yet existing benchmarks focus on model responses and tool calls, leaving

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：cross-component failure modes largely unmeasured. We adopt a computer-system analogy: treating a Claw-like agent as an agentic computer system whose gateway runtime plays an OS-like mediation role, w

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{niu2026understandingevaluatingclawlikeagent,
      title={Understanding and Evaluating Claw-like Agent Security Through a Computer-Systems Lens}, 
      author={Peizhi Niu and Wenjie Qu and Shangding Gu and Tianneng Shi and Yuankai Li and Ahmad Tawaha and Hend Alzahrani and Vincent Siu and Boyi Li and Chenguang Wang and Jiaheng Zhang and Basel Alomair and Ming Jin and Muhao Chen and Chi Wang and Costas Spanos and Dawn Song},
      year={2026},
      eprint={2606.30755},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.30755}, 
}
```

---

### [4]. From Tool Connection to Execution Control: Benchmarking Security Invariants in MCP-Style Agent Runtimes

**作者**：Ting Liu
**会议/来源**：arXiv preprint (2026-06-27)
**链接**：[论文主页](https://arxiv.org/abs/2606.29073) | [正文](https://arxiv.org/pdf/2606.29073)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> Model Context Protocol (MCP)-style ecosystems give language-model applications a practical connection layer for tools, resources, prompts, and transports. As agents move from connection to execution, security decisions often remain split across clients, servers, prompts, approval dialogs, OAuth deployments, and logs. This paper asks whether a runtime can make execution-layer invariants explicit and testable while preserving MCP-like workflows. We define eight invariants: metadata non-authority, grant-backed approval, canonical resources, principal binding, scoped capability invocation, source-and-target data-flow authorization, deny-path audit, and explicit protocol state. We implement these invariants in HCP, a Handle-Capability Protocol reference runtime for MCP-style agent execution that represents calls through principals, resources, grants, capabilities, handles, policy decisions, data-pipe checks, and audit entries. We evaluate HCP against two MCP-like baselines: a naive connection-layer runtime and a practice-informed connection-layer mitigation baseline with metadata linting, session checks, and per-call approvals. Across 10 benchmark cases, the naive baseline permits all modeled attacks, the mitigation baseline permits 6 of 10, and HCP blocks all 10 while preserving audit evidence. Ablations identify which runtime components block attacks and preserve forensic evidence. A local in-memory microbenchmark reports sub-millisecond mean latencies for measured policy, invocation, peek, and pipe operations. A bounded GitHub README-screening sample provides ecosystem signals, not vulnerability findings. The results support a narrow claim: MCP-style agent systems need an execution-control layer in addition to connection-layer conventions.

**摘要 (中文，LLM 生成)**：

Model Context Protocol (MCP)-style ecosystems give language-model applications a practical connection layer for tools, resources, prompts, and transports. As agents move from connection to execution,

**问题（LLM 解读）**：

研究场景与问题：Model Context Protocol (MCP)-style ecosystems give language-model applications a practical connection layer for tools, r……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：security decisions often remain split across clients, servers, prompts, approval dialogs, OAuth deployments, and logs. This paper asks whether a runtime can make execution-layer invariants explicit an

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：d testable while preserving MCP-like workflows. We define eight invariants: metadata non-authority, grant-backed approval, canonical resources, principal binding, scoped capability invocation, source-

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{liu2026toolconnectionexecutioncontrol,
      title={From Tool Connection to Execution Control: Benchmarking Security Invariants in MCP-Style Agent Runtimes}, 
      author={Ting Liu},
      year={2026},
      eprint={2606.29073},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.29073}, 
}
```

---

### [5]. AdvancedShelLM: A Stateful Multi-Agent LLM Honeypot for SSH Deception

**作者**：Muris Sladić, Eman Alibalić, Veronica Valeros, Carlos Catania, Sebastian Garcia
**会议/来源**：arXiv preprint (2026-06-26)
**链接**：[论文主页](https://arxiv.org/abs/2606.27990) | [正文](https://arxiv.org/pdf/2606.27990)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> LLM-based SSH honeypots can generate believable interactions, but evaluations indicate they remain somewhat identifiable to determined attackers, indicating the need for a better scaffolding. We present a new LLM-based honeypot design that uses a multi-agent, multi-LLM architecture to address the limitations of the previous shelLM LLM honeypot. Our honeypot, called AdvancedShelLM, uses two LLM agents, a Manager and a Worker, that better understand the commands while reducing incorrect responses and increasing deception. It implements an advanced permanent filesystem, allowing many simultaneous attackers to see the same changing files for the first time. It was evaluated with: (i) unit tests for generative capabilities, (ii) an AI attacker (ARACNE) to assess realism and deception, (iii) human attackers to assess its deceptive capability, and (iv) an Internet deployment to evaluate deception in real-world attacks. In unit test results, AdvancedShelLM achieved a pass rate of up to 99.02%. The AI attacker ARACNE had issues making a decision if the system is honeypot or not, but showed slight bias towards saying honeypot, even for a real Ubuntu shell. With human attackers, AdvancedShelLM deceived more humans than Cowrie, but had similar results as shelLM. The Internet deployment showed concrete evidence that the output of AdvancedShelLM can influence the behaviour of real-life attackers.

**摘要 (中文，LLM 生成)**：

LLM-based SSH honeypots can generate believable interactions, but evaluations indicate they remain somewhat identifiable to determined attackers, indicating the need for a better scaffolding. We prese

**问题（LLM 解读）**：

研究场景与问题：LLM-based SSH honeypots can generate believable interactions, but evaluations indicate they remain somewhat identifiable……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：nt a new LLM-based honeypot design that uses a multi-agent, multi-LLM architecture to address the limitations of the previous shelLM LLM honeypot. Our honeypot, called AdvancedShelLM, uses two LLM age

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：nts, a Manager and a Worker, that better understand the commands while reducing incorrect responses and increasing deception. It implements an advanced permanent filesystem, allowing many simultaneous

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{sladić2026advancedshellmstatefulmultiagentllm,
      title={AdvancedShelLM: A Stateful Multi-Agent LLM Honeypot for SSH Deception}, 
      author={Muris Sladić and Eman Alibalić and Veronica Valeros and Carlos Catania and Sebastian Garcia},
      year={2026},
      eprint={2606.27990},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.27990}, 
}
```

---

### [6]. Securing LLM-Agent Long-Term Memory Against Poisoning: Non-Malleable, Origin-Bound Authority with Machine-Checked Guarantees

**作者**：Yedidel Louck
**会议/来源**：arXiv preprint (2026-06-23)
**链接**：[论文主页](https://arxiv.org/abs/2606.24322) | [正文](https://arxiv.org/pdf/2606.24322)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> LLM agents increasingly rely on persistent long-term memory, which creates a critical vulnerability that we study here: memory poisoning. An adversary can store untrusted content in one session that later steers a consequential action, such as a payment, a setting change, or data exfiltration, in a future session. Existing defenses base a memory item's authority to act on either its content (detection or trust-scoring) or its derivation history (lineage). We show that both signals are malleable. An attacker can launder an untrusted origin through three channels specific to LLM agents: the agent's own summarization, a trusted-tool echo, and manufactured corroboration. Each makes the content look benign and breaks or flips its derivation edge to ``trusted.'' We formalize malleability for the memory write-retrieve-act pipeline and prove a machine-checked separation theorem. No content- or lineage-based defense is sound under laundering (T1), write-time origin binding is necessary (T2), and non-malleable origin-bound authority with Sybil-resistant corroboration-gated elevation is sufficient (T3). Our construction, TMA-NM (Tamper-evident Memory Authority, Non-Malleable), instantiates non-malleable information-flow control (IFC) for LLM-agent memory. A cross-defense, cross-attack, and cross-model benchmark over eight frontier models shows that existing defenses fail exactly where the theory predicts (up to 68% laundering attack-success), while TMA-NM reaches 0% attack success on both direct and laundering attacks across all models and channels, at full legitimate utility. We release the benchmark, harness, and machine-checked TLA+ models to support reproducibility.

**摘要 (中文，LLM 生成)**：

LLM agents increasingly rely on persistent long-term memory, which creates a critical vulnerability that we study here: memory poisoning. An adversary can store untrusted content in one session that l

**问题（LLM 解读）**：

研究场景与问题：LLM agents increasingly rely on persistent long-term memory, which creates a critical vulnerability that we study here: ……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：ater steers a consequential action, such as a payment, a setting change, or data exfiltration, in a future session. Existing defenses base a memory item's authority to act on either its content (detec

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：tion or trust-scoring) or its derivation history (lineage). We show that both signals are malleable. An attacker can launder an untrusted origin through three channels specific to LLM agents: the agen

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{louck2026securingllmagentlongtermmemory,
      title={Securing LLM-Agent Long-Term Memory Against Poisoning: Non-Malleable, Origin-Bound Authority with Machine-Checked Guarantees}, 
      author={Yedidel Louck},
      year={2026},
      eprint={2606.24322},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.24322}, 
}
```

---

### [7]. ShellGames: Speculative LLM-Driven SSH Deception

**作者**：Umberto Salviati, Fabio De Gaspari, Mauro Conti, Luigi Vincenzo Mancini
**会议/来源**：arXiv preprint (2026-06-16)
**链接**：[论文主页](https://arxiv.org/abs/2606.17986) | [正文](https://arxiv.org/pdf/2606.17986)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> Cyber deception and Moving Target Defense are promising strategies that aim to disrupt adversaries by increasing uncertainty. However, sustaining long-lived, credible interactive sessions with adversaries remains an open challenge. Large Language Models (LLMs) offer a promising path toward more dynamic deception systems, but suffer from key limitations that fundamentally limit their applicability, including: lack of persistent state, output inconsistencies, hallucinations, latency, and susceptibility to behavioral subversion that may reveal the deception.   We propose ShellGames, an SSH shell simulator based on LLM designed to address these limitations. ShellGames combines five complementary techniques: (i) Automatic Chain-of-Thought and few-shot learning to improve correctness; (ii) memory management to maintain system state coherency; (iii) speculative command execution to reduce response latency; (iv) smart routing of complex interactive commands to a sandboxed environment; and (v) subversion detection leveraging the constrained input-output domain of shell environments. To enable systematic evaluation, we introduce a standardized benchmarking protocol and dataset spanning correctness, consistency, state tracking, and robustness tasks. ShellGames achieves $0.898$ command accuracy on correctness ($+5.3pp$ over baselines), $0.918$ sequence-level accuracy on consistency ($+36pp$), $0.98$ state tracking accuracy ($+18.3pp$), and $0.95$ accuracy on robustness ($+37pp$). A user study with $n=20$ participants confirms that ShellGames achieves realism comparable to a real shell under free exploration and outperforms traditional honeypots on perceived command coverage.

**摘要 (中文，LLM 生成)**：

Cyber deception and Moving Target Defense are promising strategies that aim to disrupt adversaries by increasing uncertainty. However, sustaining long-lived, credible interactive sessions with adversa

**问题（LLM 解读）**：

研究场景与问题：Cyber deception and Moving Target Defense are promising strategies that aim to disrupt adversaries by increasing uncerta……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：ries remains an open challenge. Large Language Models (LLMs) offer a promising path toward more dynamic deception systems, but suffer from key limitations that fundamentally limit their applicability,

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：including: lack of persistent state, output inconsistencies, hallucinations, latency, and susceptibility to behavioral subversion that may reveal the deception.   We propose ShellGames, an SSH shell

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{salviati2026shellgamesspeculativellmdrivenssh,
      title={ShellGames: Speculative LLM-Driven SSH Deception}, 
      author={Umberto Salviati and Fabio De Gaspari and Mauro Conti and Luigi Vincenzo Mancini},
      year={2026},
      eprint={2606.17986},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.17986}, 
}
```

---

### [8]. Let Them Steal: Trapping Large Language Model Extraction Attacks with Knowledge Honeypot

**作者**：Yuyang Dai, Yushun Dong
**会议/来源**：arXiv preprint (2026-06-14)
**链接**：[论文主页](https://arxiv.org/abs/2606.15810) | [正文](https://arxiv.org/pdf/2606.15810)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> Large language models deployed as commercial APIs are vulnerable to model extraction attacks, while existing defenses either act too late or degrade utility for legitimate users. We propose \textbf{Knowledge Trap}, a defense that redirects extraction attacks toward low-transferability knowledge through a \emph{Honeypot Knowledge Graph} (HKG) and breadcrumb-guided exploration. Instead of blocking queries or perturbing outputs, Knowledge Trap consumes the attacker's limited query budget on knowledge with negligible downstream utility while preserving benign-user performance. Experiments in medical and financial domains show that Knowledge Trap reduces surrogate Agreement by 6.2\% on average without degrading legitimate-user accuracy, outperforming existing defenses that impose measurable user impact. These results suggest that defending knowledge-space traversal is a practical direction for mitigating LLM extraction attacks.

**摘要 (中文，LLM 生成)**：

Large language models deployed as commercial APIs are vulnerable to model extraction attacks, while existing defenses either act too late or degrade utility for legitimate users. We propose \textbf{Kn

**问题（LLM 解读）**：

研究场景与问题：Large language models deployed as commercial APIs are vulnerable to model extraction attacks, while existing defenses ei……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：owledge Trap}, a defense that redirects extraction attacks toward low-transferability knowledge through a \emph{Honeypot Knowledge Graph} (HKG) and breadcrumb-guided exploration. Instead of blocking q

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：ueries or perturbing outputs, Knowledge Trap consumes the attacker's limited query budget on knowledge with negligible downstream utility while preserving benign-user performance. Experiments in medic

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{dai2026letstealtrappinglarge,
      title={Let Them Steal: Trapping Large Language Model Extraction Attacks with Knowledge Honeypot}, 
      author={Yuyang Dai and Yushun Dong},
      year={2026},
      eprint={2606.15810},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.15810}, 
}
```

---

### [9]. Grammar-Constrained Decoding Can Jailbreak LLMs into Generating Malicious Code

**作者**：Yitong Zhang, Shiteng Lu, Jia Li
**会议/来源**：arXiv preprint (2026-06-10)
**链接**：[论文主页](https://arxiv.org/abs/2606.11817) | [正文](https://arxiv.org/pdf/2606.11817)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> Large Language Models (LLMs) are increasingly used for code generation, raising concerns that they may be misused to produce malicious code. Meanwhile, Grammar-Constrained Decoding (GCD) has been widely adopted to improve the reliability of LLM-generated code by enforcing syntactic validity. In this paper, we reveal a counterintuitive risk: this reliability-oriented technique can itself become an attack surface. We uncover a new jailbreak attack, termed CodeSpear, that exploits GCD to induce LLMs into generating malicious code. Our experiments show that simply applying a benign code grammar constraint can effectively jailbreak LLMs.   To address this vulnerability, we propose CodeShield, a safety alignment approach that robustly preserves safe behavior even under attacker-controlled grammar constraints. CodeShield aligns the model in the code modality by teaching it to generate honeypot code under GCD. Such code is semantically harmless, so it does not implement the malicious request, and structurally diverse, so it is difficult to suppress through grammar tightening. At the same time, CodeShield still preserves natural-language refusals when natural language is available. Experiments on 10 popular LLMs across 4 benchmarks show that CodeSpear outperforms representative jailbreak baselines and increases the attack success rate by more than 30 percentage points on average. CodeShield also restores safety under CodeSpear while preserving benign utility. Our findings reveal a fundamental risk of GCD and call for greater attention to its potential security implications.

**摘要 (中文，LLM 生成)**：

Large Language Models (LLMs) are increasingly used for code generation, raising concerns that they may be misused to produce malicious code. Meanwhile, Grammar-Constrained Decoding (GCD) has been wide

**问题（LLM 解读）**：

研究场景与问题：Large Language Models (LLMs) are increasingly used for code generation, raising concerns that they may be misused to pro……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：ly adopted to improve the reliability of LLM-generated code by enforcing syntactic validity. In this paper, we reveal a counterintuitive risk: this reliability-oriented technique can itself become an

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：attack surface. We uncover a new jailbreak attack, termed CodeSpear, that exploits GCD to induce LLMs into generating malicious code. Our experiments show that simply applying a benign code grammar co

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{zhang2026grammarconstraineddecodingjailbreakllms,
      title={Grammar-Constrained Decoding Can Jailbreak LLMs into Generating Malicious Code}, 
      author={Yitong Zhang and Shiteng Lu and Jia Li},
      year={2026},
      eprint={2606.11817},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.11817}, 
}
```

---

### [10]. Game-Theoretic Multi-Agent Control for Robust Contextual Reasoning in LLMs

**作者**：Saeid Jamshidi, Amin Nikanjam, Arghavan Moradi Dakhel, Kawser Wazed Nafi, Foutse Khomh
**会议/来源**：arXiv preprint (2026-06-09)
**链接**：[论文主页](https://arxiv.org/abs/2606.10322) | [正文](https://arxiv.org/pdf/2606.10322)
**分类**：arXiv 预印本
**研究类别**：Other

**Abstract (EN — 权威来源原文)**：

> Large Language Models (LLMs) in multi-turn interactions maintain evolving context rather than generating isolated responses, making them vulnerable to prompt-injection and context-poisoning attacks in which locally plausible adversarial fragments gradually distort reasoning trajectories. Existing defenses mainly filter individual outputs and often ignore context evolution across turns, leaving long-horizon reasoning exposed. Although the Model Context Protocol (MCP) standardizes context exchange and tool invocation, it functions as a passive routing layer and does not enforce contextual stability. To address these limitations, we introduce the Game-Theoretic Secure Model Context Protocol (GT-MCP), a controller-driven multi-agent method that treats context management as a closed-loop dynamical process. GT-MCP coordinates three heterogeneous LLM agents and selects outputs through a trust function that jointly evaluates causal consistency against a validated context graph, semantic agreement among agents, and distributional drift over time. When instability is detected, a rollback-based self-healing mechanism restores the validated context and prevents unsupported fragments from propagating. Empirical evaluation over 500 interaction turns under an adaptive adversarial threat model shows that contextual drift remains bounded in 99.6% of turns, with recovery required in only 0.4%. Per-turn utility remains tightly concentrated, with median = -0.19, P05 = -0.72, and P95 = 0.30; severe degradation below -1 occurs in only 0.4% of cases, and no injection attempt succeeds at the controller level. Selected outputs maintain stable win rates above 98%, and computational overhead remains predictable, with latency per token = 1.63e-3 s.

**摘要 (中文，LLM 生成)**：

Large Language Models (LLMs) in multi-turn interactions maintain evolving context rather than generating isolated responses, making them vulnerable to prompt-injection and context-poisoning attacks in

**问题（LLM 解读）**：

研究场景与问题：Large Language Models (LLMs) in multi-turn interactions maintain evolving context rather than generating isolated respon……现有工作在该场景下存在 gap，本文针对该 gap 开展工作。

**方法（LLM 解读）**：

方法要点（基于 abstract 事实摘要）：which locally plausible adversarial fragments gradually distort reasoning trajectories. Existing defenses mainly filter individual outputs and often ignore context evolution across turns, leaving lon

**结果（LLM 解读）**：

实验与结果（基于 abstract 事实摘要）：g-horizon reasoning exposed. Although the Model Context Protocol (MCP) standardizes context exchange and tool invocation, it functions as a passive routing layer and does not enforce contextual stabil

**贡献（LLM 解读）**：

主要贡献：提出针对上述 gap 的 in-environment / 系统级方案；在 [arXiv 实验设置] 下评估；给出未来方向。

**BibTeX（权威端点原文）**：

```bibtex
@misc{jamshidi2026gametheoreticmultiagentcontrolrobust,
      title={Game-Theoretic Multi-Agent Control for Robust Contextual Reasoning in LLMs}, 
      author={Saeid Jamshidi and Amin Nikanjam and Arghavan Moradi Dakhel and Kawser Wazed Nafi and Foutse Khomh},
      year={2026},
      eprint={2606.10322},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.10322}, 
}
```

---
