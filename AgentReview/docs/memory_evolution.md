# M★ reproduction contract

## What is reproduced

The implementation maps each review role to one M★ task. The unchanged M★ search
core is:

- executable MemoryProgram = Schema + Logic + Instructions;
- three initial programs: Vector Search, LLM Summarizer, Experience Learner;
- a persistent population rather than a linear best-parent loop;
- parent sampling by `softmax(static_score / 0.15)`;
- one static validation subset selected by embedding k-means;
- one rotating feedback subset sampled from the remaining validation pool;
- facility-location selection of training episodes with a 5:1
  train-to-validation budget;
- 21 successful child candidate evaluations (7 per independent lineage);
- at most one Toolkit LLM call per write/read operation;
- at most three compile-repair attempts;
- Reflector access to rotating failures/successes, traces, write examples,
  lineage and up to three scored population reference programs;
- no static case content or judge explanation in Reflector input;
- three independent repeated evaluations of top candidates before promotion;
- one outer Evolution seed (`0`) for the current minimal formal feasibility
  experiment; additional outer seeds are optional future stability/robustness
  repeats;
- one sealed post-selection D/Test entry through the Benchmark Adapter.

The review-domain adaptation is deliberately outside the searched program:

- immutable five-role rubric;
- fixed five-specialist-then-Meta topology;
- OpenReview episode construction;
- review evaluator and frozen opinion classifier;
- paper/retrieval inputs and output schema.

## Candidate interface

Every candidate defines:

```python
INSTRUCTION_KNOWLEDGE_ITEM: str
INSTRUCTION_QUERY: str
INSTRUCTION_RESPONSE: str
ALWAYS_ON_KNOWLEDGE: str

@dataclass
class KnowledgeItem: ...

@dataclass
class Query: ...

class KnowledgeBase:
    def __init__(self, toolkit): ...
    def write(self, item: KnowledgeItem, raw_text: str) -> None: ...
    def read(self, query: Query) -> str: ...
```

`read()` must return at most 3,000 characters. Candidate imports are restricted
to a small standard-library whitelist. It receives only Toolkit capabilities:
budgeted LLM calls, embeddings, an ephemeral vector collection, in-memory SQLite
and structured logs.

Candidate import, construction, write and read execute in a separate bubblewrap
process. The process receives a cleared environment, read-only runtime, temporary
working directory, no network namespace, memory/CPU/file-descriptor limits and a
JSON-RPC capability channel. The production repository, API environment and
memory database are not mounted.

## Six independent searches

QUAL, CLAR, SIGN, ORIG and POL use separate datasets, pools, scores, Reflector
feedback and winners. META is not searched until all five winners have been
frozen. Its inputs are cached specialist outputs; training outputs are generated
out of fold.

No reviewer is skipped. Specialists do not see each other. META cannot modify or
send feedback into specialist pools.

## Data isolation

CoCoReviewBench's 3900 papers are a benchmark source pool, not a requirement to
run all 3900 as the official test. The frozen chronological manifest defines the
only formal split: A Historical=2391, B Evolution Validation=299, C
Effectiveness Holdout=300, and D Held-out CoCo Test=899. Paper ID, normalized
title, and paper family overlap are all zero; A/B/C are permanently excluded
from D and development cutoffs precede held-out-test cutoffs.

`prepare-specialists` materializes A/B membership from that manifest and records
canonical preprocessing and asset hashes. It does not sample a new OpenReview
validation split. The old 20-paper `memory_corpus.jsonl` and OpenReview default-30
validation rules are deprecated screening protocols, not formal inputs.

The preparation command references a separate frozen Semantic Scholar pool and
records its SHA-256. Every target entry carries its review cutoff, target
identity, pool/method version, retrieval config, result SHA, and dated results.
Unknown-date and post-cutoff results are rejected. S2 is an optional capability,
not default context, and candidate processes cannot perform live queries.

