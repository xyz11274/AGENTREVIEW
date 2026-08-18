# AgentReview M★

This repository implements one review architecture:

```text
Paper → five independent specialist reviewers → final meta-reviewer
          QUAL  CLAR  SIGN  ORIG  POL             META
            └──── one independent M★ MemoryProgram per task ────┘
```

The five reviewer definitions and their 23 subcategories come only from
`REVIEW_Request.md`. Its SHA-256 is checked at runtime:

```text
609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42
```

M★ evolves executable memory schemas, write/read logic, and program
instructions. It does not mutate the reviewer definitions, Task Agent, evaluator,
model weights, or five-specialist-then-Meta topology.

## Install and test

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Run all commands from this directory. The tested project interpreter is:

```text
/data0/xyj/miniconda3/envs/env_shengao/bin/python
```

## Prepare specialist evolution data

The frozen CoCo temporal manifest supplies family-level target membership.
The 3900 read-only submission records are canonicalized and clustered, then one
deterministic score/review-independent representative is selected per family.
The resulting 3889 targets use A/B/C/D counts 2391/299/300/899.

```bash
python -m memory_evolution.cli prepare-specialists \
  --openreview-root "$OPENREVIEW_ROOT" \
  --coco-root "$COCOREVIEWBENCH_ROOT" \
  --output-dir data/evolution \
  --temporal-split-manifest configs/coco_temporal_split_v2/manifest.json \
  --s2-cache configs/frozen_s2_temporal_v2.jsonl
```

This creates independent `QUAL`, `CLAR`, `SIGN`, `ORIG`, and `POL` datasets.
No test records are written into the evolution directory. Formal evolution
rejects datasets prepared without a frozen, checksummed S2 cache.
It also reserves a third, family-disjoint `meta_validation` partition that the
specialist evolution never evaluates; only this partition validates META.
The cache must contain every selected paper ID plus target cutoff,
retrieval method/version/config and result SHA. Unknown-date or post-cutoff
results, legacy cache records and partial caches are rejected.

## Evolve the five specialists

A formal run uses three seed programs and 21 successful child candidate
evaluations (7 per lineage); failed mutation/guard/compile/evaluation attempts
do not consume that completed budget. Static
validation size 25, rotating feedback size 5, train-to-validation budget 5:1,
parent softmax temperature 0.15, three compile repairs, and one outer Evolution
seed (`0`) for the current minimal feasibility experiment. The three Seed
Program lineages remain independent and each receives seven successful child
evaluations.

```bash
python -m memory_evolution.cli evolve-specialists \
  --data-root data/evolution \
  --output runs/formal \
  --experiment-seeds 0 \
  --primary-seed 0 \
  --asset-manifest configs/asset_manifest_v2.json \
  --temporal-split-manifest configs/coco_temporal_split_v2/manifest.json \
  --coco-root "$COCOREVIEWBENCH_ROOT" \
  --classifier-adapter memory_evolution.coco_adapter:create_frozen_coco_adapter \
  --review-split-revision <immutable-ReviewSplit-commit> \
  --review-classify-revision <immutable-ReviewClassify-commit> \
  --embedding-adapter memory_evolution.embedding_adapter:create_bge_m3 \
  --embedding-revision <immutable-model-commit>
```

The bundled classifier adapter verifies the pinned CoCoReviewBench commit and
reuses its official split/classify prompts, cleanup rules and parsers. It expects
the repository's ReviewSplit and ReviewClassify vLLM services at their documented
ports (13099 and 13098).

Formal specialist selection refuses to run without that adapter. `--smoke`
permits a non-reportable explicit-label fallback and reduces the search.
`--pilot` permits the same non-formal classifier and deterministic embedding
fallbacks but does not reduce the requested iterations, validation sizes or
selection repeats. It is intended for a complete small-data pipeline test:

```bash
# OPENAI_API_KEY and OPENAI_API_BASE must already be exported.
./pilot_full.sh
```

The default pilot prepares 30 training, 20 specialist-validation and 6
Meta-validation work families; performs two mutations for each of the five
specialists and META; and prints a seed-versus-evolved score table. Override
settings with `PILOT_ITERATIONS`, `PILOT_STATIC_SIZE`,
`PILOT_ROTATING_SIZE`, `PILOT_RUN_ROOT` and the other variables at the top of
the script. Pilot output is explicitly non-reportable.
Formal M★ selection also requires a frozen BGE-M3 adapter and immutable model
revision; the adapter source hash and revision are recorded in every run.

Each specialist gets its own pool, scores, lineage, traces and promoted
`best_program.py`. No program or feedback is shared between dimensions.
Outer seed 0 is the current minimal feasibility run. One or more outer seeds are
accepted in formal mode; for example `0,1,2,3,4` remains available as a future
stability/robustness experiment, but those repeats are not required for the
current QUAL feasibility question. Outer seeds are distinct from the three Seed
Program lineages inside every Evolution run. `--primary-seed` must name one of
the configured outer seeds.

## Evolve Meta after specialists are frozen

