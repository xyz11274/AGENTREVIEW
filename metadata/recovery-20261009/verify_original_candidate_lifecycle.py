"""Local persistence proof for a copy of the historical failing candidate.

Only signature storage changes. This diagnostic copy is never admitted or scored.
"""
from pathlib import Path
from agentreview.research_v2.io import atomic_write_json, file_sha256
from agentreview.reviewer_runtime.sandbox import SandboxSession
from agentreview.reviewer_runtime.toolkit import MemoryToolkit

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "original-candidate-lifecycle"
SOURCE = ROOT / "artifacts/pilot-0930-local/search/proposals/a2-e0/program.py"
OUT.mkdir(parents=True, exist_ok=True)
code = SOURCE.read_text()
assert code.count("self._jaccard(signature, prior)") == 1
assert code.count(".append(signature)") == 1
code = code.replace("self._jaccard(signature, prior)", "self._jaccard(signature, frozenset(prior))")
code = code.replace(".append(signature)", ".append(sorted(signature))")
copy = OUT / "diagnostic-program.py"
copy.write_text(code)
roles = []
for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL"):
    toolkit = MemoryToolkit(max_llm_calls_per_operation=0)
    first = "Methodological assumptions require independently tuned baselines and reproducible variance estimates."
    second = "Notation glossary and self contained captions need consistent terminology and clear cross references."
    with SandboxSession(str(copy), toolkit, require_bubblewrap=True) as worker:
        interface = worker.program_interface()
        for text in (first, first, second):
            worker.write(interface.KnowledgeItem(topic="Persistence diagnostic", content=text,
                         reviewer_name=role, reviewer_dimension=role), text)
            snap = worker.snapshot()
        before = [worker.read(interface.Query(query_text=text, reviewer_dimension=role))
                  for text in (first, second)]
        after_read = worker.snapshot()
        original_pid = worker._process.pid
    with SandboxSession(str(copy), MemoryToolkit(max_llm_calls_per_operation=0),
                        require_bubblewrap=True) as recovered:
        recovered.restore(snap)
        assert recovered.snapshot() == snap
        interface = recovered.program_interface()
        after = [recovered.read(interface.Query(query_text=text, reviewer_dimension=role))
                 for text in (first, second)]
        assert after == before and recovered.snapshot() == after_read
        assert all(text in result for text, result in zip((first, second), after))
        recovered.write(interface.KnowledgeItem(topic="Persistence diagnostic", content=first,
                         reviewer_name=role, reviewer_dimension=role), first)
        assert recovered.snapshot() == after_read
        restored_pid = recovered._process.pid
        assert restored_pid != original_pid
    path = atomic_write_json(OUT / f"{role}.snapshot.json", snap)
    roles.append({"role":role,"original_pid":original_pid,"restored_pid":restored_pid,
                  "snapshot":{"path":str(path),"sha256":file_sha256(path)},
                  "retrieval_equal":True,"both_distinct_experiences_retrievable":True,
                  "duplicate_after_restore_does_not_change_state":True})
atomic_write_json(OUT / "evidence.json", {"status":"complete", "scope":"Local serialization diagnostic with deterministic toolkit embeddings; no model calls, scoring, pool admission, or strategy changes",
    "source":{"path":str(SOURCE),"sha256":file_sha256(SOURCE)},
    "diagnostic_program":{"path":str(copy),"sha256":file_sha256(copy)},
    "repair":"Persist sorted signature lists and reconstruct transient frozensets for unchanged Jaccard comparison", "roles":roles})
print("Historical candidate diagnostic: all five roles restored exact state, retrieval and deduplication in new processes")
