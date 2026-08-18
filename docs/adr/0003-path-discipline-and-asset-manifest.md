# 路径纪律与 Asset Manifest

本仓库以日期快照目录（`AgentReview_0722`、`AgentReview_0811`……）的方式演进，已有产物（`effectiveness_screening_v1.json`、`suite_manifest.json`、各 `config.json`）可能内嵌旧快照绝对路径，混用后实验结果将无法解释。为此决定：项目内部文件一律从当前 project root 相对解析；OpenReview / CoCoReviewBench 等外部数据根通过 CLI、环境变量或 Asset Manifest external-root resolver 显式传入；建立 Asset Manifest 记录每项资产最终解析后的路径、SHA256、版本和数据 split。任何资产不得从目录名推断。

## Considered Options

- **硬编码绝对路径**：被否决。快照复制后路径静默失效且不会报错（screening 配置指向 `_0722` 即是实例）；实验读错资产而不自知。
- **仅用环境变量/CLI 参数**：被否决。能解路径，但没有 provenance——无法记录"这次实验到底用了哪个版本的语料/程序/论文集"，结果不可复核。
- **相对路径 + 统一外部根配置 + Asset Manifest（SHA256 校验）**：被采纳。快照间可移植，资产身份由内容哈希锚定而非由位置暗示。

## Consequences

- 活跃配置与 README 示例已移除 `AgentReview_0722` 绝对路径；旧 run artifact 内的 recorded path 保持 immutable，仅由 resolver 显式映射。
- Manifest 是实验资产的唯一权威来源：跑实验前解析并记录，跑完后产物目录里必须能回答"用了哪个语料/哪个 program 的哪个哈希/哪个 split"。
- 评审口径统一：同一资产换版本（如重跑进化后的新 best_program.py）= 新 manifest 条目，而不是原地覆盖。
- **历史 run artifact 不原地修改**：如 `runs/pilot_full_iter5_202607300117` 内嵌 `_0722` 旧绝对路径的 config / manifest 保持原样（它们是历史 provenance）。正确方式：新 Asset Manifest / resolver 记录 legacy recorded path → 解析到当前真实 asset，同时记录 SHA256 与 provenance relation。
