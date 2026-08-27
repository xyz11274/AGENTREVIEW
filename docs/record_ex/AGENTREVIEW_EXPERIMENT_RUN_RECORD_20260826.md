# AgentReview 历史测试与实验运行记录（截至 2026-08-26）

## 1. 审计范围与口径

本记录是对以下现存证据的只读审计，不重新运行模型、evolution、CoCo 或测试：

- 仓库日志：`/data/xyj/shengao/AgentReview_0811/AgentReview/logs/`、`pilot_full*.log`
- MSTAR 产物：`/data/xyj/shengao/AgentReview_0811/AgentReview/runs/`
- Reviewer/CoCo 临时运行：`/tmp/agentreview-*`
- 动态快照时间：**2026-08-26 11:05:11 CST**

状态含义：

- **完整**：存在流程完成标记或最终 `best_eval_report.json` / `evaluation.json`，且对应阶段所需产物齐全。
- **部分**：完成了部分 seed、部分阶段或部分 task，但没有该轮最终产物。
- **失败**：日志包含终止异常，且没有后续产物证明同目录内恢复成功。
- **完整（恢复后）**：同一目录中早期尝试失败，但后续 artifact 证明最终完成。
- **进行中**：审计时进程仍活跃，不能把当前中间分数视为最终结果。

耗时口径：

- **精确墙钟**：来自 `time` 输出、日志内 `real`，或同一阶段日志的 birth/mtime。
- **artifact 区间**：由配置、checkpoint、输出文件的时间戳相减；可能包含调度、人工修复或等待时间。
- **模型调用下界**：对日志内 `[API DONE] elapsed` 与明确的 `[API FAILED] ... after` 求和；不是完整墙钟。
- 早期 pilot 文件在当前 checkout 的 mtime 多被统一为 2026-08-11，不能用其 mtime 恢复原始运行时间；相关日期主要来自文件名、日志内 request id 和完成标记。

## 2. 总览

| 轮次 | 日期/时间 | 规模 | 状态 | 核心结果 | 可证实耗时 |
|---|---|---|---|---|---|
| `pilot_qual` | 原始时间不可恢复，早于当前 checkout | QUAL；1 iteration；static 2；rotating 1；A 10 | 完整（artifact-only） | 选中 `program_003_child`；selection mean 0.935688；final static 0.947252 | 不可恢复 |
| `pilot_suite` | 原始时间不可恢复 | QUAL/CLAR/SIGN/ORIG；每 task 1 iteration | 部分 | QUAL、CLAR、SIGN 完成；ORIG 仅 seed0；POL/META 无本轮产物 | 不可恢复 |
| `pilot_full_20260729` | 2026-07-29 | QUAL；2 iterations；static 2；rotating 2；A 10 | 部分 | 3 seed + 2 child 均出分；无最终 selection/best report | 模型完成调用累计 16m50.5s（下界） |
| `pilot_full_iter5` | 2026-07-29/30，文件名无精确时刻 | QUAL；5 iterations；2/2/10 | 部分 | 仅 vector seed 出分 0.958333；seed1 开始后中断 | 模型完成调用累计 2m02s（下界） |
| `pilot_full_iter5_mix` | 2026-07-30，文件名无精确时刻 | QUAL；5 iterations；2/2/10 | 失败/部分 | vector seed 0.968963；seed1 写入阶段超时中断 | 完成调用累计 3m05.1s（下界；未含未完成超时） |
| `pilot_full_iter5_202607300034` | 2026-07-30 约 00:34 起 | QUAL；5 iterations；2/2/10 | 部分 | vector、llm seed 均 0.968963；experience seed 未完成 | 已完成+明确失败调用累计 22m11.7s（下界） |
| `pilot_full_iter5_202607300043` | 2026-07-30 约 00:43 起 | QUAL；5 iterations；2/2/10 | 失败/部分 | vector seed 0.968963；seed1 未完成 | 已完成+明确失败调用累计 22m32s（下界） |
| `pilot_full_iter5_202607300117` | 2026-07-30 约 01:17–06:12 | QUAL/CLAR/SIGN/ORIG/POL/META；各 5 iterations | 完整 | 六个 task 均有 promoted best program 和 final report | 约 4h54m57s；API DONE 累计 4h54m43.5s |
| `formal_coco_v2_20260819` | 2026-08-19 | frozen readiness audit | 完整（准备检查） | A/B/C/D=2391/299/300/899；五 specialist gate 全通过 | 无墙钟记录 |
| Qwen3-Embedding-8B prewarm | 2026-08-22 21:52–23:11 | 12,705 vectors；4096-d；8 GPU | 完整（准备检查） | 0 truncation；single/multi max diff 0 | 1h19m12.7s（日志 birth→mtime） |
| `local_qwen_qual_smoke_seed0_failed_16k...` | 2026-08-23 13:40–13:43 | QUAL；1 iteration；static 2；rotating 1；A 10 | 失败/部分 | 两条 seed 记录均 0；vector quality fail；无最终报告 | 约 3m05s artifact 区间 |
| `local_qwen_bf16_qual_smoke_seed0` | 2026-08-23 15:41–19:47 | QUAL；1 iteration；2/1/10 | 失败/部分 | 三 seed 均记录 0；未进入 child/final selection | 约 4h06m31s artifact 区间 |
| `...fixcheck3` | 2026-08-23，独立时间不可恢复 | vector seed 静态诊断 | 部分诊断快照 | 1 条已完成 evaluation，分数 0 | 不可恢复 |
| `...minfix` | 2026-08-23，独立时间不可恢复 | vector + llm seed 静态诊断 | 部分诊断快照 | 2 条已完成 evaluation，均 0 | 不可恢复 |
| `formal_qual_embedding_runtime_mincheck` | 2026-08-23 22:27–08-24 02:01 | QUAL；3 successful-child budget；static/rotating/A=1/1/1 | 失败/未完成 | 3 seed 均 0；2 次 reflector 均因 16K empty/length 失败 | 3h33m57s artifact 区间 |
| `formal_qual_mincheck_after_qwen_coco_fix` | 2026-08-24 11:04–15:13 | QUAL；3 successful children；1/1/1 | 完整 | best vector seed；selection/final 0.863636；三 lineage 各 1 successful child | 4h08m44s artifact 区间 |
| `/tmp/agentreview-e2e.i7PWiA` | 2026-08-24 20:00–20:04 | C1 Reviewer + CoCo 尝试 | 失败 | 1 candidate，但 response 在 4096 `finish_reason=length`；CoCo discovery 失败 | Reviewer 约 3m45.0s；CoCo fail <1s |
| `/tmp/agentreview-tiny.aca4GX` | 2026-08-24 20:08–20:28 | C1 | 完整（恢复后） | Average 3.25；Complete 6.67 | 全目录 19m19.4s；含失败、修改/等待和后补 evaluation |
| `/tmp/agentreview-full-e2e.KeVR04` | 2026-08-24 20:30–21:28 | tiny MSTAR→Reviewer C1→CoCo | 完整（恢复后） | MSTAR best llm seed；CoCo Average 3.53；Complete 46.67 | 总区间 58m05.3s |
| `/tmp/agentreview-c3-e2e.ndXcIy` | 2026-08-24 21:33–22:12 | C3 | 完整（两次恢复后） | Average 3.269444；Complete 55.740741 | 主 Reviewer 12m06.98s；成功 CoCo 11m13.76s |
| `/tmp/agentreview-c10-e2e.5PRPVo` | 2026-08-24 22:44–08-25 00:01 | C10 | 流程完整，但结果无效 | Average 3.1505；Complete 52.5447；1 篇截断 review 被接受 | Reviewer 27m39.95s；CoCo 48m59.91s |
| `/tmp/agentreview-c10-e2e-fixed-20260825` | 2026-08-25 10:15–11:36 | C10 | 完整、clean | 10/10 完整；Average 3.161167；Complete 53.0513 | Reviewer 26m35.729s；CoCo 54m56.973s；总计 1h21m32.702s |
| `formal_qual_seed0_20260825` | 2026-08-25 11:56 起；快照至 08-26 11:05 | 正式 QUAL；21 iterations；25/5/125 | **进行中** | seed0 完成，0.857248；seed1 A=64/125；尚未 mutation | 快照总区间 23h08m37s |

