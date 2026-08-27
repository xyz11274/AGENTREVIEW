# MSTAR 原论文的进化轮数与方法（2026-08-27）

## 1. 来源与结论

本文只核对本地原论文：[M★: Every Task Deserves Its Own Memory Harness](/data/xyj/shengao/2604.11811v2_Mstar.pdf)。

- 文件：`/data/xyj/shengao/2604.11811v2_Mstar.pdf`
- SHA-256：`e02aa0f498751dd29f93de2af29eb125d7c00619878aa2f91beb97511de0ffe0`
- 版本：用户提供的 `2604.11811v2_Mstar.pdf`，共 36 个 PDF 页面。

**核心结论：原论文主实验对每个 benchmark configuration 运行 20 次 evolution iterations。** 正文 Section 4 明确写道 “run 20 evolution iterations”；Appendix D.2 Table 7 也把 `Evolution iterations` 列为 `20`（PDF pp. 5, 17）。

这 20 次不是 20 个初始 seed，也不是必须成功产出并完成评估的 20 个 child：

- population 先由 **3 个固定结构 seed programs** 初始化并分别评估；这 3 次 seed 初始化在 20 次演化循环之前（Appendix A, Algorithm 1, lines 1–5；Appendix D.2 Table 7；Appendix E，PDF pp. 14, 17–18）。
- 随后算法执行 `for t = 1 to B`，主实验中 `B=20`。每次循环选择一个 parent，尝试产生至多一个 child（Appendix A, Algorithm 1, lines 6–16，PDF p. 14）。
- 如果 child 在最多 3 次 compile-fix 后仍失败，则被丢弃，但当前循环仍结束并进入下一个 `t`。Figure 3 也把 compile failures 以叉号画在 “across 20 evolution iterations” 的轨迹中（正文 Figure 3 caption，PDF p. 6）。因此论文的 20 是**预算化的 mutation/iteration 槽位**，不是“必须凑满 20 个成功 child evaluation”。
- 每次迭代只增量产生一个 child，并把有效 child 加入累计 pool；它不是传统遗传算法中整个人口同时更新的一代。因此把它称为 **20 次顺序迭代** 比“20 个 generations”更准确。

理论上，一次主实验最多得到 `3 个初始 seed + 20 个有效 child = 23 个有效 pool programs`；若有最终未修复的 compile/runtime failure，有效 child 和 pool 总数会更少。有效 child 的 static evaluation 之外，每轮还会对 parent 执行一次 rotating-subset evaluation 来产生反思材料，因此“迭代数”也不等于 evaluator 调用数或 LLM 调用数。

## 2. 三种容易混淆的 seed / iteration

| 论文术语 | 数量 | 实际含义 |
|---|---:|---|
| Population seed programs | 3 | 同一次 run 中初始化 pool 的三个固定程序：Vector Search、LLM Summarizer、Experience Learner（Appendix E, Table 8，PDF p. 18）。 |
| Evolution iterations | 20 | 同一次 run 中执行 20 个顺序 parent-selection → reflection/mutation → check → optional child evaluation/update 槽位（Section 4；Appendix A；Appendix D.2，PDF pp. 5, 14, 17）。 |
| Evolution seeds / random seeds | 5 | 稳定性实验的 5 次独立重复 run，不是一个 run 内的五个 population seeds，也不是五轮 evolution；每个稳定性 run 仍处于 20-iteration budget（Section 6, Table 3；Appendix I.2, Tables 13–14，PDF pp. 8, 29）。 |

## 3. 原论文的演化方法

### 3.1 搜索对象

每个 MemoryProgram 是具有固定接口的可执行 Python module，同时控制四部分（Section 3.1，PDF p. 4）：

1. **Schema**：`KnowledgeItem`、query 等 dataclass 数据结构。
2. **Logic**：knowledge base 的 `write()` / `read()` 存储、索引、检索和可选 LLM 处理逻辑。
3. **Instruction**：知识抽取、query 构造、response 使用方式等固定 prompt constants。
4. **Toolkit**：白名单数据结构、SQLite/向量库、embedding 和受限 LLM endpoint 等。

所以 mutation 不是只改 prompt，也不是只改检索参数；它可以联合修改 Schema、Logic 和 Instruction。正文 Table 2 的 ablation 也把 code 与 instruction 的联合演化作为完整方法（PDF pp. 7–8）。

### 3.2 初始化 population

Algorithm 1 先对三个 seed `S1...SK` 使用 episodes `De` 和 static validation set `Dv` 完整评估，并把 `(program, static score)` 放入 pool `P`（Appendix A, Algorithm 1, lines 1–5，PDF p. 14）。三个 seed 的结构是（Appendix E, Table 8，PDF p. 18）：

- **Vector Search**：ChromaDB chunks + top-k cosine retrieval；read 时不用 LLM。
- **LLM Summarizer**：内存列表 + 全文拼接后让 LLM 做 query-focused summary；read 时一次 LLM。
- **Experience Learner**：分别存 lesson/fact，检索时全部返回；read 时不用 LLM。

