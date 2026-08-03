# LLM Security Daily — 2026-08-02

> 10 篇通过元数据、BibTeX 与正文身份校验的论文
> 事实字段由确定性脚本生成；翻译与解读由 LLM 生成并明确标注
> 生成时间：2026-08-02 16:02:07 UTC

## 分类索引

- **Agent Security**：#1
- **Prompt Injection**：#2, #4, #5
- **Jailbreak**：#3, #6
- **Privacy**：#7, #8, #9
- **Backdoor**：#10

---

### [1]. FragFuse: Bypassing Access Control of Large Language Model Agents via Memory-Based Query Fragmentation and Fusion

**作者**：Zixin Rao, Wentian Zhu, Chan Aristella Lu, Zhaorun Chen, Wei Niu, Le Guan, Bo Li, Zhen Xiang
**会议/来源**：arXiv preprint `cs.CR` (2026-06-14)
**来源备注（权威来源原文）**：33 pages, 4 figures. Accepted by USENIX Security 2026
**链接**：[论文主页](https://arxiv.org/abs/2606.15609) | [正文](https://arxiv.org/pdf/2606.15609) | [Google Scholar](https://scholar.google.com/scholar?q=%22FragFuse%3A+Bypassing+Access+Control+of+Large+Language+Model+Agents+via+Memory-Based+Query+Fragmentation+and+Fusion%22)
**分类**：arXiv 预印本
**研究类别**：Agent Security

**Abstract (EN — 权威来源原文)**：

> Large language model (LLM) agents increasingly rely on long-term memory to support complex task execution, user personalization, and domain adaptation. Meanwhile, emerging access-control mechanisms for LLM agents are being explored to block policy-violating requests and prevent misuse. We reveal a novel attack surface arising from agent memory operations: prohibited content that would trigger access control can be fragmented across interactions, stored in long-term memory in benign-appearing form, and later reconstructed through memory retrieval without appearing explicitly in the final user query. We propose FragFuse, the first attack that enables unprivileged users to bypass agent access control by exploiting this temporal channel introduced by long-term memory. FragFuse operates in three stages: (1) identifying rejection-responsive fragments via black-box adaptive querying with fragment masking; (2) injecting these fragments into memory using marker carrier queries; and (3) retrieving and fusing the stored fragments through a follow-up attack query. Although FragFuse can be instantiated manually for individual agents, we further develop a surrogate-based optimization scheme that tunes fusion instructions and marker designs, enabling automated attack generation without violating the attacker's threat-model assumptions. We evaluate FragFuse across four representative agent settings and task domains, covering three state-of-the-art agent access-control mechanisms. FragFuse achieves an average bypass success rate of 86.3% and an average end-to-end harmful task success rate of 41.1% across all settings, with only 4.4% average task-success degradation compared with configurations without access control. We also show that alternative defenses, including state-of-the-art prompt-injection detectors and perplexity detectors, do not effectively address this attack.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{rao2026fragfusebypassingaccesscontrol,
      title={FragFuse: Bypassing Access Control of Large Language Model Agents via Memory-Based Query Fragmentation and Fusion}, 
      author={Zixin Rao and Wentian Zhu and Chan Aristella Lu and Zhaorun Chen and Wei Niu and Le Guan and Bo Li and Zhen Xiang},
      year={2026},
      eprint={2606.15609},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.15609}, 
}
```

---

### [2]. AttriGuard: Defeating Indirect Prompt Injection in LLM Agents via Causal Attribution of Tool Invocations

**作者**：Yu He, Haozhe Zhu, Yiming Li, Shuo Shao, Hongwei Yao, Zhihao Liu, Zhan Qin
**会议/来源**：arXiv preprint `cs.CR` (2026-03-11)
**来源备注（权威来源原文）**：Accepted by USENIX Security 2026
**链接**：[论文主页](https://arxiv.org/abs/2603.10749) | [正文](https://arxiv.org/pdf/2603.10749) | [Google Scholar](https://scholar.google.com/scholar?q=%22AttriGuard%3A+Defeating+Indirect+Prompt+Injection+in+LLM+Agents+via+Causal+Attribution+of+Tool+Invocations%22)
**分类**：arXiv 预印本
**研究类别**：Prompt Injection

**Abstract (EN — 权威来源原文)**：

> LLM agents are highly vulnerable to Indirect Prompt Injection (IPI), where adversaries embed malicious directives in untrusted tool outputs to hijack execution. Most existing defenses treat IPI as an input-level semantic discrimination problem, which often fails to generalize to unseen payloads. We propose a new paradigm, action-level causal attribution, which secures agents by asking why a particular tool call is produced. The central goal is to distinguish tool calls supported by the user's intent from those causally driven by untrusted observations. We instantiate this paradigm with AttriGuard, a runtime defense based on parallel counterfactual tests. For each proposed tool call, AttriGuard verifies its necessity by re-executing the agent under a control-attenuated view of external observations. Technically, AttriGuard combines teacher-forced shadow replay to prevent attribution confounding, hierarchical control attenuation to suppress diverse control channels while preserving task-relevant information, and a fuzzy survival criterion that is robust to LLM stochasticity. Across four LLMs and two agent benchmarks, AttriGuard achieves 0% ASR under static attacks with negligible utility loss and moderate overhead. Importantly, it remains resilient under adaptive optimization-based attacks in settings where leading defenses degrade significantly.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{he2026attriguarddefeatingindirectprompt,
      title={AttriGuard: Defeating Indirect Prompt Injection in LLM Agents via Causal Attribution of Tool Invocations}, 
      author={Yu He and Haozhe Zhu and Yiming Li and Shuo Shao and Hongwei Yao and Zhihao Liu and Zhan Qin},
      year={2026},
      eprint={2603.10749},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2603.10749}, 
}
```

