# AgentReview M★ repository map

## Immutable source

- `REVIEW_Request.md`: the only source for the five reviewer definitions.
- Expected SHA-256:
  `609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42`.
- `agentreview/single_paper_review_roles.py` verifies the hash before building
  roles. Do not copy or redefine the rubric elsewhere.

## Fixed runtime topology

1. `agentreview/paper_review_player_md.py` builds the canonical rich-paper
   context. Frozen S2 data is a separate optional capability and is never
   injected by default.
2. `run_single_paper_review_cli.py` loads six independent promoted
   MemoryPrograms.
3. QUAL, CLAR, SIGN, ORIG and POL review independently.
4. META alone reads all five completed specialist reviews.
5. Completed output is appended to the online memory library.

The Task Agents, evaluator, topology and reviewer definitions are not mutation
targets.

## M★ source

- Search and population: `memory_evolution/search.py`
- Candidate protocol: `memory_evolution/program_protocol.py`
- Three seeds: `memory_evolution/seeds/`
- Reflector and compile repair: `memory_evolution/reflector.py`
- Process sandbox: `memory_evolution/sandbox.py`,
  `memory_evolution/sandbox_worker.py`
- OpenReview task adapter: `memory_evolution/agentreview_data.py`
- Specialist and Meta fitness: `memory_evolution/metrics.py`
- Staged Meta cache: `memory_evolution/meta_cache.py`
- Commands: `memory_evolution/cli.py`

## Invariants

- Exactly three M★ population seeds: vector search, LLM summarizer and
  experience learner.
- Six role pools; inside each specialist pool the three Seed code lineages stay
  independent and runtime Memory State is never inherited across Candidates.
- Formal defaults: 21 successful child candidate evaluations (7 per lineage),
  static 25, rotating 5, ratio 5:1, temperature 0.15, three compile repairs,
  five experiment seeds. Failed mutation/guard/compile/evaluation attempts do
  not consume a completed lineage slot.
- Static cases never enter Reflector feedback. Rotating feedback may expose at
  most two weighted failures and two successes.
- Test data is evaluated only after promotion and never becomes feedback.
- Candidate code never executes in the host process.
- Formal subset selection uses frozen BGE-M3; no random/hash fallback.
- META validation families are absent from all specialist evolution.
- Reflector mutations and compile repairs use contextual V4A patches.
