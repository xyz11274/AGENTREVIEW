# MemoryProgram effectiveness screening

This experiment is a **CoCo-style proxy screening**, not a reproduction of the
official CoCoReviewBench benchmark. `Correctness_proxy` and
`CoCo_Average_proxy` use `train_clean.jsonl` category references, which do not
contain CoCoReviewBench's released `opinion_groups`, `conflicts_validation`, or
`rebuttal_validation` correctness annotations.

The frozen design is:

- 20 effectiveness papers;
- 20 separate frozen-memory-corpus papers;
- `no_memory`, `seed_experience`, and `evolved`;
- two unconditional replicates;
- 120 complete AgentReview outputs;
- no target writeback;
- the five specialist reviews form the primary review package;
- META is evaluated separately;
- POL is byte-identical between seed and evolved and is not interpreted as an
  evolution gain.

The single source of configuration is
`configs/effectiveness_screening_v1.json`.

At any point, inspect readiness without printing credential values:

```bash
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m effectiveness_screening.preflight \
  --config configs/effectiveness_screening_v1.json
```

## Current frozen data

`data/effectiveness_screening_v1/selection_audit.json` records the source
checksums, sampling seeds, selected-set checksums, and leakage audit.

The five required family overlaps are all zero:

- effectiveness versus official test;
- memory corpus versus official test;
- effectiveness versus MSTAR-seen papers;
- memory corpus versus MSTAR-seen papers;
- effectiveness versus memory corpus.

## Preparation and execution order

Run commands from the AgentReview repository root with:

```bash
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python
CFG=configs/effectiveness_screening_v1.json
```

The paper IDs have already been sampled. Do not rerun `select` after the
experiment is preregistered.

Freeze program snapshots:

```bash
$PY -m effectiveness_screening.memory_cache freeze-programs --config "$CFG"
```

Lock the selected IDs, proxy references, programs, metrics, thresholds, config,
CoCo commit, official judge prompt, and implementation hashes before any
generation:

```bash
$PY -m effectiveness_screening.run_matrix freeze-design --config "$CFG"
```

Freeze related work once. Failed HTTP responses are rejected and are never
accepted as empty retrievals:

```bash
export S2_API_KEY=...
$PY -m effectiveness_screening.prepare retrieve-related --config "$CFG"
$PY -m effectiveness_screening.prepare build-bundles --config "$CFG"
```

Print and run the one-time `no_memory` history-generation command:

```bash
$PY -m effectiveness_screening.run_matrix history-command --config "$CFG"
```

After all 20 historical reviews complete, freeze the shared raw library:

```bash
$PY -m effectiveness_screening.memory_cache freeze-memory-library \
  --config "$CFG" \
  --source-output-dir data/effectiveness_screening_v1/history_generation_no_memory
```

Compile the two program-specific KnowledgeItem caches offline:

```bash
$PY -m effectiveness_screening.memory_cache compile-cache \
  --config "$CFG" --arm seed_experience
$PY -m effectiveness_screening.memory_cache compile-cache \
  --config "$CFG" --arm evolved
```

Write the immutable 120-cell matrix and run its six recorded batch commands:

```bash
$PY -m effectiveness_screening.run_matrix matrix --config "$CFG"
```

Every target command reads the same checksummed paper bundle, creates fresh
MemoryProgram sandboxes, loads only the relevant read-only KnowledgeItem cache,
and passes `--disable_memory_writeback`.

Audit all generated targets:

```bash
$PY -m effectiveness_screening.run_matrix audit-runs --config "$CFG"
```

## CoCo-style proxy evaluation

The official split and classify models are pinned in the config:

- `hexuandeng/ReviewSplit@e405b49e472d1c4ddbf226eae7aded032a2d5c8a`
- `hexuandeng/ReviewClassify@8607b97b893bf64be0de848609863093c7ecc095`

Download each exact revision and serve it under the official model names and
ports. Do not use `main` without the pinned revision:

```bash
hf download hexuandeng/ReviewSplit \
  --revision e405b49e472d1c4ddbf226eae7aded032a2d5c8a \
  --local-dir /data0/xyj/shengao/CoCoReviewBench/model/split_model

hf download hexuandeng/ReviewClassify \
  --revision 8607b97b893bf64be0de848609863093c7ecc095 \
  --local-dir /data0/xyj/shengao/CoCoReviewBench/model/classify_model
```

Start the official vLLM services on ports 13099 and 13098, then run:

```bash
$PY -m effectiveness_screening.evaluate_proxy classify --config "$CFG"
$PY -m effectiveness_screening.evaluate_proxy judge --config "$CFG"
$PY -m effectiveness_screening.evaluate_proxy build-blind-pairs --config "$CFG"
```

The primary judge loads
`CoCoReviewBench/src/evaluation/prompt/judge.txt` directly and records its SHA.
It uses `gpt-5-mini` with temperature 0. The outputs remain explicitly marked
as proxy because the human correctness references are not official enriched
CoCoReviewBench references.

Have an independent evaluator fill
`data/effectiveness_screening_v1/evaluation/blind_judge_results.jsonl` with:

```json
{"pair_id": "...", "winner": "A"}
```

`winner` must be `A`, `B`, or `tie`. The independent evaluator receives only
`blind_pairs.jsonl`, never the sealed key, arm labels, MSTAR fitness, or primary
judge results.

Finally:

```bash
$PY -m effectiveness_screening.summarize --config "$CFG"
```

The screening gate determines only whether to consider a separately frozen
official 3,900-paper CoCoReviewBench test. A failed gate requires component
diagnosis and is not a conclusion that evolution is ineffective.
