# ADR-0011：Research Validity Core v1 与 Audit Hardening v2 分层

- 状态：Accepted
- 日期：2026-08-30
- 最近核验：2026-08-31

## 背景

研究有效性所需的最小闭包与生产级高可用能力具有不同交付节奏。把全部恢复、镜像、归档和发布治理设为首次实验前置，会扩大首轮实现面。

## 决策

### Core v1

首次正式实验以以下能力为闭包：

- 冻结 Corpus、Projection、Fragment、Review Memory 与 Evidence；
- 三枚 v2 seed、封闭 read mode、sandbox 和原子预算；
- reference-free 两阶段 Evaluator 与 canonical fitness；
- shared-pool attempts、Program/Candidate identity、global top-3 和 role winner；
- 五 winner Final Suite 与冻结后 Protected Official Access；
- 单进程共享 Qwen capacity、必要的 fail-stop、原子工件与最小 provenance；
- G1、G2、G3 三层门禁。

### Audit Hardening v2

后续强化层包含：

- journal mirror、复杂 recovery epoch 和 infrastructure generation replacement；
- root watchdog、外部归档、外部交付和多副本审计；
- Evidence terminalization、dependency re-seal 和 reporting ingress 恢复；
- publication candidate、promotion 与长期保存治理。

## 结果

Core v1 以研究可解释性和正式工件闭包为交付目标。Hardening v2 以长时间运行韧性与发布治理为交付目标。协议索引为每项语义标记当前实现层级。

## 替代方案

单层交付模型会混合研究门禁与高可用目标。当前分层让首次正式实验拥有明确完成定义，并为后续强化保留稳定接口。