## 3. 早期 pilot 详细记录

### 3.1 `runs/pilot_qual`

- 状态：**完整，但配置明确标记 `smoke_non_reportable`**。
- 配置：QUAL，`iterations=1`、`static_size=2`、`rotating_size=1`、`episode_budget=10`、seed 0。
- 结果：3 个 seed + 1 个 child，共 4 条 score；`program_003_child` 被选中。
- 指标：static 0.968963；selection mean 0.935688；final static 0.947252。
- 输出：`runs/pilot_qual/best_program.py`、`best_eval_report.json`、`scores.jsonl`、`lineage.jsonl`。
- 限制：没有保存独立运行日志，当前文件时间被 checkout 统一，原始日期和耗时不可恢复。

### 3.2 `runs/pilot_suite`

- 状态：**部分完成**。
- 共同配置：每个 task `iterations=1`、`static_size=2`、`rotating_size=1`、`episode_budget=10`。

| Task | 状态 | selected program | selection mean | final static |
|---|---|---:|---:|---:|
| QUAL | 完整 | `program_000_seed_vector` | 0.912530 | 0.923611 |
| CLAR | 完整 | `program_002_seed_experience` | 0.899903 | 0.946318 |
| SIGN | 完整 | `program_003_child` | 0.900966 | 0.957881 |
| ORIG | 部分 | 仅 `program_000_seed_vector` 出分 | seed static 0.957881 | 无 final report |
| POL | 未发现 | — | — | — |
| META | 未发现 | — | — | — |

证据：`runs/pilot_suite/{QUAL,CLAR,SIGN}/seed_0/best_eval_report.json` 与 `runs/pilot_suite/ORIG/seed_0/scores.jsonl`。

### 3.3 `pilot_full_20260729.log` / `runs/pilot_full_20260729`