---

### [3]. Great, Now Write an Article About That: The Crescendo Multi-Turn LLM Jailbreak Attack

**作者**：Mark Russinovich, Ahmed Salem, Ronen Eldan
**会议/来源**：arXiv preprint `cs.CR` (2024-04-02)
**来源备注（权威来源原文）**：Accepted at USENIX Security 2025
**链接**：[论文主页](https://arxiv.org/abs/2404.01833) | [正文](https://arxiv.org/pdf/2404.01833) | [Google Scholar](https://scholar.google.com/scholar?q=%22Great%2C+Now+Write+an+Article+About+That%3A+The+Crescendo+Multi-Turn+LLM+Jailbreak+Attack%22)
**分类**：arXiv 预印本
**研究类别**：Jailbreak

**Abstract (EN — 权威来源原文)**：

> Large Language Models (LLMs) have risen significantly in popularity and are increasingly being adopted across multiple applications. These LLMs are heavily aligned to resist engaging in illegal or unethical topics as a means to avoid contributing to responsible AI harms. However, a recent line of attacks, known as jailbreaks, seek to overcome this alignment. Intuitively, jailbreak attacks aim to narrow the gap between what the model can do and what it is willing to do. In this paper, we introduce a novel jailbreak attack called Crescendo. Unlike existing jailbreak methods, Crescendo is a simple multi-turn jailbreak that interacts with the model in a seemingly benign manner. It begins with a general prompt or question about the task at hand and then gradually escalates the dialogue by referencing the model's replies progressively leading to a successful jailbreak. We evaluate Crescendo on various public systems, including ChatGPT, Gemini Pro, Gemini-Ultra, LlaMA-2 70b and LlaMA-3 70b Chat, and Anthropic Chat. Our results demonstrate the strong efficacy of Crescendo, with it achieving high attack success rates across all evaluated models and tasks. Furthermore, we present Crescendomation, a tool that automates the Crescendo attack and demonstrate its efficacy against state-of-the-art models through our evaluations. Crescendomation surpasses other state-of-the-art jailbreaking techniques on the AdvBench subset dataset, achieving 29-61% higher performance on GPT-4 and 49-71% on Gemini-Pro. Finally, we also demonstrate Crescendo's ability to jailbreak multimodal models.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{russinovich2025greatwritearticlethat,
      title={Great, Now Write an Article About That: The Crescendo Multi-Turn LLM Jailbreak Attack}, 
      author={Mark Russinovich and Ahmed Salem and Ronen Eldan},
      year={2025},
      eprint={2404.01833},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2404.01833}, 
}
```

---

### [4]. Hijacking Large Audio-Language Models via Context-Agnostic and Imperceptible Auditory Prompt Injection

**作者**：Meng Chen, Kun Wang, Li Lu, Jiaheng Zhang, Tianwei Zhang
**会议/来源**：arXiv preprint `cs.CR` (2026-04-16)
**来源备注（权威来源原文）**：Accepted by IEEE S&P 2026
**链接**：[论文主页](https://arxiv.org/abs/2604.14604) | [正文](https://arxiv.org/pdf/2604.14604) | [Google Scholar](https://scholar.google.com/scholar?q=%22Hijacking+Large+Audio-Language+Models+via+Context-Agnostic+and+Imperceptible+Auditory+Prompt+Injection%22)
**分类**：arXiv 预印本
**研究类别**：Prompt Injection

**Abstract (EN — 权威来源原文)**：

> Modern Large audio-language models (LALMs) power intelligent voice interactions by tightly integrating audio and text. This integration, however, expands the attack surface beyond text and introduces vulnerabilities in the continuous, high-dimensional audio channel. While prior work studied audio jailbreaks, the security risks of malicious audio injection and downstream behavior manipulation remain underexamined. In this work, we reveal a previously overlooked threat, auditory prompt injection, under realistic constraints of audio data-only access and strong perceptual stealth. To systematically analyze this threat, we propose \textit{AudioHijack}, a general framework that generates context-agnostic and imperceptible adversarial audio to hijack LALMs. \textit{AudioHijack} employs sampling-based gradient estimation for end-to-end optimization across diverse models, bypassing non-differentiable audio tokenization. Through attention supervision and multi-context training, it steers model attention toward adversarial audio and generalizes to unseen user contexts. We also design a convolutional blending method that modulates perturbations into natural reverberation, making them highly imperceptible to users. Extensive experiments on 13 state-of-the-art LALMs show consistent hijacking across 6 misbehavior categories, achieving average success rates of 79\%-96\% on unseen user contexts with high acoustic fidelity. Real-world studies demonstrate that commercial voice agents from Mistral AI and Microsoft Azure can be induced to execute unauthorized actions on behalf of users. These findings expose critical vulnerabilities in LALMs and highlight the urgent need for dedicated defense.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{chen2026hijackinglargeaudiolanguagemodels,
      title={Hijacking Large Audio-Language Models via Context-Agnostic and Imperceptible Auditory Prompt Injection}, 
      author={Meng Chen and Kun Wang and Li Lu and Jiaheng Zhang and Tianwei Zhang},
      year={2026},
      eprint={2604.14604},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2604.14604}, 
}
```

