# POL Subdimension Scoring Anchors

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen（2026-08-28），第 5/5 轮 Q66–Q70 已接受。本文的 “scoring anchors” 是 evaluator rubric，不是论文 Source Anchors。

Reviewer question taxonomy 以 `AgentReview/REVIEW_Request.md` 第 77–98 行为冻结基础，SHA256 `609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42`；本文件是详细 evaluator scoring anchors 的权威。通用协议继承自 `role_specific_reference_free_evaluator_protocol_20260828.md`。

Evaluator 评分对象是 **Candidate Review 对论文 policy/compliance 问题的审查表现**，不是直接给论文的伦理或合规水平打分。Candidate 可以因准确确认没有 material concern 而获得高分，也可以因准确指出 material violation/omission 而获得高分。不得按 concern 数量、负面结论数量或政策条款数量计分。

`POLICY` evidence 只能证明 venue/year/cutoff 下的规则内容，不能单独证明论文违规。正式 violation judgment 必须同时绑定适用的 frozen policy evidence ID 与能够证明论文行为/披露状态的 `PAPER` evidence。`ARTIFACT_LICENSE` 只能使用 cutoff 前冻结、属于当前 target paper 的 records。Unavailable evidence 不处罚 Candidate。

## POL Event-Code Vocabulary

POL 使用统一 `{code, subdimension}` schema：

- `MATERIAL_OMISSION`
- `MATERIAL_MISJUDGMENT`
- `FABRICATED_MATERIAL_CLAIM`
- `INVALID_PAPER_EVIDENCE`
- `INVALID_EXTERNAL_EVIDENCE_ID`
- `UNBOUND_EXTERNAL_FACT`
- `DUPLICATE_PADDING`
- `ROLE_LEAKAGE_LOCAL`
- `ROLE_LEAKAGE_SEVERE`
- `SYSTEMATIC_HALLUCINATION`

Authority 修正：LLM Evaluator 只能输出 atomic judgment events；`DUPLICATE_PADDING` 由 deterministic duplicate normalizer 派生，`SYSTEMATIC_HALLUCINATION` 由 parser/aggregator 按 Q30 阈值派生。二者保留在统一 registry 中用于描述最终处理结果，但不得由 Evaluator 直接输出或决定。

POL 不新增统一 compliance penalty。局部错误、systematic hallucination、role leakage 与 duplicate padding 继续按 master protocol 的 validity rules 处理。

## Cross-Subdimension Non-Duplication Rule

同一 material fact 可以被多个 POL subdimension 读取以建立上下文，但同一个 compliance omission 只能归入一个 **primary subdimension** 并影响一次 fitness：

- 风险、fairness、bias、harm、dual-use 与 mitigation 的实质充分性优先归 `POL-ETH`；
- data collection/use、consent、PII/privacy、data licence 与 permitted use 优先归 `POL-DAT`；
- venue-required broader-impact statement、checklist、approval/licence identifiers 与其他 compliance documentation 的存在性/完整性优先归 `POL-IMP`；
- anonymity disclosure 归 `POL-ANO`；
- plagiarism/duplicate-submission evidence 归 `POL-PLG`。

若一项事实支持彼此独立的两个 material judgments，可以分别评分，但 Evaluator 必须输出不同 `judgment_id` 和不同 omission object；不得用同一缺失重复降低多个 subdimensions。

## POL-ETH — Ethics & Responsible-AI Compliance

### Applicability / N/A

- Applicable：从论文自身可识别 material harm、fairness/bias、marginalised-population、dual-use、safety 或 responsible-AI risk pathway；或者合法 frozen policy 明确要求该类风险披露/mitigation。论文缺少应有讨论是可评分 weakness，不能因此设为 `N/A`。
- `N/A`：论文没有可识别 material ethical-risk pathway，且没有适用的 frozen policy obligation。不得仅凭一般性技术主题人为制造伦理风险。

### Allowed Evidence

