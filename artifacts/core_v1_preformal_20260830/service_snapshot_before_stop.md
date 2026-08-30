# Research Validity Core v1 — pre-stop service snapshot

- Captured: `2026-08-30T18:27:28+08:00`
- Repository HEAD: `17ecea311bfbf0ff555ac5e835d6b1da2853b0f8`
- Branch: `20260827`
- Scope: read-only snapshot taken before the user-authorized shutdown of the old Qwen and CoCo services.

## Dependency check

No live process matched the AgentReview formal/search entry points (`memory_evolution`, `core_formal`, `run_single_paper`, or a formal runner). The only matching compute processes were the three model services listed below. The preserved legacy checkpoint remains `phase=search_inflight`, but its last checkpoint commit is `2026-08-27 20:13:02 +0800`; there is no live controller using it.

- Checkpoint: `AgentReview/runs/formal_qual_seed0_20260825/search_controller_checkpoint.json`
- Checkpoint SHA256: `66b0e2852b0ef3b981dd5f0bd9ad9c04f97702881d922a7e1e47b6faa67e6a0b`
- Legacy artifact counts: `scores.jsonl=3`, `lineage.jsonl=3`, `candidate_reviews.jsonl=81`
- Decision: GPU4/5 CoCo Split/Classify have no live formal consumer and may be stopped. Existing `runs/` artifacts are not modified.

## Services

### Qwen Reviewer service — GPU0–3

- tmux: `qwen38`
- PID / process group: `2069418 / 2069418` (tmux server PID `2069417`)
- endpoint: `http://127.0.0.1:8000/v1`
- stdout/stderr: `/dev/pts/21` (tmux pane; no file-backed log)
- model: `/data/xyj/models/Qwen3.8-27B`
- served name: `qwen3.8-27b`
- TP: `4`
- max-model-len: `262144`
- max-num-seqs: `1`
- gpu-memory-utilization: `0.95`
- prefix caching: enabled
- complete launch command:

```text
CUDA_VISIBLE_DEVICES=0,1,2,3 /data/xyj/envs/qwen38serve/bin/vllm serve /data/xyj/models/Qwen3.8-27B --served-model-name qwen3.8-27b --host 127.0.0.1 --port 8000 --tensor-parallel-size 4 --max-model-len 262144 --max-num-seqs 1 --gpu-memory-utilization 0.95 --language-model-only --reasoning-parser qwen3 --enable-prefix-caching
```

Model/tokenizer identity:

- `config.json`: `191e0af232104ed8b65258cf3fb2b842e288008baca7633c11b82a1ac7203aab`
- `tokenizer.json`: `0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3`
- `tokenizer_config.json`: `b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27`
- `model.safetensors.index.json`: `77042094076611b69791a610065f28b7013b8c621795fa86ddccc8bac7d1b9df`

### Official CoCo Split — GPU4

- tmux: `coco_split`
- PID / process group: `3409632 / 3409626`
- endpoint: `http://0.0.0.0:13099/v1`
- log: `/data/xyj/shengao/CoCoReviewBench/logs/vllm_split_tmux_restart_20260825.log`
- model: `/data/xyj/shengao/CoCoReviewBench/model/split_model`
- served name: `Qwen3-8B-Split`
- TP: `1`; max-model-len: `40960`; max-num-seqs: vLLM default/not explicitly sent
- model index SHA256: `3037dbd89fb25e0b328fddbb41b60a0967da3eb20e80e1b9fe0c5b1ac902f368`
- tokenizer SHA256: `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`
- complete launch command:

```text
/data/xyj/envs/qwen38serve/bin/vllm serve /data/xyj/shengao/CoCoReviewBench/model/split_model --host 0.0.0.0 --port 13099 --served-model-name Qwen3-8B-Split --dtype bfloat16 --max-model-len 40960 --tensor-parallel-size 1 --max-log-len 10 --gpu-memory-utilization 0.95 --enforce-eager --enable-prefix-caching
```

### Official CoCo Classify — GPU5

- tmux: `coco_classify`
- PID / process group: `2084518 / 2084508`
- endpoint: `http://0.0.0.0:13098/v1`
- log: `/data/xyj/shengao/CoCoReviewBench/logs/vllm_classify_tmux.log`
- model: `/data/xyj/shengao/CoCoReviewBench/model/classify_model`
- served name: `Qwen3-8B-Classify`
- TP: `1`; max-model-len: `32768`; max-num-seqs: vLLM default/not explicitly sent
- model index SHA256: `e7e4bdc58d3302c97357f279b270987dd620cf0cd9b1c130e3a51c9d64df95`
- tokenizer SHA256: `aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4`
- complete launch command:

```text
/data/xyj/envs/qwen38serve/bin/vllm serve /data/xyj/shengao/CoCoReviewBench/model/classify_model --host 0.0.0.0 --port 13098 --served-model-name Qwen3-8B-Classify --dtype bfloat16 --max-model-len 32768 --tensor-parallel-size 1 --max-log-len 10 --gpu-memory-utilization 0.95 --enforce-eager --enable-prefix-caching
```

## GPU and topology snapshot

| GPU | UUID | Used MiB | Free MiB | Compute process |
|---:|---|---:|---:|---|
| 0 | `GPU-9162a960-3127-c9dc-dba2-3752aeee9979` | 23539 | 542 | `2071467 VLLM::Worker_TP` |
| 1 | `GPU-34ebf3a5-de8c-d9e2-a6bd-3ac4324812e9` | 23539 | 542 | `2071468 VLLM::Worker_TP` |
| 2 | `GPU-a4138573-4cc4-9995-a9fe-ca1ca63101a4` | 23541 | 540 | `2071469 VLLM::Worker_TP` |
| 3 | `GPU-716d2837-03c7-11a4-968e-1f5ce6257713` | 23539 | 542 | `2071470 VLLM::Worker_TP` |
| 4 | `GPU-f4cf67bc-7878-a71b-f141-0e684ca79245` | 23681 | 401 | `3409865 VLLM::EngineCore` |
| 5 | `GPU-0bad054c-ebcd-e65d-f8bc-1aae5fa5c174` | 23683 | 399 | `2085175 VLLM::EngineCore` |
| 6 | `GPU-de606621-7221-c4ad-6ea2-9de6ded1fd13` | 15 | 24067 | none |
| 7 | `GPU-53d6244c-1061-1c5a-7570-96d01cf23eae` | 15 | 24067 | none |

GPU0–3 are a NUMA0/PIX island (`CPU 0-35,72-107`); GPU4–7 are a NUMA1/PIX island (`CPU 36-71,108-143`).