---

### [5]. When AI Meets the Web: Prompt Injection Risks in Third-Party AI Chatbot Plugins

**作者**：Yigitcan Kaya, Anton Landerer, Stijn Pletinckx, Michelle Zimmermann, Christopher Kruegel, Giovanni Vigna
**会议/来源**：arXiv preprint `cs.CR` (2025-11-08)
**来源备注（权威来源原文）**：At IEEE S&P 2026
**链接**：[论文主页](https://arxiv.org/abs/2511.05797) | [正文](https://arxiv.org/pdf/2511.05797) | [Google Scholar](https://scholar.google.com/scholar?q=%22When+AI+Meets+the+Web%3A+Prompt+Injection+Risks+in+Third-Party+AI+Chatbot+Plugins%22)
**分类**：arXiv 预印本
**研究类别**：Prompt Injection

**Abstract (EN — 权威来源原文)**：

> Prompt injection attacks pose a critical threat to large language models (LLMs), with prior work focusing on cutting-edge LLM applications like personal copilots. In contrast, simpler LLM applications, such as customer service chatbots, are widespread on the web, yet their security posture and exposure to such attacks remain poorly understood. These applications often rely on third-party chatbot plugins that act as intermediaries to commercial LLM APIs, offering non-expert website builders intuitive ways to customize chatbot behaviors. To bridge this gap, we present the first large-scale study of 17 third-party chatbot plugins used by over 10,000 public websites, uncovering previously unknown prompt injection risks in practice. First, 8 of these plugins (used by 8,000 websites) fail to enforce the integrity of the conversation history transmitted in network requests between the website visitor and the chatbot. This oversight amplifies the impact of direct prompt injection attacks by allowing adversaries to forge conversation histories (including fake system messages), boosting their ability to elicit unintended behavior (e.g., code generation) by 3 to 8x. Second, 15 plugins offer tools, such as web-scraping, to enrich the chatbot's context with website-specific content. However, these tools do not distinguish the website's trusted content (e.g., product descriptions) from untrusted, third-party content (e.g., customer reviews), introducing a risk of indirect prompt injection. Notably, we found that ~13% of e-commerce websites have already exposed their chatbots to third-party content. We systematically evaluate both vulnerabilities through controlled experiments grounded in real-world observations, focusing on factors such as system prompt design and the underlying LLM. Our findings show that many plugins adopt insecure practices that undermine the built-in LLM safeguards.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{kaya2025aimeetswebprompt,
      title={When AI Meets the Web: Prompt Injection Risks in Third-Party AI Chatbot Plugins}, 
      author={Yigitcan Kaya and Anton Landerer and Stijn Pletinckx and Michelle Zimmermann and Christopher Kruegel and Giovanni Vigna},
      year={2025},
      eprint={2511.05797},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2511.05797}, 
}
```

---

### [6]. Five Queries Are Enough: Query-Efficient and Surrogate-Free Membership Inference Attacks on RAG via Entailment

**作者**：Nguyen Linh Bao Nguyen, Wanlun Ma, Viet Vo, Alsharif Abuadbba, Minghong Fang, Jun Zhang, Yang Xiang
**会议/来源**：arXiv preprint `cs.CR` (2026-05-23)
**来源备注（权威来源原文）**：Accepted by USENIX Security 2026
**链接**：[论文主页](https://arxiv.org/abs/2605.24312) | [正文](https://arxiv.org/pdf/2605.24312) | [Google Scholar](https://scholar.google.com/scholar?q=%22Five+Queries+Are+Enough%3A+Query-Efficient+and+Surrogate-Free+Membership+Inference+Attacks+on+RAG+via+Entailment%22)
**分类**：arXiv 预印本
**研究类别**：Jailbreak

**Abstract (EN — 权威来源原文)**：

> Retrieval-augmented generation (RAG) has become central to large language model (LLM) deployments, grounding responses in enterprise or proprietary data to reduce hallucinations. However, this design introduces a new privacy risk: model outputs may signal the presence of specific documents in the retrieval corpus, enabling membership inference attacks (MIAs) that leak sensitive information. Existing MIAs are feasible, but they often rely on easily detected templated queries or require many non-templated yet costly and repetitive queries, limiting practicality. We ask: Can an adversary launch a limited-budget, surrogate-free, stealthy, and defense-agnostic membership inference attack using non-templated queries? We present MEntA (Membership Entailment Attack), a query-efficient MIA that leverages natural-language entailment to maximize information gained per query. By asking low-cost, broad, information-seeking questions and measuring entailment between model responses and candidate documents, MEntA eliminates the need for costly shadow models and large query budgets. Across NFCorpus, SCIDOCS, and TREC-COVID, MEntA achieves up to 0.991 AUC with only 5 queries, outperforming prior methods by up to 0.42 AUC under equivalent conditions. It remains effective under state-of-the-art (SOTA) RAG defenses, while current detectors either miss MEntA or flag benign queries at high rates. Regarding cost, MEntA reduces total attack cost by up to 65$\times$ lower compared to SOTA attacks under the same attack setting. Our findings expose the feasibility of realistic, low-cost privacy leakage in RAG systems and highlight the urgent need for privacy-aware retrieval and defense mechanisms.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{nguyen2026queriesenoughqueryefficientsurrogatefree,
      title={Five Queries Are Enough: Query-Efficient and Surrogate-Free Membership Inference Attacks on RAG via Entailment}, 
      author={Nguyen Linh Bao Nguyen and Wanlun Ma and Viet Vo and Alsharif Abuadbba and Minghong Fang and Jun Zhang and Yang Xiang},
      year={2026},
      eprint={2605.24312},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2605.24312}, 
}
```

