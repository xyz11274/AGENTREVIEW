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
- 20 mutation iterations;
- at most one Toolkit LLM call per write/read operation;
- at most three compile-repair attempts;
- Reflector access to rotating failures/successes, traces, write examples,
  lineage and up to three scored population reference programs;
- no static case content or judge explanation in Reflector input;
- three independent repeated evaluations of top candidates before promotion;
- five independent evolution seeds for formal results;
- one post-selection held-out test.

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

`prepare-specialists`:

1. loads `OpenReview/train_clean.jsonl`;
2. forms transitive work families whenever records share either `paperhash` or
   normalized title;
3. builds the work-family set from `OpenReview/test.jsonl`;
4. removes every overlapping training family;
5. samples train and validation families;
6. reserves separate specialist-validation and meta-validation families;
7. writes one dataset per specialist dimension.

The preparation command also embeds a frozen Semantic Scholar cache and records
its SHA-256. Formal specialist evolution rejects a manifest without that hash,
so live retrieval changes cannot silently alter an experiment.

Human category opinions appear only in training write episodes or evaluator
targets. Validation questions contain the paper and source anchors, not the
human answer. The candidate and Task Agent cannot read rebuttals, meta-review,
decision, validation reference or test reference for the current paper.
META validation uses work families that were absent from specialist evolution;
its training specialist outputs are generated out of fold.

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
`final-evaluate-suite` is the sealed test path: it runs QUAL/CLAR/SIGN/ORIG/POL
once, builds the sixth task from those outputs, then runs META once. The report
stores per-case scores but hides paper, reference, retrieval and answer content;
an existing report is never overwritten.

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