- 状态：**evolution 主循环的 2 次 child evaluation 已完成，但整套 pilot 未完成**。
- 配置：QUAL，`iterations=2`、`static_size=2`、`rotating_size=2`、`episode_budget=10`。
- seed：vector 0.9579、llm summarizer 0.9690、experience 0.9583。
- child：iteration 1 = 0.9342；iteration 2 = 0.9125；两次 rotating 均 0.9473。
- 终止点：最后一行是一个新的 `Memory KnowledgeItem` API START；118 次 START、117 次 DONE；没有 `[PILOT] Complete`、`best_eval_report.json` 或 promoted best。
- 耗时：117 个完成调用的 `elapsed` 合计 **1010.5 秒（16m50.5s）**，仅为下界。
- 证据：`pilot_full_20260729.log`、`runs/pilot_full_20260729/QUAL/seed_0/{scores,lineage}.jsonl`。

### 3.4 四轮未完成的 iter5 尝试

| 轮次 | 已完成进度 | 错误/终止 | 耗时证据 |
|---|---|---|---|
| `pilot_full_iter5.log` | vector seed 0.958333；seed1 已开始 | 最后一个 API START 无 DONE | 完成调用累计 122.0s，下界 |
| `pilot_full_iter5_mix.log` | vector seed 0.968963；seed1 已开始 | Memory KnowledgeItem 两次 timeout retry 后日志终止 | 完成调用累计 185.1s，下界，未含未结束请求 |
| `pilot_full_iter5_202607300034.log` | vector 与 llm seed 均 0.968963；seed2 已开始 | 两次请求在约 366.2s/367.5s 后 timeout fail | DONE + 明确 FAILED 合计 1331.7s，下界 |
| `pilot_full_iter5_202607300043.log` | vector seed 0.968963；seed1 已开始 | 至少一次 365.5s timeout fail，之后仍有 retry，最终日志中断 | DONE + 明确 FAILED 合计 1352.0s，下界 |

这些目录均无 `best_eval_report.json`，不能按完整 evolution 结果使用。

### 3.5 `pilot_full_iter5_202607300117.log` / 六任务完整 pilot

- 状态：**完整**；日志结尾存在 `[PILOT] Complete`，六任务均有 `best_program.py` 和 `best_eval_report.json`。
- 配置：每 task 5 iterations、static 2、rotating 2、episode budget 10、seed 0。
- 时间：日志 request id 从约 `20260730011742` 延伸到 `20260730061239`，区间约 **4h54m57s**；2029 个 API DONE 的 elapsed 合计 **17683.5 秒（4h54m43.5s）**。

| Task | promoted program | selection mean | final static | 日志中的 best-seed→best-evolved |
|---|---|---:|---:|---:|
| QUAL | `program_003_child` | 0.974277 | 0.979167 | 0.979592 → 0.989796 |
| CLAR | `program_004_child` | 0.974277 | 0.958333 | 0.968963 → 0.989796 |
| SIGN | `program_005_child` | 0.984694 | 0.957881 | 0.968963 → 0.979167 |
| ORIG | `program_007_child` | 0.979379 | 0.968963 | 0.979592 → 0.979592 |
| POL | `program_002_seed_experience` | 0.989796 | 0.935688 | 0.979167 → 0.956522 |
| META | `program_004_child` | 0.651431 | 0.664730 | 0.649848 → 0.710253 |

注意：最后一列是 `pilot_full_iter5_202607300117.log` 的 evolution summary；`selection mean` 和 `final static` 来自各 task 的 `best_eval_report.json`，是不同评估阶段，数值不应混为一个指标。

完整运行中仍有可恢复错误：47 条 API retry、若干 HTTP 500、一次 timeout retry、至少一次 AST parse quality failure，以及 QUAL/META iteration 5 的 V4A patch context mismatch；这些尝试失败没有阻止整轮完成。

证据：`runs/pilot_full_iter5_202607300117/{suite_manifest,meta_manifest}.json`、各 task 的 `seed_0/best_eval_report.json`、`pilot_full_iter5_202607300117.log`。

## 4. 正式资产准备与本地 Qwen/mincheck

### 4.1 frozen readiness（2026-08-19）

`runs/formal_coco_v2_20260819/00_formal_readiness_{check,register}.log` 均显示 passed：

- historical A = 2391
- evolution validation B = 299
- effectiveness holdout C = 300
- held-out benchmark D = 899
- QUAL/CLAR/SIGN/ORIG/POL 五个 specialist gate 全部通过
- S2 query integrity：3889/3889 checked，0 canonical mismatch，0 malformed/polluted query title

这是准备检查，不是 evolution 或 CoCo 得分轮次。

### 4.2 Qwen3-Embedding-8B prewarm（2026-08-22）

- 状态：**完整**。
- 规模：五 specialist，共 12,705 vectors；dimension 4096；8 个 GPU worker。
- 结果：`truncated_count=0`；`single_multi_max_abs_diff=0.0`；ordered matrix SHA-256 为 `64cab845bdcccf15c11f3f3cc262d56787fb58ff78aa00393ac8b8ba2ce8e3e7`。
- 模型：`Qwen/Qwen3-Embedding-8B`，revision `1d8ad4ca9b3dd8059ad90a75d4983776a23d44af`。
- cache：`cache/qwen3_embedding_8b_cache.sqlite3`。
- 耗时：日志 birth 21:52:32、mtime 23:11:44，约 **1h19m12.7s**。
- 证据：`logs/qwen3_embedding_8b_prewarm.log`。