- `PAPER`：用途、affected groups、limitations、risk/ethics statement、fairness/bias/safety evidence 与 mitigations。
- `STRUCTURAL`：ethics/limitations/impact/safety section 与 checklist presence；只证明结构存在，不证明内容充分。
- `FROZEN_EXTERNAL/POLICY`：venue/year/cutoff 的 ethics、risk-disclosure 或 mitigation 义务。
- `FROZEN_EXTERNAL/ARTIFACT_LICENSE`：仅当 paper 明确引用且其内容直接支持当前伦理判断。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 applicable ethical issues；或 fabricated/misread harm、affected group、policy obligation 或 mitigation，使判断失效。
- `1`：泛称“有伦理风险/没有问题”，未识别 material pathway、affected group、evidence 或 obligation；遗漏核心 concern。
- `2`：正确评价部分 material risks/mitigations，但遗漏至少一个会明显改变合规判断的 pathway、population、severity、uncertainty 或 frozen obligation，或把 speculative concern 当作已证实违规。
- `3`：准确、具体、grounded 地评价主要 risks、affected groups、mitigations、uncertainty 与适用义务；仅有次要遗漏。
- `4`：充分评价所有 material ethical/responsible-AI concerns，清楚区分潜在风险、已观察 harm、披露义务与 mitigation sufficiency，并保持严格 evidence boundary；不要求人为制造 concern。

### Material Error

包括：虚构 harmed group 或 dual-use scenario；把潜在风险说成已发生伤害；无 frozen policy evidence 宣称违反特定规则；忽略论文中足以改变判断的 material mitigation 或明确 material risk。

### Role Leakage

现实影响的范围/重要性属于 SIGN-IMP；数据 consent/privacy/licence 属于 POL-DAT；checklist/approval documentation 属于 POL-IMP。POL-ETH 只评价 ethical-risk substance 与 mitigation adequacy。

### Admissible Event Codes

使用本文件十个 POL codes，`subdimension="POL-ETH"`；外部义务判断必须支持 `UNBOUND_EXTERNAL_FACT` 与 `INVALID_EXTERNAL_EVIDENCE_ID`。

## POL-DAT — Data Usage & Privacy Compliance

### Applicability / N/A

- Applicable：论文收集、处理、链接、发布或依赖 human/personal/sensitive/restricted/licensed data，或对 consent、privacy、anonymisation、data licence、permitted use 作 material compliance claim。缺少本应存在的 consent/licence/privacy 证据是可评分 weakness，不能因此设为 `N/A`。
- `N/A`：论文不使用上述数据，也没有 material data-use compliance claim。仅使用完全生成且不涉及外部权利/个人信息的数据时通常为 `N/A`。

### Allowed Evidence

- `PAPER`：data source、collection/use、subjects/users、consent、PII/privacy、anonymisation、licence 与 use restrictions。
- `STRUCTURAL`：data statement、datasheet、consent/privacy/licence section 与 artifact-link presence。
- `FROZEN_EXTERNAL/POLICY`：适用的数据、privacy、consent 或 licence 披露规则。
- `FROZEN_EXTERNAL/ARTIFACT_LICENSE`：论文明确引用的数据页面、terms、licence、IRB/consent record；影响判断必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 applicable data/privacy issues；或 fabricated/misread data source、PII、consent、licence、permission 或 policy，使判断失效。
- `1`：只声称“合规/可能违规”，未说明 data object、use、right/obligation 与 evidence；遗漏核心 concern。
- `2`：正确评价部分 data-use compliance，但遗漏至少一个会明显改变判断的 material consent、privacy、licence term、permitted use、population 或 unavailable-evidence boundary。
- `3`：准确、具体、grounded 地评价主要 data objects、uses、privacy/consent/licence obligations 与 evidence；仅有次要遗漏。
- `4`：充分评价所有 material data/privacy compliance issues，区分论文声明、外部 terms、实际 use 与仍不可判定部分，并保持严格 evidence boundary。

### Material Error

包括：无 evidence ID 宣称某数据 licence/terms；把 public access 等同于 unrestricted use；虚构 consent/IRB/PII；忽略明确不兼容的 frozen licence term；因 evidence unavailable 而处罚 Candidate。

### Role Leakage

数据质量/代表性对实验可靠性的影响属于 QUAL；fairness/harm substance 属于 POL-ETH；文档是否按 venue 要求提交归 POL-IMP。POL-DAT 只评价 data-use、privacy、consent 与 licence compliance。

### Admissible Event Codes

使用本文件十个 POL codes，`subdimension="POL-DAT"`；外部 terms 判断必须支持 `UNBOUND_EXTERNAL_FACT` 与 `INVALID_EXTERNAL_EVIDENCE_ID`。

## POL-ANO — Double-Blind / Anonymity Compliance

### Applicability / N/A

- Applicable：当前 venue/year/cutoff 的合法 frozen policy 要求匿名或 double-blind submission，并且 formal input identity 保留可审查的 submission text/metadata surface。缺少匿名处理或出现身份泄露是可评分 weakness。
- `N/A`：frozen policy 不要求匿名、policy unavailable，或 formal input 已在上游被不可逆地剥离相关 submission surface，导致无法审查。不得用模型参数知识猜测作者身份。

