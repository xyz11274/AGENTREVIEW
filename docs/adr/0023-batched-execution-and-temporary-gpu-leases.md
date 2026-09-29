# Batched execution and temporary GPU leases

The user authorized four concurrent papers, fixed GPUs 0–3, and temporary use of
otherwise idle GPUs 4–6. GPU 7 is permanently excluded. The existing study,
three searches, data allocation, random streams, retry budgets and source
bindings remain unchanged. This is a separately frozen deployment appendix.

`deployment/elastic_study.py run` wraps the original backend. Build and review
generation use four private paper workers. An aggregate admission limit reserves
actual input tokens plus the entire requested output budget (262144 tokens total).
Individual context checks, logical retries, full raw output preservation and
truncation rejection still belong to the original backend. A private client caches
identical tokenization results; it does not truncate or rewrite messages.

Assessment cohorts run in batches of at most 25 papers. All final reviews in a
batch complete before scoring. Original per-paper evaluators rendezvous at Split,
Classify and Judge barriers, with at most two active original stage workers. Each
paper retains its atomic receipt; the original receipt validator checks every
child before cohort composition. A failed peer stops progression and prevents
admission of a partially evaluated candidate. Complete cached stages require no
GPU allocation. A rotating cohort contains its original five fresh papers. No
dependent search rounds or candidate selections overlap.

The base four-card mode coordinator and original vLLM arguments remain unchanged.
Before a noncached Split/Classify wave, the lease pool may claim two of GPUs 4–6:

1. No compute process, at most 256 MiB used, and zero reported utilization for
   30 continuous seconds are required. Missing or failed observations reset that
   interval. GPU 7 never enters the candidate list.
2. Per-GPU advisory locks and a second occupancy check precede service startup;
   the guardian checks occupancy again. These are cooperative local locks, not
   a scheduler reservation against unrelated users.
3. Only actual queued stage jobs justify loading. Original BF16 weights, TP2,
   service ports, contexts, generation parameters and worker identity validation
   are retained. The three spare cards cannot host another original TP4 27B
   service, so reviewer/Judge remain on GPUs 0–3.
4. The borrowed service is released after its stage batch, including exceptions.
   A separate guardian owns the GPU locks, tracks controller identity, and stops
   only its recorded API process and verified descendants if the controller dies.
   Cleanup checks process termination rather than only an empty listening port.
5. If another compute process appears, the current bounded stage jobs drain and
   the lease is released before further work. With no eligible idle pair, scoring
   uses the existing four-card rotation. Public service failures do not produce
   zero scores or reset any search budget.

Creating `artifacts/coco-paired-3search-20260917/deployment-elastic/borrow.pause`
disables new borrowing and requests release after the current in-flight stage
jobs. Removing this operator-created marker re-enables borrowing. Lease records,
readiness, ownership, release results and rotation events are saved in that same
directory. The existing telemetry records all three services, token admission and
per-paper full-flow completion. Borrowing alone is not evidence of higher
throughput; this version batches stages and does not claim overlapping generation
with scoring. Report validated full reviews per hour separately from build
experience generation, together with failure counts.

Verification covers atomic receipt composition, independent paper/role state,
partial failures, barrier cancellation, aggregate token limits, cache reuse,
GPU 7 exclusion, occupied-card rejection, fallback, normal/error release and
controller-death cleanup. A real temporary Split lifecycle check verifies its
original worker-visible service identity and complete release; it performs no
new pilot inference or quality evaluation. Before activating this appendix,
the old controller drains its active papers, existing response hashes and retry
budgets are recorded, and the same run resumes from those artifacts.