### 4.3 2026-08-23 local-Qwen smoke

#### FP8 16K 失败轮

- 目录：`runs/local_qwen_qual_smoke_seed0_failed_16k_20260823_134735`。
- 状态：**失败/部分**；配置计划 1 iteration、static 2、rotating 1、A 10。
- 已有两条 score：vector seed quality fail、llm seed completed evaluation；均为 0；experience seed 没有 score。
- 明确错误：vector smoke 访问 formal read-only embedding cache 时 miss：`requested=1 hits=0 misses=1`。
- 无 trace case、无 child、无 best report。
- artifact 时间区间约 13:40:20–13:43:25，约 3m05s；没有独立日志，不能当完整墙钟。

#### BF16 smoke

- 目录：`runs/local_qwen_bf16_qual_smoke_seed0`。
- 状态：**失败/部分**；三 seed 均留下 score，全部为 0；未进入 child/final selection。
- vector seed仍因 formal embedding cache smoke miss 而 `quality_passed=false`；llm 和 experience 记录为 `completed_evaluation=true`，但没有 trace 文件解释其 0 分根因。
- artifact 区间 15:41:23–19:47:54，约 **4h06m31s**。
- 不能仅从 0 分推断 Qwen parser 错误；该目录缺少对应 evaluator 日志。

#### `fixcheck3` / `minfix`

- `runs/local_qwen_bf16_qual_smoke_seed0_fixcheck3`：1 条 vector seed score，quality/evaluation completed，但 static 0。
- `runs/local_qwen_bf16_qual_smoke_seed0_minfix`：vector 与 llm 两条 seed score，均 quality/evaluation completed，但 static 0。
- 两目录缺失 config、llm runtime 和独立运行日志，且文件时间集中在同一秒；只能记为**部分诊断快照**，不能恢复实际命令、独立耗时或最终状态。

### 4.4 `formal_qual_embedding_runtime_mincheck`

- 状态：**失败/未完成**。
- 配置：QUAL；`iterations=3`（successful child budget）；static 1；rotating 1；A 1。
- 时间：2026-08-23 22:27:16–2026-08-24 02:01:13，artifact 区间 **3h33m57s**。
- 进度：3 seed 均完成静态 evaluation 但均为 0；之后 attempt 1 和 2 分别选择 vector 与 llm parent，均未产生 child。
- 根因：两次 reflector response 到 16,384 token 仍为空且 `finish_reason=length`，抛出 `EmptyModelResponseError`。
- controller 留在 `phase=search`、`attempt=3`；无 `best_program.py` / `best_eval_report.json`。
- 证据：`scores.jsonl`、`lineage.jsonl`、`search_controller_checkpoint.json`、`traces/iter_{001,002}_rotating.jsonl`。

### 4.5 `formal_qual_mincheck_after_qwen_coco_fix`

- 状态：**完整**。
- 配置：QUAL；3 successful child budget；static/rotating/A = 1/1/1；每 lineage target 1，max attempts 3。
- 时间：2026-08-24 11:04:45–15:13:29，artifact 区间 **4h08m44s**。
- 三 seed static 均 0.863636。
- successful child：iteration 1 vector = 0.863636；iteration 3 experience = 0.863636；iteration 5 llm = 0.809524。
- failed attempts：iteration 2 与 4 都是 `V4A hunk context matched 0 locations`，不消耗 successful-child budget。
- lineage fairness：attempts 1/3/1，successful completed 1/1/1，equal completed budget passed。
- 最终：`program_000_seed_vector_search`；static/selection/final 均 0.863636；quality passed，无 final error。
- 证据：`best_eval_report.json`、`lineage_budget.json`、`lineage_champions.json`、`search_controller_checkpoint.json`。

## 5. Reviewer → candidate → CoCo 流程记录

### 5.1 `/tmp/agentreview-e2e.i7PWiA`：首个 C1 失败样例

- 状态：**失败**。
- Reviewer 生成 1 条 QUAL candidate，review 3610 字符；约 20:00:20–20:04:05，**3m45.0s**。
- 该 review 的生成响应为 `finish_reason=length`、requested/completion 4096；当时非空截断响应仍被错误接受，因此 candidate 不应作为有效结果。
- CoCo 在 benchmark discovery 阶段立即失败：将 benchmark 目录当成文件，`FileNotFoundError: .../CoCoReviewBench/benchmark`。
- 没有 split/classify/evaluation artifact。
- 证据：`reviewer.log`、`candidate_reviews.jsonl`、`coco.log`。

### 5.2 `/tmp/agentreview-tiny.aca4GX`：C1，benchmark 相对路径修复前后