The Human Reference Boundary is unchanged in every zone: human opinions, gold,
taxonomy and judge rationale/full traces stay evaluator-only. A historical
`episodes.jsonl`, B `validation.jsonl`, and specialist `queries.jsonl` contain
only paper tasks, safe metadata and source anchors. B gold is stored separately
under `evaluator_only/<TASK>/validation_references.jsonl`, marked
`evaluator_only=true` and `candidate_forbidden=true`, and joined by ID only in
the evaluator loader. Preparation and formal runtime both fail closed on
forbidden fields or copied human-reference text in Candidate-visible artifacts.

## Fitness

For specialists, a fixed judge returns five 1–5 scores:

- alignment/correctness;
- completeness/thoroughness;
- grounding;
- verifiability;
- clarity.

The mean is normalized to `[0,1]`. A frozen ReviewSplit/ReviewClassify adapter
assigns candidate categories. The candidate's self-declared `subcategory` field
is ignored. Coverage is:

```text
|candidate categories ∩ human categories| / |human categories|
```

This corrects the unbounded category-count ratio that can reward label flooding.
Specialist fitness is the harmonic mean of quality and coverage.

META uses the harmonic mean of three groups:

- adjudication clarity, groundedness, verifiability and conflict coverage;
- alignment and completeness against the human meta-review;
- overall calibration and decision consistency.

CoCoReviewBench supplies metric and taxonomy design only. It is not a second
evolution architecture.

## Artifacts

```text
runs/<suite>/<TASK>/seed_<n>/
  config.json
  selection.json
  pool/*.py
  scores.jsonl
  lineage.jsonl
  traces/iter_*_rotating.jsonl
  best_program.py
  best_eval_report.json

runs/<suite>/<TASK>/best_program.py
runs/<suite>/suite_manifest.json
```

Static case text is not written into iteration traces. `best_eval_report.json`
contains aggregate static results only.
Rotating evaluator full traces live under a directory whose manifest marks them
`evaluator_only=true` and `candidate_forbidden=true`; promotion copies programs,
never traces. Candidate mutation receives only the fail-closed numeric/category
projection.
`final-evaluate-suite` is the sealed test path: it runs QUAL/CLAR/SIGN/ORIG/POL
once, builds the sixth task from those outputs, then runs META once. The report
stores per-case scores but hides paper, reference, retrieval and answer content;
an existing report is never overwritten.
The legacy-looking `evaluate --final-test` route is rejected unconditionally;
there is no second formal D/Test report path.

Formal searches also write `search_controller_checkpoint.json`. It binds the
exact config/data/Seed identity and atomically commits admitted Candidates,
lineage completed budgets, RNG state, selection repeats and JSONL log lengths.
Rerunning the identical command resumes this controller plus the current
Candidate's Historical checkpoint; a different Candidate/config/replicate
identity is rejected instead of inheriting state.

## Formal and ablation modes

The formal configuration is the CLI default. The following options implement
the agreed ablations without adding another architecture:

- three un-evolved seeds: `--iterations 0`;
- memory/schema/logic only: `--mutation-scope schema_logic`;
- prompt/instructions only: `--mutation-scope instructions_only`;
- no population/linear best: `--parent-policy best`;
- no historical memory: prepare/evaluate with an empty episode set;
- no memory production baseline: `--memory_mode none`;
- original AgentReview baseline: run the separately preserved 0716 version.

`--smoke` reduces the search and permits a non-formal classifier fallback. Smoke
results are explicitly non-reportable.

`--pilot` also permits the non-formal classifier and deterministic embedding
fallbacks, but preserves the explicitly requested iterations, subset sizes and
selection repeats. It supports complete small-data pipeline tests and remains
explicitly non-reportable. `pilot_full.sh` runs all five specialists, constructs
out-of-fold META inputs, evolves META, and summarizes the initial-seed versus
evolved-candidate validation scores.

Formal searches require `--embedding-adapter module:factory` with
`--embedding-model-name BAAI/bge-m3` and an immutable
`--embedding-revision`. Static k-means and facility-location selection use only
that adapter; hashed fallback embeddings are restricted to smoke/unit tests.

## Known operational requirement

Formal specialist fitness requires a frozen classifier adapter passed as
`module:factory`. The factory returns the split and classify callables. This keeps
large ReviewSplit/ReviewClassify checkpoints outside the source repository while
pinning their adapter and model versions in experiment configuration.