Meta inputs are generated from the five promoted specialists. Training papers use
out-of-fold generation so a paper's human target is never present in the memory
used to generate its specialist outputs.

```bash
python -m memory_evolution.cli cache-meta-inputs \
  --data-root data/evolution \
  --program-root runs/formal \
  --output data/evolution/meta_inputs.jsonl \
  --embedding-adapter memory_evolution.embedding_adapter:create_bge_m3 \
  --embedding-revision <same-immutable-model-commit>

python -m memory_evolution.cli prepare-meta \
  --openreview-root "$OPENREVIEW_ROOT" \
  --specialist-outputs data/evolution/meta_inputs.jsonl \
  --output-dir data/evolution

python -m memory_evolution.cli evolve-meta \
  --data-root data/evolution \
  --output runs/formal \
  --embedding-adapter memory_evolution.embedding_adapter:create_bge_m3 \
  --embedding-revision <same-immutable-model-commit>
```

For a non-reportable end-to-end pilot, `cache-meta-inputs --pilot` permits the
same deterministic hash-embedding fallback without reducing search iterations.

## Run the complete review system

The program root must contain:

```text
runs/formal/
  QUAL/best_program.py
  CLAR/best_program.py
  SIGN/best_program.py
  ORIG/best_program.py
  POL/best_program.py
  META/best_program.py
```

```bash
python run_single_paper_review_cli.py \
  --paper_md_dir /path/to/papers \
  --output_dir outputs/reviews \
  --model_name gpt-5-mini \
  --memory_mode mstar_program \
  --memory_program_dir runs/formal \
  --memory_embedding_adapter memory_evolution.embedding_adapter:create_bge_m3 \
  --memory_embedding_revision <same-immutable-model-commit> \
  --overwrite
```

Five specialists run independently and can execute concurrently. Only the final
Meta reviewer sees all five outputs. Online runs append completed reviews to the
memory library; executable programs change only through a new offline M★ run.

## Evaluation

Specialist fitness is the harmonic mean of:

1. the mean of alignment, completeness, grounding, verifiability and clarity;
2. bounded category recall
   `|AI categories ∩ human categories| / |human categories|`.

Candidate-supplied category labels are ignored. Formal labels come from the
frozen classifier adapter. Meta fitness is the harmonic mean of adjudication,
human-reference agreement, and decision/calibration groups.

Materialize only the frozen D manifest from the CoCo source pool:

```bash
python -m memory_evolution.cli prepare-test \
  --coco-root "$COCOREVIEWBENCH_ROOT" \
  --output-dir data/sealed_test \
  --temporal-split-manifest configs/coco_temporal_split_v2/manifest.json \
  --s2-cache configs/frozen_s2_temporal_v2.jsonl
```

The formal suite command executes every test specialist exactly once, constructs
META inputs from those five frozen outputs, evaluates META once, and refuses to
overwrite its report:

```bash
python -m memory_evolution.cli final-evaluate-suite \
  --data-root data/evolution \
  --test-data-root data/sealed_test \
  --program-root runs/formal \
  --output runs/formal/final_suite_test.json \
  --asset-manifest configs/asset_manifest_v2.json \
  --temporal-split-manifest configs/coco_temporal_split_v2/manifest.json \
  --coco-root "$COCOREVIEWBENCH_ROOT" \
  --classifier-adapter memory_evolution.coco_adapter:create_frozen_coco_adapter \
  --review-split-revision <same-ReviewSplit-commit> \
  --review-classify-revision <same-ReviewClassify-commit> \
  --embedding-adapter memory_evolution.embedding_adapter:create_bge_m3 \
  --embedding-revision <same-immutable-model-commit>
```

The lower-level `evaluate` command remains available for development single-task
evaluation or ablation. `evaluate --final-test` is always rejected and cannot
produce a formal report. The only legal D/Test entry is
`final-evaluate-suite → formal gate → CoCoMetricBenchmarkAdapter`.

CoCoReviewBench is used as an opinion-taxonomy and metric-design reference. Its
benchmark construction and left-out-reviewer workflow are not part of the M★
search architecture.

## Audit and viewer

Candidates execute in isolated processes with bubblewrap, cleared environments,
resource limits, restricted imports, in-memory SQLite, and capability-based
LLM/embedding/vector access.

```bash
python viewer.py --output_dir outputs/reviews --host 127.0.0.1 --port 7861
```

See [docs/memory_evolution.md](docs/memory_evolution.md) for the exact search
contract, artifacts, ablations and known environment requirements.

The frozen post-evolution three-arm effectiveness screen is documented in
[docs/effectiveness_screening.md](docs/effectiveness_screening.md). Its
`train_clean.jsonl` evaluation is explicitly a CoCo-style proxy and does not
claim official CoCoReviewBench Correctness.

/data0/xyj/miniconda3/envs/env_shengao/bin/python
  viewer.py
  --output_dir outputs
  --host 0.0.0.0
  --port 7861

/data0/xyj/miniconda3/envs/env_shengao/bin/python
  -m evolution_viewer.app
  --runs-dir runs/pilot_full_iter5_202607300117
  --host 0.0.0.0
  --port 7862