- 状态：**完整（恢复后）**。
- 1 条 candidate，review 570 字符。
- 首次 CoCo：Split 4 requests、Classify 1 task/0 retry 均完成；official evaluation 因绝对 benchmark glob 抛 `NotImplementedError: Non-relative patterns are unsupported`。
- 同目录后来生成 `evaluation.json`，证明在修复/重跑 official evaluation 后完成；后补过程没有独立日志，因此不能从 `coco.log` 得到其精确命令和墙钟。
- 指标：Average 3.25；Clarity 4.0；Correctness 3.0；Thoroughness 2.0；Grounding 4.0；Verifiability 无条目；Complete 6.6667。
- 全目录从创建到 evaluation 约 **19m19.4s**，包含数据准备、失败、人工修改/等待和后补 evaluation，不是纯模型耗时。
- 证据：`coco.log`、`coco_eval/{candidate_split,candidate_classify,evaluation}.jsonl/json`。

### 5.3 `/tmp/agentreview-full-e2e.KeVR04`：tiny MSTAR 到 C1 CoCo

- 状态：**完整（恢复后）**。
- 数据：A 1 条、validation 2 条。
- MSTAR：1 iteration、static/rotating/A=1/1/1；运行 **23m18.8s**。三个 seed static 分别 0.717949 / 0.75 / 0.75；vector child 0.75；最终选择 llm seed，selection mean 0.780488；final static 0.684211。
- MSTAR observation artifact 有 7 行 candidate review；独立 Reviewer Core 随后输出 C1 candidate 1 行、5304 字符，Reviewer **3m25.2s**。
- 首次 CoCo：Split 4 requests、Classify 23 base tasks + 2 retry，随后 official evaluation 因绝对 benchmark path 失败。
- 同目录后补的 `evaluation.json` 证明 official evaluation 最终完成；`official_reviewer_selection.json` 在 21:25:52 更新，evaluation 在 21:28:31 生成，后补 judge 区间约 **2m39.5s**，但无独立 rerun log。
- CoCo 指标：Average 3.53；Clarity 4.6；Correctness 3.8；Thoroughness 2.8；Grounding 3.6；Verifiability 2.3333；Complete 46.6667。
- 从目录创建至 evaluation 总区间 **58m05.3s**，含阶段间空档与失败恢复。
- 证据：`mstar.log`、`mstar_qual/best_eval_report.json`、`reviewer.log`、`coco.log`、`coco_eval/evaluation.json`。

### 5.4 `/tmp/agentreview-c3-e2e.ndXcIy`：C3 两次恢复

- 状态：**完整（恢复后）**。
- Reviewer attempt 1：**5.90s**，因缺少 `OPENAI_API_KEY` 失败。
- Reviewer attempt 2：3/3 candidates 完整，review 字符数 5035 / 5584 / 4616；**12m06.98s**。其中一次 empty response 从 8192 提升到 16384 后恢复。
- CoCo attempt 1：**14m31.95s** 后 Split 子进程被 SIGTERM；无成功输出。
- CoCo attempt 2（`NO_PROXY` 后）：12 Split requests；68 base Classify tasks + 6 retry（日志 `Retry count: 74` 是 total attempts）；15 judge tasks；**11m13.76s** 完成。
- 指标：Average 3.269444；Clarity 4.611111；Correctness 3.388889；Thoroughness 2.055556；Grounding 3.722222；Verifiability 2.388889；Complete 55.740741。
- 从第一次 Reviewer attempt 到成功 evaluation 的总区间约 **38m57s**，包含失败和切换间隔。
- 证据：`reviewer_attempt1.{log,time}`、`reviewer.{log,time}`、`coco.{log,time}`、`coco_noproxy.{log,time}`、`coco_eval_noproxy/evaluation.json`。

### 5.5 `/tmp/agentreview-c10-e2e.5PRPVo`：C10 截断缺陷暴露轮

- 状态：**管线完整，但实验结果无效**。
- Reviewer：10 行 candidate；墙钟由文件时间为 **27m39.95s**。
- `paper_id=4t9q35BxGr` 的 Memory Response 非空但 `finish_reason=length`、completion 8192，只有 2191 字符，被旧 backend 当成功写入；其余 review 为 4464–5392 字符。
- CoCo：40 Split requests；206 base Classify tasks，第一轮需恢复 18 个，实际 attempts 224；41 judge tasks；总 **48m59.91s**。
- 指标：Average 3.1505；Clarity 4.545；Correctness 2.931667；Thoroughness 1.915；Grounding 3.538333；Verifiability 2.763333；Complete 52.544678。
- 指标文件存在不代表候选有效：该轮必须仅用于回归/性能对照，不能作为正式 C 结果。
- 证据：`reviewer.log`、`candidate_reviews.jsonl`、`coco.log`、`coco_eval/evaluation.json`。

### 5.6 `/tmp/agentreview-c10-e2e-fixed-20260825`：截断修复后的 clean C10

- 状态：**完整、有效的 clean E2E smoke**。
- Reviewer：10/10 candidates；review 长度 3239–5579 字符；全部最终 `finish_reason=stop`；**26m35.729s**。
- CoCo Split：40 requests；约 **6m45.98s**。
- CoCo Classify：215 base tasks + 20 retry，即 235 total attempts；约 **20m57.09s**。
- Judge/evaluation：43 tasks；约 **27m13.28s**。
- CoCo 总墙钟：**54m56.973s**；Reviewer→CoCo 合计 **1h21m32.702s**。
- 指标：Average 3.161167；Clarity 4.476667；Correctness 2.96；Thoroughness 1.873333；Grounding 3.791667；Verifiability 2.591667；Complete 53.051282。
- 证据：`reviewer.log` 中的 `real`、`coco.log` 中的 `real`、`candidate_reviews.jsonl`、`coco_eval/evaluation.json`。