---

### [7]. Window-based Membership Inference Attacks Against Fine-tuned Large Language Models

**作者**：Yuetian Chen, Yuntao Du, Kaiyuan Zhang, Ashish Kundu, Charles Fleming, Bruno Ribeiro, Ninghui Li
**会议/来源**：arXiv preprint `cs.CL` (2026-01-06)
**来源备注（权威来源原文）**：Accepted to USENIX Security 2026. This extended arXiv version includes complete experimental results. The source code is publicly available at: https://github.com/Stry233/WBC/
**链接**：[论文主页](https://arxiv.org/abs/2601.02751) | [正文](https://arxiv.org/pdf/2601.02751) | [Google Scholar](https://scholar.google.com/scholar?q=%22Window-based+Membership+Inference+Attacks+Against+Fine-tuned+Large+Language+Models%22)
**分类**：arXiv 预印本
**研究类别**：Privacy

**Abstract (EN — 权威来源原文)**：

> Most membership inference attacks (MIAs) against Large Language Models (LLMs) rely on global signals, like average loss, to identify training data. This approach, however, dilutes the subtle, localized signals of memorization, reducing attack effectiveness. We challenge this global-averaging paradigm, positing that membership signals are more pronounced within localized contexts. We introduce WBC (Window-Based Comparison), which exploits this insight through a sliding window approach with sign-based aggregation. Our method slides windows of varying sizes across text sequences, with each window casting a binary vote on membership based on loss comparisons between target and reference models. By ensembling votes across geometrically spaced window sizes, we capture memorization patterns from token-level artifacts to phrase-level structures. Extensive experiments across eleven datasets demonstrate that WBC substantially outperforms established baselines, achieving higher AUC scores and 2-3 times improvements in detection rates at low false positive thresholds. Our findings reveal that aggregating localized evidence is fundamentally more effective than global averaging, exposing critical privacy vulnerabilities in fine-tuned LLMs.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{chen2026windowbasedmembershipinferenceattacks,
      title={Window-based Membership Inference Attacks Against Fine-tuned Large Language Models}, 
      author={Yuetian Chen and Yuntao Du and Kaiyuan Zhang and Ashish Kundu and Charles Fleming and Bruno Ribeiro and Ninghui Li},
      year={2026},
      eprint={2601.02751},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2601.02751}, 
}
```

---

### [8]. Depth Gives a False Sense of Privacy: LLM Internal States Inversion

**作者**：Tian Dong, Yan Meng, Shaofeng Li, Guoxing Chen, Zhen Liu, Haojin Zhu
**会议/来源**：arXiv preprint `cs.CR` (2025-07-22)
**来源备注（权威来源原文）**：Accepted by USENIX Security 2025. Please cite this paper as "Tian Dong, Yan Meng, Shaofeng Li, Guoxing Chen, Zhen Liu, Haojin Zhu. Depth Gives a False Sense of Privacy: LLM Internal States Inversion. In the 34th USENIX Security Symposium (USENIX Security '25)."
**链接**：[论文主页](https://arxiv.org/abs/2507.16372) | [正文](https://arxiv.org/pdf/2507.16372) | [Google Scholar](https://scholar.google.com/scholar?q=%22Depth+Gives+a+False+Sense+of+Privacy%3A+LLM+Internal+States+Inversion%22)
**分类**：arXiv 预印本
**研究类别**：Privacy

**Abstract (EN — 权威来源原文)**：

> Large Language Models (LLMs) are increasingly integrated into daily routines, yet they raise significant privacy and safety concerns. Recent research proposes collaborative inference, which outsources the early-layer inference to ensure data locality, and introduces model safety auditing based on inner neuron patterns. Both techniques expose the LLM's Internal States (ISs), which are traditionally considered irreversible to inputs due to optimization challenges and the highly abstract representations in deep layers. In this work, we challenge this assumption by proposing four inversion attacks that significantly improve the semantic similarity and token matching rate of inverted inputs. Specifically, we first develop two white-box optimization-based attacks tailored for low-depth and high-depth ISs. These attacks avoid local minima convergence, a limitation observed in prior work, through a two-phase inversion process. Then, we extend our optimization attack under more practical black-box weight access by leveraging the transferability between the source and the derived LLMs. Additionally, we introduce a generation-based attack that treats inversion as a translation task, employing an inversion model to reconstruct inputs. Extensive evaluation of short and long prompts from medical consulting and coding assistance datasets and 6 LLMs validates the effectiveness of our inversion attacks. Notably, a 4,112-token long medical consulting prompt can be nearly perfectly inverted with 86.88 F1 token matching from the middle layer of Llama-3 model. Finally, we evaluate four practical defenses that we found cannot perfectly prevent ISs inversion and draw conclusions for future mitigation design.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{dong2025depthgivesfalsesense,
      title={Depth Gives a False Sense of Privacy: LLM Internal States Inversion}, 
      author={Tian Dong and Yan Meng and Shaofeng Li and Guoxing Chen and Zhen Liu and Haojin Zhu},
      year={2025},
      eprint={2507.16372},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2507.16372}, 
}
```