### Allowed Evidence

- `PAPER`：显式作者/机构名称、自我识别文字、acknowledgements、非匿名引用措辞与可见 URL/identifier。
- `STRUCTURAL`：author/affiliation/acknowledgement/header/footer/link field presence。
- `FROZEN_EXTERNAL/POLICY`：venue/year/cutoff 的匿名范围、例外与 URL/self-citation 规则。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 anonymity；或 fabricated identity/policy/violation，使判断失效。
- `1`：泛称“匿名合规/泄露身份”，未绑定具体 submission evidence 与适用规则；遗漏明显 material disclosure。
- `2`：正确评价部分 anonymity surface，但遗漏至少一个会明显改变判断的 material identifier、policy exception 或 input-observability boundary。
- `3`：准确、具体、grounded 地评价主要 visible identity disclosures 与适用 frozen rules；仅有次要遗漏。
- `4`：充分评价所有 material anonymity surfaces，清楚区分显式泄露、规则允许内容、不可观察外部身份与不确定部分；不要求外部反向识别作者。

### Material Error

包括：通过参数知识或 live lookup 猜作者；把普通公开项目 URL 自动判为违规而未检查 frozen policy；虚构 affiliation/acknowledgement；忽略显式作者姓名或机构。

### Role Leakage

引用完整性属于 QUAL/ORIG 的相应边界；POL-ANO 只评价 submission anonymity obligation，不评价研究内容本身。

### Admissible Event Codes

使用本文件十个 POL codes，`subdimension="POL-ANO"`；policy 判断必须支持 `UNBOUND_EXTERNAL_FACT` 与 `INVALID_EXTERNAL_EVIDENCE_ID`。

## POL-PLG — Plagiarism & Dual Submission

### Applicability / N/A

- Applicable：只有当前 target paper 存在可采纳、可确定绑定的 overlap/duplicate-submission evidence，例如未来冻结的 `PLAGIARISM_OVERLAP` partition，或 PAPER 自身明确披露可核验的 material reuse/parallel submission。
- `N/A`：没有上述确定 evidence。v1 不建立 `PLAGIARISM_OVERLAP`，因此通常必须为 `N/A`；不得从 prior-work similarity、写作风格、模型记忆或“未发现证据”确认无抄袭。

### Allowed Evidence

- `PAPER`：明确披露的 prior version、material reuse、parallel/dual submission 声明及对应文本位置。
- `STRUCTURAL`：只用于定位 disclosure/citation，不足以证明 overlap 或 duplicate submission。
- `FROZEN_EXTERNAL/POLICY`：venue/year/cutoff 的 plagiarism、reuse、prior-publication 与 dual-submission 规则，只证明规则。
- v1 没有可用于证明文本/figure overlap 的 external partition；`PRIOR_WORK` records 不自动等同于 overlap evidence。

### 0–4 Subdimension Scoring Anchors

- `0`：在 applicable case 中没有有效评价，或 fabricated/misread overlap、submission status、source、policy 或 attribution，使判断失效。
- `1`：只声称“抄袭/无问题”，未说明 reuse object、attribution、submission fact、rule 与 evidence。
- `2`：正确评价部分 material reuse/dual-submission evidence，但遗漏至少一个会明显改变判断的 source、scope、attribution、policy exception 或 uncertainty。
- `3`：准确、具体、grounded 地评价主要 overlap/reuse/submission evidence 与适用规则；仅有次要遗漏。
- `4`：充分评价所有 material evidence，清楚区分合法引用/扩展、自我复用、plagiarism 与 dual submission，并严格限制在 frozen evidence 能证明的范围。

### Material Error

包括：无确定 evidence 指控或排除 plagiarism/dual submission；把主题/方法相似自动当作文本复用；把 frozen policy 单独当作违规证据；把 absence of evidence 当作合规证明。

### Role Leakage

相对 prior work 的原创性属于 ORIG；citation/attribution 是否构成 plagiarism 只有在本 subdimension 的 applicability evidence 满足时才进入 POL-PLG。

### Admissible Event Codes

使用本文件十个 POL codes，`subdimension="POL-PLG"`；无合法 evidence 时必须 `N/A`，不能产生合规奖励或违规处罚。

## POL-IMP — Broader-Impact & Compliance Documentation

### Applicability / N/A