## 6. 当前正式 QUAL 动态快照

- 运行目录：`runs/formal_qual_seed0_20260825`
- 主日志：`logs/formal_qual_seed0_20260825.log`
- 快照：**2026-08-26 11:05:11 CST**

### 6.1 配置与进程

- 状态：**进行中**；tmux `formal_qual_B` 的 pane `dead=0`，`memory_evolution.cli evolve-task` PID 3646381 存活。
- 正式配置：QUAL；iterations 21；static 25；rotating 5；temperature 0.15；selection repeats/top-k 3/3；max fix attempts 3；parent softmax；mutation full；episode budget 125；sandbox 8192 MiB。
- Reviewer/Reflector/Judge：本地 `qwen3.8-27b`。
- Embedding：`Qwen/Qwen3-Embedding-8B`；formal cache `cache/qwen3_embedding_8b_cache.sqlite3`；runtime cache `cache/qwen3_embedding_runtime.sqlite3`。

### 6.2 当前进度

- controller：`phase=seeds`、`seed_index=1`、`attempt=0`；因此 **尚未进入 mutation/evolution child 阶段**。
- seed0 `program_000_seed_vector_search`：A 125/125、B static 25/25 完成；static score **0.8572484212623305**；quality passed、failed cases 0。
- seed1 `program_001_seed_llm_summarizer`：A checkpoint `next_paper_index=64`、completed 64/125；B static 尚未开始。
- seed2 `program_002_seed_experience_learner`：尚未开始。
- `candidate_reviews.jsonl`：26 行、25 个唯一 `(paper_id, task_id)`；`0ypGZvm0er0::QUAL` 重复一次，来自失败后 resume 的 observation。当前 search state 只有 seed0 的 1 条 score/lineage 记录。

### 6.3 可证实时间线

| 区间 | 事件 | 耗时 |
|---|---|---:|
| 08-25 11:56:21–11:58:28 | preflight、选择与 embedding closure smoke | 2m07s |
| 08-25 11:58:28–21:03:14 | seed0 A 125 条完成（中间含一次失败/resume） | 9h04m46s |
| 08-25 21:03:14–22:07:49 | 首个 B case、MemoryError、修复与最终 resume | 1h04m35s |
| 08-25 22:07:49–08-26 04:35:00 | seed0 B static 25 条完成并出分 | 6h27m11s |
| 08-26 04:35:00–10:57:01 | seed1 A 到 checkpoint 64/125 | 6h22m01s，仍在继续 |
| 08-25 11:56:34–08-26 11:05:11 | 全运行快照区间 | 23h08m37s |

### 6.4 已发生但已恢复的错误

- 同一累积日志中有两次 `MemoryError` traceback：一次在早期 historical replay（之后从已完成 A=13 resume），一次在完整历史 snapshot 的 `session.restore()`；最终以 `runtime_resources.json` 的 `sandbox_memory_mb=8192` 恢复。
- 截至快照，累积日志包含 723 API START、716 DONE、45 retry、5 FAILED；其中 24 次 empty-response retry、5 次在 16K 仍为空的 Harness fail、21 次 truncated-response retry。统计跨越多次 resume，不能直接当成唯一请求数。
- seed1 的 Harness failure 由 seed 自身回退逻辑吸收，进程仍推进；当前没有 fatal traceback 发生在最后一次 resume 之后。
- 当前累计日志中的 CoCo Classify 扇出很大；这解释 B static 墙钟，但不代表 evolution 已经开始。

## 7. 无法确认与证据缺口

1. `runs/formal_temporal_v2` 是空目录，没有可记录的运行结果。
2. 早期 `pilot_qual` / `pilot_suite` 没有独立日志，且当前 mtime 被 checkout 统一；其原始日期和耗时不可恢复。
3. `fixcheck3` / `minfix` 缺少 config、runtime 与日志，只能确认现存 score/trace，不能确认独立命令和墙钟。
4. `local_qwen_bf16_qual_smoke_seed0` 的 llm/experience seed 为什么 completed evaluation 仍为 0，现有 artifact 不足以区分 judge、CoCo、parser 或空 cases；不能仅凭 0 分指定根因。
5. tiny C1 与 full C1 的 `coco.log` 记录的是 absolute benchmark path 失败，但稍后的 `evaluation.json` 证明有后补 rerun；没有单独保存 rerun 命令/完整日志，因此只把最终结果记为 artifact-confirmed。
6. C10 截断缺陷轮虽然有完整 `evaluation.json`，candidate 本身无效，不能用于正式比较。
7. 当前 formal QUAL 仍在写日志和 checkpoint；本节只代表 2026-08-26 11:05:11 的动态快照。
8. 仓库没有保存本轮所述 `unittest discover` 的完整输出日志；因此本文不把聊天中出现过的 pass 数当作日志审计结论。

## 8. `runs/` 二次核验：结果口径与日志对照

