# ADR-0003：项目相对路径、显式外部根与 Asset Manifest

- 状态：Accepted
- 适用范围：配置、冻结数据、Program、模型资产与运行产物

## Context

仓库会在不同工作区、机器和归档位置运行。路径只表达位置，实验身份需要由内容、版本和数据分区共同确定。OpenReview、CoCoReviewBench 和模型缓存属于外部资产；Frozen Reviewer Identity、split、Frozen S2 和 Seed Program 属于项目资产。

## Decision

1. 项目内文件使用 AgentReview 项目根相对路径。
2. 外部数据根通过 CLI、环境变量或 manifest 的 external_roots 显式配置。
3. Asset Manifest 记录每个正式资产的相对路径、版本、用途分区和 SHA256。
4. 模型资产额外记录 provider、model ID、immutable revision、维度、序列化规则与 fingerprint。
5. 运行开始时 resolver 解析实际路径，formal gate 校验内容身份。
6. 运行产物记录已解析资产、Program SHA、数据 manifest SHA 和配置身份。
7. 生成目录使用运行 ID 隔离；正式归档保存完整 manifest 与 hash。
8. Promotion writer 将套件树内的 `program` 与 `source_run` 写成 manifest 所在目录的相对路径。resolver 以 `manifest.parent` 解析相对路径；显式外部路径使用绝对路径。
9. Promotion consumer 读取 `<source_run>/config.json` 和 `selection.json`，校验 embedding provenance 与冻结历史选择。

## Rationale

相对路径提供工作区可移植性。显式 external root 提供外部数据定位。内容 hash 和版本提供可复核身份。三者组合让同一实验在新路径中保持同一语义，并让资产变化形成新的明确身份。

## Consequences

- configs/asset_manifest_v2.json 是 pre-evolution 正式资产索引。
- configs/frozen_reviewer_identity_v2.json 是六任务 prompt 与 suite identity 的语义 manifest。
- configs/formal_temporal_v3_qwen3e8b.json 记录正式 embedding 实验和 selection 资产状态。
- temporal split、Frozen S2、Reviewer Identity 与 Seed Program 各自拥有独立版本和 SHA。
- Program promotion 通过 suite_manifest.json 与 meta_manifest.json 绑定相对来源 run、源码 SHA、selection 和 source-run config。
- runs/、outputs*/ 和 cache 作为可再生位置；外部归档承担正式运行留存。
- 历史 provenance 字段保留原始来源信息，并由当前 resolver 解释位置。