- Applicable：合法 frozen policy 对当前 submission 要求 broader-impact/ethics statement、checklist、IRB/ethics approval、consent/licence identifier 或其他 material compliance documentation；或者 PAPER 明确声称已经提供这些合规材料。缺少 required document/identifier 是可评分 weakness，不能因此设为 `N/A`。
- `N/A`：没有可引用的 frozen obligation，且论文没有 material compliance-documentation claim。Policy 页面 unavailable 时不得用当前规则或参数知识补齐。

### Allowed Evidence

- `PAPER`：broader-impact/ethics statement、checklist answers、approval/consent/licence identifiers 与 compliance claims。
- `STRUCTURAL`：required section/checklist/identifier field presence；不证明内容真实或充分。
- `FROZEN_EXTERNAL/POLICY`：venue/year/cutoff 的具体 documentation obligation。
- `FROZEN_EXTERNAL/ARTIFACT_LICENSE`：论文明确引用的 approval、consent、licence 或 artifact record。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 applicable documentation obligations；或 fabricated/misread requirement、document、identifier 或 external record，使判断失效。
- `1`：只称“材料齐全/缺失”，未说明 required object、内容、evidence 或适用规则；遗漏核心 obligation。
- `2`：正确评价部分 documentation compliance，但遗漏至少一个会明显改变判断的 material checklist item、statement、identifier、approval/licence evidence 或 policy exception。
- `3`：准确、具体、grounded 地评价主要 required documents、identifiers 与 frozen obligations；仅有次要遗漏。
- `4`：充分评价所有 material documentation obligations，清楚区分结构存在、内容充分、外部记录可核验与 evidence unavailable，并保持严格 evidence boundary。

### Material Error

包括：无 frozen policy 宣称某 checklist/statement 必需；把 section presence 等同于 substantive adequacy；虚构 IRB/licence/consent identifier；因 cutoff 前记录 unavailable 而处罚 Candidate。

### Role Leakage

社会影响的范围/重要性属于 SIGN-IMP；伦理风险 substance 属于 POL-ETH；数据实际使用与 licence compatibility 属于 POL-DAT。POL-IMP 只评价 frozen policy 所要求的 documentation/compliance evidence 是否存在且足够。

### Admissible Event Codes

使用本文件十个 POL codes，`subdimension="POL-IMP"`；外部义务判断必须支持 `UNBOUND_EXTERNAL_FACT` 与 `INVALID_EXTERNAL_EVIDENCE_ID`。

## Frozen Round-5 Decisions

- `POL-ETH` 只在 `PAPER` 存在可识别 material ethical-risk pathway，或合法 frozen `POLICY` 要求相应 disclosure/mitigation 时 applicable。普通研究不得人为制造泛化伦理问题；存在 material fairness、bias、harm、dual-use 等风险但讨论不足时属于 weakness，不能设为 `N/A`。
- `POL-DAT` 在论文使用 human、personal、sensitive、restricted、licensed data，或提出 consent/privacy/anonymisation/licence compliance claim 时 applicable。按数据使用性质本应存在但缺失的 consent、privacy、permitted-use 或 licence evidence 属于 weakness；只有不存在相关数据使用与合规对象时为 `N/A`。
- `POL-ANO` 只有在 frozen `POLICY` 证明该 venue/year/cutoff 存在适用匿名规则，并且 formal text-only input 保留可检查的 submission text/metadata surface 时 applicable。只评价实际可见的姓名、机构、acknowledgement、自我识别文本、URL 等；不得依赖 parametric knowledge、作者识别或外部反向搜索。Policy unavailable 或相关信息已由上游剥离时为 `N/A`。
- v1 没有经过审计的 `PLAGIARISM_OVERLAP` partition，因此 `POL-PLG` 默认 `N/A`；只有 `PAPER` 自身明确披露且存在可核验 material reuse/parallel-submission evidence 时才允许评分。不得把 `PRIOR_WORK` 的主题、方法或文本语义相似性当作 plagiarism evidence，不得用模型参数记忆进行指控，也不得因 absence of evidence 确认不存在 plagiarism/dual submission 或给予高分。
- POL non-duplication ownership 已冻结：ETH=风险/fairness/bias/harm/dual-use/mitigation；DAT=数据使用/consent/privacy/licence/permitted use；IMP=policy-required statement/checklist/approval/licence identifier 等文档义务；ANO=匿名合规；PLG=plagiarism/material reuse/dual submission。一个 omission 只能归属一个 primary subdimension 并降低一次 fitness；不同评价对象的独立问题必须分别输出唯一 `judgment_id`。
- 继续使用统一 event code + `subdimension`，不新增 concern-count bonus 或统一 compliance penalty。