### 8.1 先确认用户记忆中的“CoCo 50 多”

核验结论：**确实存在 50 多的 CoCo 结果，但它是官方 CoCo 输出中的 `Complete.` 百分比，不是 MSTAR 的 fitness，也不是 CoCo `Average` 1–5 分。** 这些官方 CoCo 产物在 `/tmp/agentreview-*` 的 C holdout smoke 目录中，不在 `AgentReview/runs/` 中。

| C smoke | QUAL 篇数 | CoCo `Average` | CoCo `Complete.` | 有效性 | 证据 |
|---|---:|---:|---:|---|---|
| `agentreview-e2e.i7PWiA` | 1 | — | — | **失败**；截断 candidate，且 CoCo 在 benchmark discovery 失败，无 `evaluation.json` | `/tmp/agentreview-e2e.i7PWiA/{reviewer,coco}.log` |
| `agentreview-tiny.aca4GX` | 1 | 3.250000 | 6.666667% | 完整（修复 benchmark path 后） | `/tmp/agentreview-tiny.aca4GX/coco_eval/evaluation.json` |
| `agentreview-full-e2e.KeVR04` | 1 | 3.530000 | 46.666667% | 完整（修复 benchmark path 后） | `/tmp/agentreview-full-e2e.KeVR04/coco_eval/evaluation.json` |
| `agentreview-c3-e2e.ndXcIy` | 3 | 3.269444 | **55.740741%** | 完整（恢复后） | `/tmp/agentreview-c3-e2e.ndXcIy/coco_eval_noproxy/evaluation.json` |
| `agentreview-c10-e2e.5PRPVo` | 10 | 3.150500 | **52.544678%** | **无效**；1 篇非空 `finish_reason=length` 截断 review 被写入 | `/tmp/agentreview-c10-e2e.5PRPVo/coco_eval/evaluation.json` |
| `agentreview-c10-e2e-fixed-20260825` | 10 | 3.161167 | **53.051282%** | **完整、clean，可作为当前有效 smoke 对照** | `/tmp/agentreview-c10-e2e-fixed-20260825/coco_eval/evaluation.json` |

日志对照：C3 的 `coco_noproxy.log` 显示 `Average=3.27` / `Complete.=55.74`；旧 C10 的 `coco.log` 显示 `3.15` / `52.54`；clean C10 显示 `3.16` / `53.05`，与各自 `evaluation.json` 一致。

这 3/10 篇 C 的 candidate 都是 `task_id=QUAL`，使用了同一个 tiny MSTAR 产生的 `mstar_qual/best_program.py`，SHA-256 为 `0f120fcd98f0d99bcab6709b08f5165b9194646ea46352b2a5ddfaafb505652f`。该 tiny MSTAR 本身的结果是 `static_score=0.75`、`selection_mean=0.7804878`、`final_static=0.6842105`；后续 CoCo C smoke 的 `Average` / `Complete.` 是另一阶段、另一口径的结果。

### 8.2 四类分数不能混用

| 字段 | 范围 | 用途 | 本文中的例子 |
|---|---|---|---|
| MSTAR `static_score` / `rotating_score` | 0–1 | B 上的 evolution fitness；由分类覆盖与 judge quality 组合 | 正式 run 当前 seed0 `static_score=0.857248` |
| MSTAR `selection_mean` | 0–1 | 对候选 program 进行重复 selection 后的均值 | formal mincheck `0.863636` |
| MSTAR `final_static_evaluation.mean_score` | 0–1 | promoted program 在 static validation 上的最终复评 | 完整 QUAL pilot `0.979167` |
| 官方 CoCo `Average` | 1–5 | `evaluation.json` 中的 judge 质量平均分 | clean C10 `3.161167` |
| 官方 CoCo `Complete.` | 0–100% | `evaluation.json` 中的类别覆盖完整度 | clean C10 `53.051282%` |

`memory_evolution/metrics.py` 中的 `CoCoReviewFitness` 明确说明它是 **inspired by CoCoReviewBench** 的 MSTAR task fitness；官方 standalone CoCo 结果则由 `agentreview.coco_eval` 产生 `evaluation.json`。因此把 `0.857248` 读成 85.72 CoCo，或把 `Complete.=53.05%` 读成 53.05 的质量得分，都会改变指标语义。

### 8.3 `runs/` 每个顶层目录的产物与配对日志

对 `/data/xyj/shengao/AgentReview_0811/AgentReview/runs` 的 17 个顶层目录逐一检查后，**没有任何一个目录包含官方 standalone CoCo `evaluation.json`、`candidate_split.jsonl` 或 `candidate_classify.jsonl`**。下表的分数因此都是 MSTAR 产物口径，除非明确标注为“准备检查”。

