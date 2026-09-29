"""Prepare CoCo-only development splits, preserving the original sampled 1300.

No model calls, no reference relabeling, no search or final evaluation. Historical
manifests remain unchanged. All selections are independent of system scores.
"""
from collections import Counter, defaultdict
from pathlib import Path
import hashlib
import json
import sys

REPO = Path(__file__).resolve().parents[1]
BASE = REPO.parent
OUT = REPO / "artifacts/coco-paired-resplit-20260917"
OLD = REPO / "artifacts/paired-benchmark-20260917"
COCO = BASE / "CoCoReviewBench"
ORIGINAL = REPO / "data/original/coco_1300"
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.data import canonical_title, unwrap_value
from agentreview.research_v2.coco_protocol import _benchmark_files, _paper_text_path
from agentreview.research_v2.experiment_protocol import normalized_content_sha256, _identity, _identity_keys, _validate_paper_asset
from agentreview.research_v2.io import atomic_write_json, write_immutable_jsonl, file_sha256
from agentreview.research_v2.models import Paper
from agentreview.research_v2.development_coco_worker import _load_standard


def bind(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": file_sha256(path)}


def read(path):
    return json.loads(Path(path).read_text())


def key(label, pid):
    return hashlib.sha256(f"coco-resplit:0:{label}:{pid}".encode()).hexdigest()


def main(*, retain_original_evaluation=False):
    OUT.mkdir(parents=True, exist_ok=True)
    original_binding = bind(ORIGINAL / "manifest.json")
    original = read(original_binding["path"])
    original_ids = [r["paper_id"] for r in original["records"]]
    assert len(original_ids) == len(set(original_ids)) == 1300
    records, meta, texts, source_bindings = {}, {}, {}, []
    for venue, year, source in _benchmark_files(COCO):
        binding = bind(source)
        source_bindings.append(binding)
        for line_no, line in enumerate(source.open(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            pid = row["id"]
            assert pid not in records
            content = row["content"]
            title = str(unwrap_value(content.get("title")) or "").strip()
            text_path = _paper_text_path(COCO, row)
            text = text_path.read_text(encoding="utf-8")
            texts[pid] = text
            records[pid] = row
            meta[pid] = {"paper_id": pid, "title": title, "venue": venue, "year": year,
                         "venue_year": f"{venue}_{year}", "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
                         "normalized_content_sha256": normalized_content_sha256(text),
                         "paperhash": str(unwrap_value(content.get("paperhash")) or ""),
                         "source_file": binding, "source_line": line_no, "md_path": str(text_path)}
    assert len(records) == 3900 and set(original_ids) <= set(records)
    parents = {pid: pid for pid in records}

    def find(pid):
        if parents[pid] != pid:
            parents[pid] = find(parents[pid])
        return parents[pid]

    def union(a, b):
        parents[find(b)] = find(a)

    family_source = BASE / "AgentReview_0811_new/AgentReview/configs/coco_temporal_split_v2/paper_families.jsonl"
    for line in family_source.open():
        family = json.loads(line)
        ids = [x["paper_id"] for x in family["source_provenance"]]
        for pid in ids[1:]:
            union(ids[0], pid)
    seen = {}
    for pid, m in sorted(meta.items()):
        for kind, value in (("title", canonical_title(m["title"])), ("paperhash", m["paperhash"]),
                            ("normalized_content", m["normalized_content_sha256"])):
            if value:
                if (kind, value) in seen:
                    union(seen[kind, value], pid)
                seen[kind, value] = pid
    families = defaultdict(list)
    for pid in meta:
        families[find(pid)].append(pid)
    for ids in families.values():
        ids.sort()
        family_id = hashlib.sha256("\n".join(ids).encode()).hexdigest()
        for pid in ids:
            meta[pid].update(work_family=family_id, version_ids=ids)

    uses = {x["paper_id"]: x["evidence"] for x in read(OLD / "audit/use-inventory.json")["records"]}
    cases = {"BJuysoFeg": "AdaBN", "Hymt27b0Z": "MINE", "Cj3B4SoWuT": "Latte"}
    case_receipt = atomic_write_json(OUT / "audit/user-supplied-case-provenance.json", {
        "source": "User message on 2026-09-17 quoting prior low_score_audit_20260917 report",
        "original_report_available_on_this_host": False,
        "purpose": "Named technical failure cases now used to guide this system audit",
        "records": [{"paper_id": pid, "case": label, "title": meta[pid]["title"]} for pid, label in cases.items()],
        "other_unnamed_cases": "The quoted six-case report is unavailable locally; no blanket exclusion of the 902 or 803 scored papers"})
    for pid in cases:
        uses.setdefault(pid, []).append({"purpose": "current_user_supplied_system_diagnostic_case", "evidence": bind(case_receipt)})
    used_families = {meta[pid]["work_family"] for pid in uses if pid in meta}
    original_families = {meta[pid]["work_family"] for pid in original_ids}
    exclusions = []
    final_ids = []
    final_families = set()
    for pid in original_ids:
        family = meta[pid]["work_family"]
        evidence = [{"paper_id": other, "evidence": uses[other]}
                    for other in meta[pid]["version_ids"] if other in uses]
        if family in used_families:
            exclusions.append({"paper_id": pid, "reason": "documented_development_use_or_same_family", "evidence": evidence})
        elif family in final_families:
            exclusions.append({"paper_id": pid, "reason": "duplicate_family_in_sample"})
        else:
            final_ids.append(pid)
            final_families.add(family)

    if retain_original_evaluation:
        final_ids = list(original_ids)

    # Reserve all original 1300 identities and their versions, including those
    # excluded from the primary effect estimate, from all new development arms.
    available = [pid for pid in meta if meta[pid]["work_family"] not in original_families]
    representatives = {}
    for pid in sorted(available, key=lambda p: (meta[p]["year"], key("representative", p))):
        representatives.setdefault(meta[pid]["work_family"], pid)
    pool = list(representatives.values())
    selected = set()

    def select(label, count, years):
        buckets = defaultdict(list)
        for pid in pool:
            if pid not in selected and meta[pid]["year"] in years:
                buckets[meta[pid]["venue_year"]].append(pid)
        total = sum(map(len, buckets.values()))
        assert 0 < count <= total, (label, count, total)
        quotas = {group: len(values) * count // total for group, values in buckets.items()}
        order = sorted(buckets, key=lambda group: (-(len(buckets[group]) * count % total), group))
        for group in order[:count - sum(quotas.values())]:
            quotas[group] += 1
        result = []
        for group in sorted(buckets):
            result.extend(sorted(buckets[group], key=lambda p: key(label, p))[:quotas[group]])
        assert len(result) == count
        selected.update(result)
        return result

    split_ids = {"build": select("build", 125, set(range(2017, 2022))),
                 "static": select("static", 25, {2022}),
                 "rotating": select("rotating", 125, {2022}), "sealed_test": final_ids}
    human_source = COCO / "benchmark/benchmark_all_human_classify.jsonl"
    humans = {r["id"]: r for r in (json.loads(line) for line in human_source.open() if line.strip())}
    selection_source = REPO / "artifacts/deployment/original-coco/references/benchmark_reviewer_selection.json"
    selections = read(selection_source)
    evaluation = _load_standard(COCO, "_4_evaluation")
    manifests, reference_checks, all_keys = {}, {}, {}
    for split, ids in split_ids.items():
        root = OUT / "inputs" / split
        reference = None
        if split != "build":
            scoring = root / "scoring-only"
            for pid in ids:
                assert pid in humans and pid in selections
                obj = records[pid]
                assert len(obj["conflicts_validation"]) == len(obj["opinions"])
                assert len(obj["rebuttal_validation"]) == len(obj["opinions"])
            benchmark = write_immutable_jsonl(scoring / "benchmark.jsonl", [records[pid] for pid in ids])
            human = write_immutable_jsonl(scoring / "human.jsonl", [humans[pid] for pid in ids])
            selection = atomic_write_json(scoring / "selection.json", {pid: selections[pid] for pid in ids})
            evaluation.benchmark_reviewer_selection_file = selection
            loaded = evaluation.load_benchmark_records(ids, [benchmark])
            chosen = evaluation.left_out_one([benchmark])
            refs = evaluation.load_human_review_data(loaded, [benchmark])
            categories = evaluation.load_human_complete_category_sets(human, ids)
            assert set(refs) == set(categories) == set(chosen) == set(ids)
            assert all(refs[pid]["paper_correct"].strip() for pid in ids)
            reference_checks[split] = {"count": len(ids), "original_loader_passed": True,
                                       "nonempty_correct_paper_reference": len(ids), "model_calls": 0}
            reference = bind(atomic_write_json(scoring / "references.json", {
                "protocol": "research-v2-coco-reference-manifest-v1", "partition": split, "paper_ids": ids,
                "benchmark_files": [bind(benchmark)], "complete_human_classify_file": bind(human),
                "reviewer_selection_file": bind(selection)}))
        rows = []
        for pid in ids:
            m = meta[pid]
            source = {"dataset": "CoCoReviewBench/published-benchmark", "source_partition": split,
                      "venue": m["venue"], "year": m["year"], "venue_year": m["venue_year"],
                      "benchmark_source_sha256": m["source_file"]["sha256"], "source_line": m["source_line"],
                      "md_sha256": m["content_sha256"]}
            if split == "sealed_test":
                original_paper = ORIGINAL / "papers" / f"{pid}.json"
                old = read(original_paper)
                assert old["text"] == texts[pid], f"Frozen and current full text differ: {pid}"
                source["original_frozen_asset"] = bind(original_paper)
            paper = Paper(pid, m["title"], str(unwrap_value(records[pid]["content"].get("abstract")) or ""), texts[pid], source)
            asset = atomic_write_json(root / "papers" / f"{pid}.json", paper.to_dict())
            row = {k: m[k] for k in ("paper_id", "title", "work_family", "version_ids", "content_sha256", "normalized_content_sha256", "venue_year")}
            row.update(source_partition=split, assets={"paper": bind(asset)})
            if reference:
                row["assets"]["coco_reference"] = reference
            _identity(row, split)
            _validate_paper_asset(row, split, split)
            rows.append(row)
        all_keys[split] = set().union(*(_identity_keys(row) for row in rows))
        manifests[split] = bind(atomic_write_json(root / "manifest.json", {
            "protocol": "shared-memory-split-v1", "split": split, "records": rows}))
    for a in all_keys:
        for b in all_keys:
            if a != b:
                assert not all_keys[a] & all_keys[b], f"Partition identity overlap: {a}/{b}"
    assert bind(ORIGINAL / "manifest.json") == original_binding
    atomic_write_json(OUT / "audit/exclusions.json", {"records": [] if retain_original_evaluation else exclusions})
    if retain_original_evaluation:
        atomic_write_json(OUT / "audit/historical-use-annotations.json", {
            "policy": "retain-label-no-test-feedback", "records": exclusions,
            "known_uses_retained_for_diagnosis": True, "strictly_unseen_claim": False})
    atomic_write_json(OUT / "audit/families.json", {
        "source": bind(family_source), "method": "Frozen family links, canonical title, paperhash and normalized complete Markdown",
        "families": [ids for ids in families.values() if len(ids) > 1]})
    atomic_write_json(OUT / "original-1300-preserved.json", {
        "binding": original_binding, "paper_ids": original_ids,
        "group_counts": dict(Counter(meta[pid]["venue_year"] for pid in original_ids)),
        "historical_manifest_modified": False})
    summary = {"status": "data_prepared_and_cpu_validated", "protocol": "coco-only-paired-resplit-v1",
        "coco_records": len(records), "unique_families": len(families),
        "venue_year_counts": dict(Counter(m["venue_year"] for m in meta.values())),
        "original_evaluation_basis": 1300, "primary_evaluation_count": len(final_ids),
        "excluded_from_primary": 0 if retain_original_evaluation else len(exclusions), "development_pool_before_family_dedup": len(available),
        "development_pool_families": len(pool), "counts": {s: len(ids) for s, ids in split_ids.items()},
        "split_years": {s: dict(Counter(meta[p]["venue_year"] for p in ids)) for s, ids in split_ids.items()},
        "sampling_rule": "Development seed 0 deterministic SHA256, proportional venue-year quotas; build 2017-2021, static/rotating 2022; original evaluation remains all-year sampled",
        "temporal_holdout_claim": False, "strictly_unseen_claim": False,
        "historical_use_limitations": ("Original 1300 retained with documented development-use labels for paired diagnostics; unknown history remains unknown; no strictly unseen claim"
            if retain_original_evaluation else "Known actual uses and declared development assignments excluded; unknown history remains unknown; no blanket exclusion for previous scoring"),
        "cross_partition_identity_overlap_count": 0, "reference_validation": reference_checks,
        "manifests": manifests, "source_files": source_bindings,
        "human_classify_source": bind(human_source), "reviewer_selection_source": bind(selection_source),
        "use_inventory": bind(OLD / "audit/use-inventory.json"), "model_calls": 0,
        "execution_started": False, "all_systems_must_be_frozen_before_final": True,
        "original_coco_reference_annotations_reused": True, "external_reference_curation_required": False}
    atomic_write_json(OUT / "data-readiness.json", summary)
    print(json.dumps({k: summary[k] for k in ("status", "coco_records", "unique_families", "counts", "excluded_from_primary", "cross_partition_identity_overlap_count", "reference_validation")}, indent=2))


if __name__ == "__main__":
    main()