---

### [9]. Shadow in the Cache: Unveiling and Mitigating Privacy Risks of KV-cache in LLM Inference

**作者**：Zhifan Luo, Shuo Shao, Su Zhang, Lijing Zhou, Yuke Hu, Chenxu Zhao, Zhihao Liu, Zhan Qin
**会议/来源**：arXiv preprint `cs.CR` (2025-08-13)
**来源备注（权威来源原文）**：This paper is accepted by Network and Distributed System Security Symposium (NDSS) 2026. Code: https://github.com/SiO-2/kvcloak
**链接**：[论文主页](https://arxiv.org/abs/2508.09442) | [正文](https://arxiv.org/pdf/2508.09442) | [Google Scholar](https://scholar.google.com/scholar?q=%22Shadow+in+the+Cache%3A+Unveiling+and+Mitigating+Privacy+Risks+of+KV-cache+in+LLM+Inference%22)
**分类**：arXiv 预印本
**研究类别**：Privacy

**Abstract (EN — 权威来源原文)**：

> The Key-Value (KV) cache, which stores intermediate attention computations (Key and Value pairs) to avoid redundant calculations, is a fundamental mechanism for accelerating Large Language Model (LLM) inference. However, this efficiency optimization introduces significant yet underexplored privacy risks. This paper provides the first comprehensive analysis of these vulnerabilities, demonstrating that an attacker can reconstruct sensitive user inputs directly from the KV-cache. We design and implement three distinct attack vectors: a direct Inversion Attack, a more broadly applicable and potent Collision Attack, and a semantic-based Injection Attack. These methods demonstrate the practicality and severity of KV-cache privacy leakage issues. To mitigate this, we propose KV-Cloak, a novel, lightweight, and efficient defense mechanism. KV-Cloak uses a reversible matrix-based obfuscation scheme, combined with operator fusion, to secure the KV-cache. Our extensive experiments show that KV-Cloak effectively thwarts all proposed attacks, reducing reconstruction quality to random noise. Crucially, it achieves this robust security with virtually no degradation in model accuracy and minimal performance overhead, offering a practical solution for trustworthy LLM deployment.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{luo2026shadowcacheunveilingmitigating,
      title={Shadow in the Cache: Unveiling and Mitigating Privacy Risks of KV-cache in LLM Inference}, 
      author={Zhifan Luo and Shuo Shao and Su Zhang and Lijing Zhou and Yuke Hu and Chenxu Zhao and Zhihao Liu and Zhan Qin},
      year={2026},
      eprint={2508.09442},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2508.09442}, 
}
```

---

### [10]. When the Aggregator Cheats: Data-Free Backdoors in Federated LLM-based QA Systems

**作者**：Chenqing Zhu, Yanbo Dai, Yulong Tian, Qingming Li, Songze Li
**会议/来源**：arXiv preprint `cs.CR` (2026-06-25)
**来源备注（权威来源原文）**：Accepted at the 35th USENIX Security Symposium (USENIX Security 2026)
**链接**：[论文主页](https://arxiv.org/abs/2606.27511) | [正文](https://arxiv.org/pdf/2606.27511) | [Google Scholar](https://scholar.google.com/scholar?q=%22When+the+Aggregator+Cheats%3A+Data-Free+Backdoors+in+Federated+LLM-based+QA+Systems%22)
**分类**：arXiv 预印本
**研究类别**：Backdoor

**Abstract (EN — 权威来源原文)**：

> Large Language Model (LLM)-based question-answering (QA) systems are increasingly deployed in sensitive domains such as healthcare, mental health counseling, and legal consultation. Federated learning (FL) enables collaborative training without sharing raw client data, for which locally trained models are aggregated at a central server (i.e., a cloud service provider) to obtain a global model. In this paper, we explore the potential vulnerability where a malicious aggregator, who may collude with a third-party vendor, stealthily implants advertisement-type backdoors into federated QA models, without ever accessing client data. The attacker's goals are twofold: (1) preserve clean QA fidelity (i.e., the poisoned model behaves like a clean model on non-triggered queries); and (2) generate highly natural, contextually relevant responses with target advertisements when a trigger appears. Achieving these two goals simultaneously is highly challenging, as naive backdoor injection without knowledge about private data may degrade model's clean performance or fail to inject the target. Motivated by this, we propose to leverage clients' uploaded gradients during training, and develop a two-stage framework for data-free and stealthy poisoning: (1) recover representative training samples from client gradients, and (2) construct poisoning datasets utilizing recovered samples and trigger phrases to inject backdoors into the global model. Experiments across representative QA datasets and LLM families under full fine-tuning and LoRA settings demonstrate that, our method achieves nearly 100% Attack Success Rate (ASR) while incurring negligible degradation on clean tasks. Crucially, reconstructing only 5-20% of gradients suffices to mount a reliable attack, exposing a practical blind spot in the pipeline of federated training of QA LLMs.

**摘要 (中文，LLM 生成)**：

（待解读）

**问题（LLM 解读）**：

（待解读）

**方法（LLM 解读）**：

（待解读）

**结果（LLM 解读）**：

（待解读）

**贡献（LLM 解读）**：

（待解读）

**BibTeX（权威端点原文）**：

```bibtex
@misc{zhu2026aggregatorcheatsdatafreebackdoors,
      title={When the Aggregator Cheats: Data-Free Backdoors in Federated LLM-based QA Systems}, 
      author={Chenqing Zhu and Yanbo Dai and Yulong Tian and Qingming Li and Songze Li},
      year={2026},
      eprint={2606.27511},
      archivePrefix={arXiv},
      primaryClass={cs.CR},
      url={https://arxiv.org/abs/2606.27511}, 
}
```

---