| `runs/` 顶层目录 | 产物状态 | 可核验结果 | 配对日志/主证据 | standalone CoCo |
|---|---|---|---|---|
| `formal_coco_v2_20260819` | 完整的 formal readiness | A/B/C/D=2391/299/300/899；5 specialist gate passed | run 内 `00_formal_readiness_check.log` / `00_formal_readiness_register.log` | 无；没有评分运行 |
| `formal_qual_embedding_runtime_mincheck` | 未完成 | 3 seed static 均 0；2 次 child 尝试未产生 program | run 内 `scores.jsonl`、`search_controller_checkpoint.json`、`traces/iter_*` | 无 |
| `formal_qual_mincheck_after_qwen_coco_fix` | **完整** | QUAL selected vector seed；static / selection / final 均 **0.863636** | run 内 `best_eval_report.json`、`scores.jsonl`、`lineage_budget.json` | 无 |
| `formal_qual_seed0_20260825` | **进行中** | seed0 B static **0.857248**；不是 final selection，当前无 `best_eval_report.json` | `logs/formal_qual_seed0_20260825.log`、run 内 `scores.jsonl`、controller/checkpoint | 无 |
| `formal_temporal_v2` | 空目录 | 无结果 | 无 | 无 |
| `local_qwen_bf16_qual_smoke_seed0` | 未完成 | 3 seed 均 0；无 child / best report | run 内 `config.json`、`scores.jsonl` | 无 |
| `local_qwen_bf16_qual_smoke_seed0_fixcheck3` | 部分诊断 | vector seed static 0；无 config / best report | run 内 `scores.jsonl`、`traces/seed_000_static.jsonl` | 无 |
| `local_qwen_bf16_qual_smoke_seed0_minfix` | 部分诊断 | vector + llm seed 均 0；无 best report | run 内 `scores.jsonl`、`traces/seed_*` | 无 |
| `local_qwen_qual_smoke_seed0_failed_16k_20260823_134735` | 未完成 | vector quality fail；llm seed completed 但 static 0；无 final | run 内 `config.json`、`scores.jsonl` | 无 |
| `pilot_full_20260729` | 部分 | 3 seed + 2 child 有 score；已有记录最高 static **0.968963**；无 final | 根目录 `pilot_full_20260729.log`；run 内 `QUAL/seed_0/scores.jsonl` | 无 |
| `pilot_full_iter5` | 部分 | 仅 vector seed **0.958333** | 根目录 `pilot_full_iter5.log`；run 内 score | 无 |
| `pilot_full_iter5_mix` | 失败/部分 | 仅 vector seed **0.968963** | 根目录 `pilot_full_iter5_mix.log`；run 内 score | 无 |
| `pilot_full_iter5_202607300034` | 部分 | vector / llm seed 均 **0.968963** | 同名根目录 log；run 内 score | 无 |
| `pilot_full_iter5_202607300043` | 失败/部分 | 仅 vector seed **0.968963** | 同名根目录 log；run 内 score | 无 |
| `pilot_full_iter5_202607300117` | **完整六 task pilot** | QUAL selected child；static **0.989796** / selection **0.974277** / final **0.979167**；其余 task 见 3.5 | 根目录同名 log 有 `[PILOT] Complete`；各 task `best_eval_report.json` | 无 |
| `pilot_qual` | **完整 smoke** | QUAL selected child；static **0.968963** / selection **0.935688** / final **0.947252** | 无独立 log；`best_eval_report.json` + `scores.jsonl` | 无 |
| `pilot_suite` | 部分 suite | QUAL final **0.923611**；CLAR final **0.946318**；SIGN final **0.957881**；ORIG 仅 seed0 **0.957881** | 无独立 log；各 task `best_eval_report.json` / `scores.jsonl` | 无 |

### 8.4 QUAL 完整结果的精确口径对照

| QUAL run | selected program | selected 原始 static | selection mean | final static | test set used |
|---|---|---:|---:|---:|---|
| `pilot_qual` | `program_003_child` | 0.968963 | 0.935688 | 0.947252 | `false` |
| `pilot_suite/QUAL/seed_0` | `program_000_seed_vector` | 0.957881 | 0.912530 | 0.923611 | `false` |
| `pilot_full_iter5_202607300117/QUAL/seed_0` | `program_003_child` | **0.989796** | 0.974277 | **0.979167** | `false` |
| `formal_qual_mincheck_after_qwen_coco_fix` | `program_000_seed_vector_search` | 0.863636 | 0.863636 | 0.863636 | `false` |

上表只列存在 `best_eval_report.json` 的 QUAL run。`test_set_used=false` 说明这些是 evolution/selection 侧评估，不是官方 C/D standalone CoCo final benchmark。当前 `formal_qual_seed0_20260825` 仅完成 seed0 static，不能追加到“QUAL 完整结果”表中。

## 9. 主要证据索引

- 早期完整六任务：`AgentReview/pilot_full_iter5_202607300117.log`、`AgentReview/runs/pilot_full_iter5_202607300117/`
- 早期中断轮：`AgentReview/pilot_full*.log`、对应 `AgentReview/runs/pilot_full*`
- Aug23 失败/mincheck：`AgentReview/runs/local_qwen*`、`AgentReview/runs/formal_qual_embedding_runtime_mincheck/`
- Aug24 修复后 mincheck：`AgentReview/runs/formal_qual_mincheck_after_qwen_coco_fix/`
- C1/C3/C10：`/tmp/agentreview-*`
- 当前正式运行：`AgentReview/logs/formal_qual_seed0_20260825.log`、`AgentReview/runs/formal_qual_seed0_20260825/`