### 3.3 每次 evolution iteration

对 `t = 1...20`，步骤如下（Section 3.2–3.3；Appendix A, Algorithm 1，PDF pp. 4–5, 14）：

1. **Softmax 选择 parent**：从整个累计 pool 按 static validation score 加权采样，不是永远选择当前最优程序：

   `Pr(P_i) = exp(J(P_i)/τ) / Σ_j exp(J(P_j)/τ)`

   主实验 `τ=0.15`（Section 3.3 Eq. 3；Appendix D.2 Table 7，PDF pp. 5, 17）。论文算法没有规定三个初始 lineage 各自必须得到相同数量的成功 child。

2. **构造 rotating feedback**：从 rotating validation pool 选当轮 subset；主实验是每轮 5 items。static subset 固定不变，rotating subset 每轮通过改变 k-means seed 重新选择（Section 3.2；Appendix D.2–D.3，PDF pp. 4, 17–18）。

3. **在 rotating subset 上评估 parent**：重新写入 episodes，执行 retrieval/response，并将样本按 threshold 分成失败案例 `F` 和成功案例 `S`。rotating trajectories 供改进使用，不承担最终 pool fitness；fitness 来自 static validation score（Section 3.2；Algorithm 1(b)，PDF pp. 4, 14）。

4. **Reflect and mutate**：coding-agent/reflector 接收当前程序、当前 score、write/read execution trajectories、rotating set 中的低分案例、祖先 change logs 及其 score，并诊断根因、生成 V4A code patch 和新的 change log（Section 3.2；Appendix G.2，PDF pp. 4, 24–26）。Appendix G.2 还说明每轮从 rotating set 中按 `1-score` 加权抽取 2 个失败案例；lineage log、write examples、success cases、reference programs 和 debug logs 是有数据时才加入的可选上下文（PDF p. 26）。

5. **Constraint check / compile-fix**：对 patched program 做 compile 和 runtime quality checks。失败时把错误和程序重新交给 reflector 修复，最多 `k=3` 次；仍失败则丢弃本次 mutation（Section 3.2；Algorithm 1 line 11；Appendix D.2；Appendix G.3，PDF pp. 4, 14, 17, 26）。

6. **评估有效 child 并更新 pool**：只有通过检查的 child 才重新 ingest episodes，在固定 static subset 上计算 `J'`，然后把 `(child, J')` 加入 pool；pool 不要求 child 必须优于 parent，也没有在算法伪代码中淘汰低分 child（Algorithm 1 lines 12–15，PDF p. 14）。因此有效但退步的 child 仍会进入 pool，后续只是被 softmax 选中的概率更低。

### 3.4 结束与最终选择

20 次循环结束后，从累计 pool 中选择 static validation score 最高的 program：`arg max J`；再在 held-out test split 上做最终报告（Algorithm 1 line 17；Section 4，PDF pp. 14, 5）。held-out test 不参与 evolution。

## 4. 论文主实验的关键超参数

Appendix D.2 Table 7（PDF p. 17）给出的共享设置是：

| 参数 | 原论文值 |
|---|---:|
| Evolution iterations | 20 |
| Population seed programs | 3 |
| Parent selection | Softmax, `τ=0.15` |
| Static validation subset | 32–60 items，固定 k-means representative subset |
| Rotating validation subset | 5 items / iteration |
| Train-to-validation ratio | 2 |
| Subset-selection embedding | BGE-M3 |
| Reflector | GPT-5.3-Codex, reasoning medium |
| Task agent / toolkit model | GPT-5.4-mini |
| Toolkit LLM budget | 每次 `read()` / `write()` 最多 1 call |
| Compile-fix attempts | 3 |
| Evaluator workers | 64 |
| LLM retry attempts | 3 |

## 5. 对“成功 iteration”口径的直接判断

如果把当前系统的 `21 successful evolution iterations` 理解成原论文的等价设置，**并不准确**：

- 原论文：`B=20` 个 evolution loop 槽位；compile/runtime mutation 最终失败也占用一个 `t`，但不加入 child。
- “成功 child”：仅指通过检查、完成 static evaluation 并被加入 pool 的 child；原论文没有要求其 fitness 超过 parent。
- 原论文没有“每条 lineage 必须成功 7 个 child”的 fairness quota；它从全 pool 全局 softmax sampling。
- 原论文稳定性实验的 5 个 random seeds 是 5 次独立完整 run，不是把 20 次迭代扩成 5×20 个同一 run 内的 child。

最后一个区别也可由 Appendix D.1 Table 6（PDF p. 17）交叉验证：各配置均列 `Iter.=20`，但 reflector 调用数为 24–48，说明 compile-fix/retry 会让 LLM 调用数超过 evolution iteration 数；调用次数不能反推为额外 evolution rounds。
