"""Compose independently verified original-CoCo paper receipts, never scores alone."""
from .io import atomic_write_json, read_json, value_sha256

PROTOCOL = "research-v2-composed-coco-receipt-v1"
IDENTITY_KEYS = ("partition", "run_id", "candidate_id", "evaluation_epoch", "scoring_protocol", "purpose", "review_target")


def validate_composed(receipt, *, expected_paper_ids=None, expected_epoch=None):
    from . import development_coco as coco
    if (receipt.get("protocol") != PROTOCOL or receipt.get("status") != "complete"
            or receipt.get("receipt_id") != value_sha256({k: v for k, v in receipt.items() if k != "receipt_id"})):
        raise ValueError("Composed receipt requires complete self-hashed evidence")
    paper_ids = coco._ids(receipt["paper_ids"])
    if expected_paper_ids is not None and paper_ids != expected_paper_ids:
        raise ValueError("Composed receipt differs from its frozen paper cohort")
    if expected_epoch is not None and receipt["evaluation_epoch"] != expected_epoch:
        raise ValueError("Composed receipt differs from its comparison epoch")
    children = [read_json(coco._bound_file(ref)) for ref in receipt["children"]]
    if len(children) != len(paper_ids):
        raise ValueError("Each requested paper requires one complete child receipt")
    role_reviews = {role: [] for role in receipt["candidate_identity"]["role_reviews"]}
    rows, papers, records = [], [], []
    stages = {stage: {"checks": [], "tasks": []} for stage in ("split", "classify", "evaluation")}
    for pid, child in zip(paper_ids, children):
        # Refuse nesting before recursion: no cycles or partial composition.
        if child.get("protocol") != coco.RECEIPT_PROTOCOL:
            raise ValueError("Composition requires original atomic CoCo receipts")
        coco.validate_assessment_receipt(child, expected_paper_ids=[pid], expected_epoch=receipt["evaluation_epoch"])
        if any(child[key] != receipt[key] for key in IDENTITY_KEYS):
            raise ValueError("Child scoring identity differs from its cohort")
        for key in ("references", "scoring_config"):
            if child["inputs"][key] != receipt["inputs"][key]:
                raise ValueError("Child reference or scoring configuration differs")
        identity = child["candidate_identity"]
        if ({k: v for k, v in identity.items() if k != "role_reviews"}
                != {k: v for k, v in receipt["candidate_identity"].items() if k != "role_reviews"}
                or set(identity["role_reviews"]) != set(role_reviews)):
            raise ValueError("Child program, memory or runtime differs from its cohort")
        for role, refs in identity["role_reviews"].items():
            if refs and [ref["paper_id"] for ref in refs] != [pid]:
                raise ValueError("Child role reviews cross paper boundaries")
            role_reviews[role].extend(refs)
        manifest = read_json(coco._bound_file(child["inputs"]["reviews"]))
        records.extend(manifest["records"])
        rows.extend(child["rows"])
        papers.extend(child["papers"])
        for stage, ref in child["stage_integrity"].items():
            evidence = read_json(coco._bound_file(ref))
            stages[stage]["checks"].extend(evidence["checks"])
            stages[stage]["tasks"].extend(evidence["tasks"])
    if (rows != receipt["rows"] or papers != receipt["papers"]
            or role_reviews != receipt["candidate_identity"]["role_reviews"]):
        raise ValueError("Composed evidence differs from its complete children")
    manifest, _, _ = coco._validate_inputs(coco._bound_file(receipt["inputs"]["reviews"]),
        coco._bound_file(receipt["inputs"]["references"]), receipt["partition"])
    if manifest["paper_ids"] != paper_ids or manifest["records"] != records:
        raise ValueError("Composed manifest differs from original scored final reviews")
    coco._bound_file(receipt["inputs"]["scoring_config"])
    coco.validate_stage_integrity(receipt["stage_integrity"], paper_ids)
    for stage, expected in stages.items():
        observed = read_json(coco._bound_file(receipt["stage_integrity"][stage]))
        if any(observed[key] != expected[key] for key in ("checks", "tasks")):
            raise ValueError("Composed stage evidence differs from its children")
    aggregate = coco.aggregate_paper_scores(rows)
    if (receipt["aggregate"] != aggregate or receipt["mean_score"] != aggregate["absolute_scores"]["Paper."]
            or receipt["expected_count"] != len(rows) or receipt["complete_count"] != len(rows)):
        raise ValueError("Composed aggregate differs from complete original CoCo scores")
    return receipt


def compose(*, children, review_manifest, candidate_identity, output):
    from . import development_coco as coco
    receipts = [coco.validate_assessment_receipt(coco._bound_file(ref)) for ref in children]
    first = receipts[0]
    rows = [row for receipt in receipts for row in receipt["rows"]]
    aggregate = coco.aggregate_paper_scores(rows)
    stages = {}
    for stage in ("split", "classify", "evaluation"):
        evidence = [read_json(coco._bound_file(receipt["stage_integrity"][stage])) for receipt in receipts]
        path = atomic_write_json(output / "integrity" / f"{stage}.json", {
            "status": "complete", "stage": stage, "unhandled_failures": 0,
            "checks": [check for item in evidence for check in item["checks"]],
            "tasks": [task for item in evidence for task in item["tasks"]]})
        stages[stage] = coco._binding(path)
    value = {"protocol": PROTOCOL, "status": "complete", **{k: first[k] for k in IDENTITY_KEYS},
        "children": children, "candidate_identity": candidate_identity,
        "inputs": {**first["inputs"], "reviews": coco._binding(review_manifest)},
        "paper_ids": [row["paper_id"] for row in rows], "rows": rows,
        "papers": [row for receipt in receipts for row in receipt["papers"]],
        "stage_integrity": stages, "aggregate": aggregate, "mean_score": aggregate["absolute_scores"]["Paper."],
        "expected_count": len(rows), "complete_count": len(rows)}
    value["receipt_id"] = value_sha256(value)
    validate_composed(value)
    atomic_write_json(output / "assessment.json", value)
    return value
