# Global Top-3 Finalist Selection Protocol

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen contract（2026-08-29），Q121–Q140 已接受。本文冻结shared-pool unique-Program top-3、ordering、failure与winner规则；Program/Candidate machine artifacts与gate vectors仍待物化。完整identity和数值边界见 `canonical_fitness_program_and_candidate_identity_protocol_20260829.md`，journal authority见 `formal_controller_journal_and_recovery_protocol_20260829.md`。

## 1. Fixed Global Top-3

每个role的shared eligible pool包含三个合法initial seeds与全部成功完成static-25 canonical evaluation的children。先按严格Program identity去重，再从全部unique Programs中选择固定K=3的global finalists。

K=3的协议依据是预注册、固定且可控的full-pool finalist reevaluation budget。三个initial seeds只是search起点，不拥有finalist席位；三个finalists可以全部来自同一seed lineage。K不得根据eligible pool规模、lineage分布、score gap或其他结果动态改变。

## 2. Strict Program Deduplication

只有完整 canonical Program manifest 生成的 `program_identity_sha256` 完全相同时，才视为同一个unique Program。Identity至少绑定：

- exact Program/code bytes；
- prompt/instruction/config/registry/dependency identity；
- Reviewer/runtime-relevant behavior identity；
- MemoryProgram schema/behavior assets；
- allowed toolkit/API capability与manifest/schema version。

代码相似、同lineage、同parent、同static fitness或语义相似均不足以去重。代码仅空白、注释或格式不同也视为不同Program。Admission时已执行strict dedup；finalist阶段再次去重是防御性完整性检查。每组严格重复Program只保留canonical candidate ID最小的代表参与finalist排序；所有其他实例的candidate/lineage/parent/attempt provenance完整保留在dedup audit。

## 3. Static Ordering and Boundary

Unique eligible Programs按以下键排序：

1. `static_25_fitness` descending；
2. 完整 `cand_` + 64位小写SHA256格式的 `canonical_candidate_id` ascending。

第3/4名即使canonical Decimal fitness同分，也只选择排序前3，不扩容、不按lineage保留、不引入其他result-dependent tie-break。Static fitness必须遵循paper内 applicable-subdimension平均、再跨evaluable papers平均的两层语义，并使用precision 50、scale 12、`ROUND_HALF_EVEN`生成的canonical Decimal固定字符串；禁止跨paper flatten、平均已舍入paper decimals或依赖platform float比较。Canonical numerator/denominator保留作审计来源；不同exact fitness量化为相同Decimal时不回退比较rational。

Static-25 fitness只决定finalist membership，不与full-pool fitness组合。

## 4. No Replacement

- 去重后unique eligible Programs少于3：role formal run fail-fast；
- finalist发生Candidate-originated runtime/protocol failure：winner selection fail-fast；
- Evaluator两次均非法：按Q29 fail-fast；
- infrastructure failure：恢复同一finalist的同一evaluation；
- 禁止启用第4名递补，禁止只在剩余finalists中继续选择。

Finalist identities一经top-3 manifest提交即不可改变。

## 5. Full-Pool Winner

三名finalists在完全相同的frozen B-local full pool、paper order、Evaluator Identity、exclusion set/denominator与fresh-session protocol下重评。排序键为：

1. `full_pool_fitness` descending；
2. `canonical_candidate_id` ascending。

第一名成为唯一role winner。Static-25 fitness不与full-pool fitness加权/平均，也不作为full-pool tie-break。

## 6. Required Audit

Formal audit至少保存：

- 去重前eligible candidates与全部identity/provenance；
- strict Program dedup groups与代表选择；
- 完整canonical Decimal static ranking与top-3 boundary；
- committed finalist manifest与SHA；
- 三名finalists的static/full-pool canonical records；
- full-pool applicability/exclusion set/denominator；
- full-pool canonical Decimal ranking、winner与tie-break provenance；
- infrastructure replay/failure audit；
- Program identity、fitness arithmetic与candidate-ID versions。
