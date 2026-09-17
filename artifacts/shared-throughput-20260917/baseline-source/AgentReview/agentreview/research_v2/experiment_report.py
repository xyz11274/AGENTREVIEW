"""Rebuild independent experiment reports from saved, partitioned score artifacts.

The input manifest declares the comparison roster and each evaluation cohort.
Every declared paper receives a row, including missing and failed evaluations.
The report consumes saved scores and produces a separate immutable directory.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any

from .io import value_sha256


INPUT_PROTOCOL = "research-v2-experiment-report-input-v1"
REPORT_PROTOCOL = "research-v2-experiment-report-v1"
PAPER_PROTOCOL = "research-v2-development-coco-paper-v1"
TABLE_COLUMNS = (
    "BLEU", "ROUGE-L", "BERT.", "Correct.", "Thoro.", "Ground.",
    "Verify.", "Clarity", "Average", "Paper.", "Complete.",
)
SCOPE_FIELDS = (
    "partition", "purpose", "run_id", "scoring_protocol", "evaluation_epoch", "cohort_id",
)
LINEAGE_FIELDS = (
    "variant_id", "kind", "seed", "matched_seed", "candidate_id", "parent_id",
    "round", "program_sha256",
)
VARIANT_KINDS = {"seed", "no_memory", "coco_native", "candidate", "champion"}
SEEDS = {"vector_search", "llm_summarizer", "experience_learner"}
ROLES = {"QUAL", "CLAR", "SIGN", "ORIG", "POL"}
OUTPUT_NAMES = {"report.json", "papers.csv", "variants.csv", "role_diagnostics.csv", "README.md"}


def _text(value: Any, label: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"{label} requires a nonempty string")


def _object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise ValueError(f"{label} requires an object")


def _list(value: Any, label: str) -> list[Any]:
    if isinstance(value, list):
        return value
    raise ValueError(f"{label} requires a list")


def _protocol_identity(value: Any, label: str) -> Any:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, dict) and isinstance(value.get("protocol"), str) and value["protocol"].strip():
        return value
    raise ValueError(f"{label} requires a scoring protocol identity")


def _json_load(data: bytes, source: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Repeated JSON field {key}: {source}")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=pairs)


class _Inputs:
    def __init__(self, manifest_path: Path, output_dir: Path):
        self.base = manifest_path.parent
        self.output_dir = output_dir
        self.provenance: dict[str, dict[str, Any]] = {}

    def read(self, binding: dict[str, Any], *, base: Path | None = None) -> tuple[Any | None, dict[str, Any]]:
        binding = _object(binding, "artifact binding")
        requested = Path(_text(binding.get("path"), "artifact.path")).expanduser()
        source = (requested if requested.is_absolute() else (base or self.base) / requested).resolve()
        if source.is_relative_to(self.output_dir):
            raise ValueError("Report inputs require paths outside the report output directory")
        identity: dict[str, Any] = {"path": str(source), "expected_sha256": binding.get("sha256")}
        try:
            data = source.read_bytes()
        except FileNotFoundError:
            identity.update(status="missing", sha256=None)
            self.provenance[str(source)] = identity
            return None, identity
        digest = hashlib.sha256(data).hexdigest()
        expected = binding.get("sha256")
        if expected is not None and digest != expected:
            raise ValueError(f"Report input hash differs: {source}")
        identity.update(status="read", sha256=digest, size_bytes=len(data))
        previous = self.provenance.get(str(source))
        if previous is not None and previous != identity:
            raise ValueError(f"Report input identity changed: {source}")
        self.provenance[str(source)] = identity
        return _json_load(data, source), identity


def _scope_identity(scope: dict[str, Any]) -> dict[str, Any]:
    partition = scope.get("partition")
    if partition in {"development", "build", "static", "rotating", "sealed_test", "historical"}:
        identity = {name: scope.get(name) for name in SCOPE_FIELDS}
    else:
        raise ValueError("Report scope partition requires a development split, sealed_test, or historical")
    for field in ("purpose", "run_id", "cohort_id"):
        _text(identity[field], f"scope.{field}")
    _protocol_identity(identity["scoring_protocol"], "scope.scoring_protocol")
    epoch = identity["evaluation_epoch"]
    if type(epoch) is int and epoch >= 0:
        return identity
    raise ValueError("scope.evaluation_epoch requires a nonnegative integer")


def _lineage(variant: dict[str, Any]) -> dict[str, Any]:
    _text(variant.get("variant_id"), "variant.variant_id")
    if variant.get("kind") in VARIANT_KINDS:
        result = {name: variant.get(name) for name in LINEAGE_FIELDS}
    else:
        raise ValueError("variant.kind requires seed, no_memory, coco_native, candidate, or champion")
    if variant["kind"] == "seed" and variant.get("seed") not in SEEDS:
        raise ValueError("A seed variant requires one of the three initial Memory seed identities")
    if variant["kind"] == "no_memory" and variant.get("matched_seed") not in SEEDS:
        raise ValueError("A no_memory variant requires its matched Memory seed identity")
    return result


def _empty_score(paper_id: str, status: str, message: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id, "status": status,
        "metrics": {name: None for name in TABLE_COLUMNS},
        "metric_counts": {name: 0 for name in TABLE_COLUMNS},
        "applicability": {name: "unknown" for name in TABLE_COLUMNS},
        "failure": {"stage": "report_input", "category": status, "message": message},
    }


def _validate_score(record: dict[str, Any], identity: dict[str, Any], lineage: dict[str, Any], paper_id: str) -> None:
    if record.get("protocol") != PAPER_PROTOCOL:
        raise ValueError("Final-review CoCo rows require the standard CoCo paper protocol")
    for field in ("partition", "run_id", "evaluation_epoch", "scoring_protocol"):
        if record.get(field) != identity[field]:
            raise ValueError(f"CoCo paper {field} differs from its report scope")
    if record.get("paper_id") != paper_id:
        raise ValueError("CoCo paper ID differs from its planned paper binding")
    if lineage.get("candidate_id") is not None and record.get("candidate_id") != lineage["candidate_id"]:
        raise ValueError("CoCo candidate ID differs from its report variant")
    status = record.get("status")
    if status not in {"complete", "failed", "pending"}:
        raise ValueError("CoCo paper status requires complete, failed, or pending")
    for name in TABLE_COLUMNS:
        count = _object(record.get("metric_counts"), "metric_counts").get(name)
        metric = _object(record.get("metrics"), "metrics").get(name)
        applicability = _object(record.get("applicability"), "applicability").get(name)
        numeric = type(metric) in (float, int) and math.isfinite(metric)
        valid = type(count) is int and (
            (status == "complete" and count == 1 and numeric and applicability == "applicable")
            or (status == "complete" and count == 0 and metric is None and applicability == "not_applicable")
            or (status in {"failed", "pending"} and count == 0 and metric is None and applicability == "unknown")
        )
        if valid is False:
            raise ValueError(f"CoCo paper metric/count/applicability contract differs: {paper_id}/{name}")
    if status == "complete" and record["metric_counts"]["Paper."] != 1:
        raise ValueError("A complete CoCo paper requires its Paper. score")


def _comparison(original: Any, recomputed: dict[str, Any]) -> dict[str, Any]:
    if original is None:
        return {"status": "absent", "original": None, "differences": []}
    original = _object(original, "original aggregate")
    differences = []
    for section in ("absolute_scores", "paper_counts"):
        original_section = _object(original.get(section), f"original aggregate.{section}")
        for metric in TABLE_COLUMNS:
            before = original_section.get(metric)
            after = recomputed[section][metric]
            equal = before == after
            if section == "absolute_scores" and type(before) in (float, int) and type(after) in (float, int):
                equal = math.isclose(before, after, rel_tol=0.0, abs_tol=1e-9)
            if equal is False:
                differences.append({"field": f"{section}.{metric}", "original": before, "recomputed": after})
    return {"status": "equal" if len(differences) == 0 else "different", "original": original, "differences": differences}


def _diagnostics(inputs: _Inputs, variant: dict[str, Any], identity: dict[str, Any], lineage: dict[str, Any], paper_ids: set[str]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for entry in _list(variant.get("role_diagnostics", []), "variant.role_diagnostics"):
        entry = _object(entry, "role diagnostic binding")
        paper_id = _text(entry.get("paper_id"), "role diagnostic.paper_id")
        role = entry.get("role")
        if paper_id not in paper_ids or role not in ROLES:
            raise ValueError("Role diagnostics require a planned paper and one of the five fixed roles")
        protocol = _protocol_identity(entry.get("scoring_protocol"), "role diagnostic.scoring_protocol")
        key = (paper_id, role, value_sha256(protocol))
        if key in seen:
            raise ValueError("Role diagnostic binding repeats a paper/role/protocol")
        seen.add(key)
        value, source = inputs.read(entry["artifact"])
        result.append({
            **identity, **lineage, "paper_id": paper_id, "role": role,
            "diagnostic_scoring_protocol": protocol, "score_role": "diagnostic",
            "status": "missing" if value is None else "recorded", "source": source,
            "diagnostic": value,
        })
    return result


def _variant_rows(inputs: _Inputs, scope: dict[str, Any], variant: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    from .development_coco import aggregate_paper_scores

    identity = _scope_identity(scope)
    lineage = _lineage(variant)
    planned: dict[str, dict[str, Any]] = {}
    for item in _list(scope.get("papers"), "scope.papers"):
        item = _object(item, "planned paper")
        paper_id = _text(item.get("paper_id"), "planned paper.paper_id")
        if paper_id in planned:
            raise ValueError("Report cohort repeats a planned paper ID")
        planned[paper_id] = item
    if len(planned) == 0:
        raise ValueError("A report cohort requires its predeclared paper list")
    outcomes: dict[str, dict[str, Any]] = {}
    for item in _list(variant.get("paper_outcomes", []), "variant.paper_outcomes"):
        item = _object(item, "paper outcome")
        paper_id = item.get("paper_id")
        if paper_id not in planned or paper_id in outcomes:
            raise ValueError("Paper outcomes require a unique subset of the planned cohort")
        if item.get("status") not in {"not_generated", "not_scored", "failed"}:
            raise ValueError("Paper outcome status requires not_generated, not_scored, or failed")
        outcomes[paper_id] = item

    receipt = None
    assessment_source = None
    if variant.get("assessment") is not None:
        receipt, assessment_source = inputs.read(variant["assessment"])
    bindings: dict[str, dict[str, Any]] = {}
    if receipt is not None:
        receipt = _object(receipt, "assessment")
        for field in ("partition", "run_id", "evaluation_epoch", "scoring_protocol"):
            if receipt.get(field) != identity[field]:
                raise ValueError(f"CoCo assessment {field} differs from its report scope")
        if "purpose" in receipt:
            allowed_purposes = {
                "development": {"development", "selection", "reflection", "fitness"},
                "final_test": {"final_test"},
            }
            if identity["purpose"] not in allowed_purposes.get(receipt["purpose"], {receipt["purpose"]}):
                raise ValueError("CoCo assessment execution purpose differs from its report scope")
        if lineage.get("candidate_id") is not None and receipt.get("candidate_id") != lineage["candidate_id"]:
            raise ValueError("CoCo assessment candidate differs from its report variant")
        for item in _list(receipt.get("papers"), "assessment.papers"):
            item = _object(item, "assessment paper binding")
            paper_id = _text(item.get("paper_id"), "assessment paper.paper_id")
            if paper_id in bindings or paper_id not in planned:
                raise ValueError("CoCo assessment paper bindings require a unique subset of the planned cohort")
            bindings[paper_id] = item

    rows = []
    records = []
    for paper_id, paper in planned.items():
        source = None
        record = None
        if paper_id in bindings:
            record, source = inputs.read(bindings[paper_id], base=Path(assessment_source["path"]).parent)
        if record is None:
            message = "The planned paper has no saved CoCo result in this report input snapshot"
            record = _empty_score(paper_id, "missing", message)
            outcome = outcomes.get(paper_id)
            if outcome is not None:
                record["status"] = {"not_generated": "missing", "not_scored": "pending", "failed": "failed"}[outcome["status"]]
                record["failure"] = outcome.get("failure")
                record["review"] = outcome.get("review")
                record["generation_status"] = {"not_generated": "pending", "not_scored": "complete", "failed": "failed"}[outcome["status"]]
        else:
            record = _object(record, "CoCo paper record")
            _validate_score(record, identity, lineage, paper_id)
            record = {**record, "generation_status": "complete"}
        records.append(record)
        rows.append({
            **identity, **lineage, "paper_id": paper_id, "title": paper.get("title"),
            "status": record["status"], "metrics": record["metrics"],
            "generation_status": record.get("generation_status", "unknown"),
            "metric_counts": record["metric_counts"], "applicability": record["applicability"],
            "failure": record.get("failure"), "source": source,
            "review": record.get("review"), "raw": record.get("raw"),
            "paper_judge_scores": record.get("paper_judge_scores"),
            "category_judge_scores": record.get("category_judge_scores"),
        })
    aggregate = aggregate_paper_scores([record for record in records if record["status"] == "complete"])
    # Explicit empty columns make a completely absent historical seed comparable
    # to a completed result without assigning a synthetic score.
    for section, empty in (("absolute_scores", None), ("paper_counts", 0)):
        aggregate.setdefault(section, {})
        for column in TABLE_COLUMNS:
            aggregate[section].setdefault(column, empty)
    counts = Counter(row["status"] for row in rows)
    evaluation_complete = counts["complete"] == len(planned)
    summary = {
        **identity, **lineage, "expected_count": len(planned),
        "complete_count": counts["complete"], "failed_count": counts["failed"],
        "pending_count": counts["pending"], "missing_count": counts["missing"],
        "coverage": counts["complete"] / len(planned),
        "evaluation_complete": evaluation_complete,
        "recorded_status": variant.get("status"), "failure": variant.get("failure"),
        "aggregate_role": "complete_evaluation" if evaluation_complete else "successful_subset_diagnostic",
        "selection_effect": "report_only", "aggregate": aggregate,
        "original_comparison": _comparison(receipt.get("aggregate") if receipt else None, aggregate),
        "assessment_source": assessment_source,
    }
    return rows, summary, _diagnostics(inputs, variant, identity, lineage, set(planned))


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _csv(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _cell(row.get(field)) for field in fields})
    return buffer.getvalue().encode("utf-8")


def _flatten_paper(row: dict[str, Any]) -> dict[str, Any]:
    flattened = {key: value for key, value in row.items() if key not in {"metrics", "metric_counts", "applicability"}}
    for column in TABLE_COLUMNS:
        flattened[column] = row["metrics"][column]
        flattened[f"{column}_count"] = row["metric_counts"][column]
        flattened[f"{column}_applicability"] = row["applicability"][column]
    return flattened


def _flatten_summary(row: dict[str, Any]) -> dict[str, Any]:
    flattened = {key: value for key, value in row.items() if key != "aggregate"}
    for column in TABLE_COLUMNS:
        flattened[column] = row["aggregate"]["absolute_scores"][column]
        flattened[f"{column}_count"] = row["aggregate"]["paper_counts"][column]
    return flattened


def _commit_bytes(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        if path.is_file() and path.read_bytes() == content:
            return
        raise FileExistsError(f"A changed report snapshot requires a new output directory: {path}") from None


def generate_experiment_report(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    stage: str = "development",
) -> dict[str, Any]:
    """Write JSON, CSV, and a short index from a saved experiment manifest.

    ``development`` consumes development/build/static/rotating scopes. ``final`` additionally consumes
    sealed-test and historical scopes. Every scope and variant stays separate.
    Identical input snapshots can replay into the same immutable output directory.
    """
    if stage not in {"development", "final"}:
        raise ValueError("Report stage requires development or final")
    source = Path(manifest_path).expanduser().resolve()
    target = Path(os.path.abspath(Path(output_dir).expanduser()))
    if target.resolve() != target:
        raise ValueError("Report output requires a canonical directory path")
    if target.exists():
        if target.is_dir() is False or any(path.name not in OUTPUT_NAMES or path.is_symlink() for path in target.iterdir()):
            raise ValueError("Report output requires a dedicated directory containing report artifacts")
    inputs = _Inputs(source, target)
    manifest, manifest_source = inputs.read({"path": str(source)})
    manifest = _object(manifest, "report manifest")
    if manifest.get("protocol") != INPUT_PROTOCOL:
        raise ValueError("Report input requires its explicit manifest protocol")
    papers, summaries, diagnostics = [], [], []
    seen = set()
    skipped = 0
    for scope in _list(manifest.get("scopes"), "manifest.scopes"):
        scope = _object(scope, "scope")
        if stage == "development" and scope.get("partition") in {"sealed_test", "historical"}:
            # The partition gate precedes every path read and scope asset check.
            skipped += 1
            continue
        identity = _scope_identity(scope)
        variants = _list(scope.get("variants"), "scope.variants")
        if len(variants) == 0:
            raise ValueError("A report scope requires its planned variant roster")
        for variant in variants:
            variant = _object(variant, "variant")
            key = value_sha256({**identity, "variant_id": variant.get("variant_id")})
            if key in seen:
                raise ValueError("Report manifest repeats a scope/variant identity")
            seen.add(key)
            rows, summary, role_rows = _variant_rows(inputs, scope, variant)
            papers.extend(rows)
            summaries.append(summary)
            diagnostics.extend(role_rows)
    report = {
        "protocol": REPORT_PROTOCOL, "stage": stage,
        "input_manifest": manifest_source, "skipped_scope_count": skipped,
        "selection_effect": "report_only", "variants": summaries,
        "papers": papers, "role_diagnostics": diagnostics,
        "provenance": sorted(inputs.provenance.values(), key=lambda item: item["path"]),
    }
    report["report_id"] = value_sha256(report)
    paper_fields = [*SCOPE_FIELDS, *LINEAGE_FIELDS, "paper_id", "title", "status", "generation_status"]
    for metric in TABLE_COLUMNS:
        paper_fields.extend([metric, f"{metric}_count", f"{metric}_applicability"])
    paper_fields.extend(["failure", "source", "review", "raw", "paper_judge_scores", "category_judge_scores"])
    summary_fields = [*SCOPE_FIELDS, *LINEAGE_FIELDS, "expected_count", "complete_count", "failed_count", "pending_count", "missing_count", "coverage", "evaluation_complete", "recorded_status", "failure", "aggregate_role", "selection_effect"]
    for metric in TABLE_COLUMNS:
        summary_fields.extend([metric, f"{metric}_count"])
    summary_fields.extend(["original_comparison", "assessment_source"])
    diagnostic_fields = [*SCOPE_FIELDS, *LINEAGE_FIELDS, "paper_id", "role", "diagnostic_scoring_protocol", "score_role", "status", "source", "diagnostic"]
    outputs = {
        "papers.csv": _csv([_flatten_paper(row) for row in papers], paper_fields),
        "variants.csv": _csv([_flatten_summary(row) for row in summaries], summary_fields),
        "role_diagnostics.csv": _csv(diagnostics, diagnostic_fields),
        "README.md": (
            "# 独立实验结果报告\n\n"
            f"报告身份：`{report['report_id']}`\n\n"
            f"范围：`{stage}`；方案/评价集合记录：{len(summaries)}；逐论文记录：{len(papers)}。\n\n"
            "- `report.json`：完整记录、原汇总与重算差异、输入来源。\n"
            "- `papers.csv`：预定清单中的每篇论文、CoCo 分项、分母、适用性与失败状态。\n"
            "- `variants.csv`：按分区、用途、运行、协议、epoch、cohort、方案分别汇总。\n"
            "- `role_diagnostics.csv`：角色诊断及其自身评价协议。\n\n"
            "`successful_subset_diagnostic` 表示评价仍含失败、待评分或缺失条目，成功子集均分仅供诊断。"
            "`not_applicable` 保留标准 CoCo 的合法不适用语义；`unknown` 表示尚无可用判定。"
            "`Paper.` 采用标准 Table-1 定义。报告保留正式候选身份与排名来源，输出为独立派生记录。\n"
        ).encode("utf-8"),
    }
    # Validate every destination before writing the first output artifact.
    outputs["report.json"] = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    for name, content in outputs.items():
        path = target / name
        if path.exists() and (path.is_file() is False or path.read_bytes() != content):
            raise FileExistsError(f"A changed report snapshot requires a new output directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        _commit_bytes(target / name, content)
    return {"report_id": report["report_id"], "output_dir": str(target), "paths": {name: str(target / name) for name in sorted(OUTPUT_NAMES)}, "variant_count": len(summaries), "paper_count": len(papers)}


__all__ = ["INPUT_PROTOCOL", "REPORT_PROTOCOL", "generate_experiment_report"]
