"""Rebuild and reevaluate an affected population before resuming selection."""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .constants import ROLES
from .evolution import EvolutionPool, PoolEntry, build_evaluation_receipt, validate_pool_evaluation_receipts
from .failure_policy import EvolutionBudgetExhausted, controller_for_run, failure_detail
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .memory import SEED_NAMES, build_memory_snapshot, seed_program_path
from .program_checks import program_capability_manifest
from .review_preparation import ReviewPlanError


def affected_population(output_root: str | Path, run_id: str) -> dict[str, list[dict[str, Any]]]:
    """Capture admitted candidates and successful proposals awaiting evaluation."""
    output = Path(output_root).resolve()
    population = {}
    for role in ROLES:
        root = output / "evolution" / role / run_id
        pool_path = root / "pool.json"
        if pool_path.is_file():
            entries = list(read_json(pool_path)["entries"])
        elif (root / "run-spec.json").is_file():
            entries = [asdict(PoolEntry(
                candidate_id=f"seed-{name}", role=role,
                program_path=str(seed_program_path(name).resolve()),
                program_sha256=file_sha256(seed_program_path(name)), parent_id=None,
                static_score=None, seed_kind=name,
                capability_manifest=program_capability_manifest(path=seed_program_path(name)),
            )) for name in SEED_NAMES]
        else:
            continue
        by_id = {entry["candidate_id"]: entry for entry in entries}
        for path in sorted(root.glob("attempt-[0-9][0-9][0-9].json")):
            outcome = read_json(path)
            attempt = int(outcome["attempt"])
            failed = root / f"attempt-{attempt:03d}.candidate-failure.json"
            if outcome.get("status") == "proposed" and outcome["candidate_id"] not in by_id and failed.is_file() is False:
                from .formal_evolution import _effective_attempt
                active_path, outcome = _effective_attempt(root, attempt)
                parent = by_id[outcome["parent_id"]]
                by_id[outcome["candidate_id"]] = {
                    **asdict(PoolEntry(
                        candidate_id=outcome["candidate_id"], role=role,
                        program_path=outcome["program_path"], program_sha256=outcome["program_sha256"],
                        parent_id=outcome["parent_id"], static_score=None,
                        seed_kind=parent["seed_kind"], capability_manifest=outcome["capability_manifest"],
                        lineage=[*parent["lineage"], {"attempt": attempt, "attempt_path": str(active_path)}],
                    )),
                    "pending_attempt": attempt,
                }
        population[role] = list(by_id.values())
    return population


def population_ids(population: dict[str, list[dict[str, Any]]]) -> list[str]:
    return sorted(f"{role}:{entry['candidate_id']}" for role, entries in population.items() for entry in entries)


def recover_formal_round(
    *, output_root: str | Path, run_id: str, repair_evidence_path: str | Path,
    canonical_evaluation_summary_path: str | Path, canonical_split_path: str | Path,
    static_split_path: str | Path, rotating_split_path: str | Path,
    frozen_s2_path: str | Path, completion_client: Any, model_identity: dict[str, Any],
    embedding_client: Any, embedding_identity: dict[str, Any], max_workers: int,
    require_bubblewrap: bool, progress: Any = None,
) -> None:
    from .formal_evolution import (
        BoundedCompletionClient, _evaluation_metadata,
        _exclusive_lock, _preflight_formal_role_run, _run_evaluation_batch,
        _run_review_batch, _state, _write_state, validate_canonical_evaluation_summary,
        _repair_runtime_candidate, _effective_attempt, record_candidate_failure,
    )

    output = Path(output_root).resolve()
    controller = controller_for_run(output, run_id)
    with _exclusive_lock(output / "runs" / "formal-evolution-runner.lock"):
        repaired_inventory = (
            population_ids(affected_population(output, run_id))
            if controller.value.get("population_capture_failed") else None
        )
        epoch = controller.begin_recovery(repair_evidence_path=repair_evidence_path, affected_candidates=repaired_inventory)
        recovery_root = output / "runs" / run_id / f"recovery-e{epoch:03d}"
        population_path = recovery_root / "population.json"
        if population_path.is_file():
            population = read_json(population_path)
        else:
            population = affected_population(output, run_id)
            atomic_write_json(population_path, population)
        ids = population_ids(population)
        if ids != sorted(controller.value["pending_candidates"]):
            raise ValueError("Recovery population must match the paused candidate set")
        limited = BoundedCompletionClient(completion_client, max_workers)
        prepared = {}
        resolutions = {}

        def discard_candidate(role, root, entry, attempt, phase, error):
            terminal = record_candidate_failure(
                work_path=root / f"attempt-{attempt:03d}.work.json", pool_path=root / "pool.json",
                output_root=output, phase=phase, error=error,
            )
            resolutions[f"{role}:{entry.candidate_id}"] = terminal
            if controller_for_run(output, run_id).value["status"] == "ended":
                raise EvolutionBudgetExhausted("Candidate failure budget exhausted during recovery")

        for role, entries in population.items():
            root = output / "evolution" / role / run_id
            # Preserve old evidence before rebinding repaired shared inputs.
            for name in ("run-spec.json", "winner.json", "pool.json", "coordinator.json"):
                source = root / name
                archive = recovery_root / role / "before" / name
                if source.is_file() and archive.is_file() is False:
                    atomic_write_json(archive, read_json(source))
            # Preflight writes the repaired spec into an epoch-specific artifact.
            _, canonical_reviews, canonical_evaluations, records = _preflight_formal_role_run(
                role=role, run_id=run_id, pool_source=root / "pool.json",
                canonical_evaluation_summary_path=canonical_evaluation_summary_path,
                canonical_split_path=canonical_split_path, static_split_path=static_split_path,
                rotating_split_path=rotating_split_path, frozen_s2_path=frozen_s2_path,
                output_root=output, model_identity=model_identity,
                embedding_identity=embedding_identity, require_bubblewrap=require_bubblewrap,
                evaluation_epoch=epoch,
            )
            pool = EvolutionPool(role)
            for raw_entry in entries:
                entry = PoolEntry(**{key: value for key, value in raw_entry.items() if key != "pending_attempt"})
                attempt = raw_entry.get("pending_attempt")
                if attempt is not None:
                    repair_request = root / f"attempt-{attempt:03d}.runtime-repair-request.json"
                    repair_result = root / f"attempt-{attempt:03d}.runtime-repair.json"
                    failure_path = root / f"attempt-{attempt:03d}.candidate-failure.json"
                    if failure_path.is_file():
                        failure = read_json(failure_path)
                        discard_candidate(role, root, entry, attempt, failure["phase"], ReviewPlanError(failure["error"]))
                        continue
                    if repair_request.is_file() and repair_result.is_file() is False:
                        request = read_json(repair_request)
                        repaired = _repair_runtime_candidate(
                            root=root, attempt=attempt, error=RuntimeError(request["error"]),
                            phase=request["phase"], controller=controller,
                            completion_client=limited, completion_identity=model_identity,
                            require_bubblewrap=require_bubblewrap,
                        )
                        if repaired is False:
                            discard_candidate(role, root, entry, attempt, request["phase"],
                                              ReviewPlanError("Candidate runtime repair budget completed with an invalid Program"))
                            continue
                    if repair_result.is_file():
                        outcome = read_json(repair_result)
                        if outcome.get("status") == "failed":
                            discard_candidate(role, root, entry, attempt, outcome["runtime_repair"]["phase"],
                                              ReviewPlanError("Candidate runtime repair failed: " + str(outcome.get("errors", []))))
                            continue
                        _, outcome = _effective_attempt(root, attempt)
                        entry = replace(entry, program_path=outcome["program_path"],
                                        program_sha256=outcome["program_sha256"],
                                        capability_manifest=outcome["capability_manifest"])
                while True:
                    phase = "candidate_memory_build"
                    try:
                        if file_sha256(entry.program_path) != entry.program_sha256:
                            raise ValueError("Recovery requires the frozen candidate Program source")
                        version = f"{run_id}-{role.lower()}-{entry.candidate_id}-recovery-e{epoch:03d}-{entry.program_sha256[:12]}"
                        snapshot = output / "memories" / role / version / "snapshot.json"
                        phase = "candidate_memory_build"
                        build_memory_snapshot(
                            role=role, program_path=entry.program_path, program_version=version,
                            review_paths=canonical_reviews, evaluation_paths=canonical_evaluations,
                            output_path=snapshot, seed_kind=entry.seed_kind,
                            embedding_client=embedding_client, embedding_identity=embedding_identity,
                            completion_client=limited if entry.seed_kind == "experience_learner" else None,
                            completion_identity=model_identity if entry.seed_kind == "experience_learner" else None,
                            require_bubblewrap=require_bubblewrap,
                        )
                        phase = "candidate_static_review"
                        reviews = _run_review_batch(
                            records=records, role=role, program_path=entry.program_path,
                            snapshot_path=snapshot, program_version=version, run_id=version + "-review",
                            output_root=output, completion_client=limited, model_identity=model_identity,
                            frozen_s2_path=frozen_s2_path, embedding_client=embedding_client,
                            embedding_identity=embedding_identity, max_workers=max_workers,
                            require_bubblewrap=require_bubblewrap, progress=progress,
                        )
                        phase = "candidate_static_evaluation"
                        evaluations = _run_evaluation_batch(
                            records=records, review_paths=reviews, run_id=version + "-eval", output_root=output,
                            completion_client=limited, evaluator_identity=model_identity,
                            max_workers=max_workers, progress=progress,
                        )
                        receipt = build_evaluation_receipt(
                            role=role, snapshot_path=snapshot, review_paths=reviews,
                            evaluation_paths=evaluations, epoch=epoch,
                            metadata=_evaluation_metadata(static_split_path),
                        )
                        if set(receipt["paper_ids"]) != {item["paper_id"] for item in records}:
                            raise ValueError("Recovery receipt requires the full static cohort")
                        updated = replace(entry, static_score=receipt["mean_score"],
                            memory_snapshot_path=str(snapshot), memory_snapshot_sha256=file_sha256(snapshot),
                            memory_snapshot_id=read_json(snapshot)["snapshot_id"], evaluation_receipt=receipt)
                        pool.add(updated)
                        resolutions[f"{role}:{entry.candidate_id}"] = {"status": "complete", "receipt_id": receipt["receipt_id"]}
                        break
                    except Exception as exc:
                        if attempt is not None and failure_detail(exc, phase=phase)["category"] == "candidate":
                            repaired = _repair_runtime_candidate(
                                root=root, attempt=attempt, error=exc, phase=phase, controller=controller,
                                completion_client=limited, completion_identity=model_identity,
                                require_bubblewrap=require_bubblewrap,
                            )
                            if repaired:
                                _, outcome = _effective_attempt(root, attempt)
                                entry = replace(entry, program_path=outcome["program_path"],
                                                program_sha256=outcome["program_sha256"],
                                                capability_manifest=outcome["capability_manifest"])
                                continue
                            discard_candidate(role, root, entry, attempt, phase, exc)
                            break
                        raise
            validate_pool_evaluation_receipts(pool, expected_epoch=epoch)
            prepared[role] = pool
            atomic_write_json(recovery_root / role / "pool.json", pool.to_dict())
        if set(resolutions) != set(ids):
            raise ValueError("Recovery requires a completed disposition for every affected candidate")
        atomic_write_json(recovery_root / "resolutions.json", resolutions)
        # Keep the shared gate closed during all pool writes, including crash replay.
        for name in ("repair_budget", "candidate_failure_budget"):
            budget = controller_for_run(output, run_id).value[name]
            if budget["used"] >= budget["limit"]:
                controller.end(name)
                raise EvolutionBudgetExhausted("Recovery exhausted the round budget; prior pools and Champion retained")
        champions = {role: asdict(pool.best(require_snapshot=True)) for role, pool in prepared.items()}
        for role, pool in prepared.items():
            root = output / "evolution" / role / run_id
            pool.failure_policy_path = str(controller.path)
            atomic_write_json(root / "pool.json", pool.to_dict(), immutable=False)
            epoch_spec = root / f"run-spec.e{epoch:03d}.json"
            atomic_write_json(root / "run-spec.json", read_json(epoch_spec), immutable=False)
            state = _state(root, role=role, run_id=run_id)
            by_id = {item.candidate_id: item for item in pool.entries}
            for path in sorted(root.glob("attempt-[0-9][0-9][0-9].json")):
                outcome = read_json(path)
                attempt = outcome["attempt"]
                entry = by_id.get(outcome.get("candidate_id"))
                if outcome.get("status") == "proposed" and entry is not None:
                    state["attempts"][f"{attempt:03d}"] = {
                        "status": "committed", "attempt_path": str(root / f"attempt-{attempt:03d}.json"),
                        "candidate": asdict(entry), "recovery_epoch": epoch,
                    }
            for terminal in state["attempts"].values():
                if terminal.get("status") == "committed":
                    candidate_id = terminal["candidate"]["candidate_id"]
                    terminal["candidate"] = asdict(next(item for item in pool.entries if item.candidate_id == candidate_id))
                    terminal["recovery_epoch"] = epoch
            for path in sorted(root.glob("attempt-[0-9][0-9][0-9].work.json")):
                work = read_json(path)
                attempt = work["attempt"]
                terminal = state["attempts"].get(f"{attempt:03d}", {})
                if terminal.get("status") in {"committed", "failed", "candidate_failed"}:
                    continue
                archive = recovery_root / role / "before" / path.name
                if archive.is_file() is False:
                    atomic_write_json(archive, work)
                parent = by_id[work["parent"]["candidate_id"]]
                work["parent"] = {
                    "candidate_id": parent.candidate_id, "program_path": parent.program_path,
                    "program_sha256": parent.program_sha256,
                    "program_version": read_json(parent.memory_snapshot_path)["program_version"],
                    "memory_snapshot_path": parent.memory_snapshot_path,
                    "memory_snapshot_sha256": parent.memory_snapshot_sha256,
                    "memory_snapshot_id": parent.memory_snapshot_id, "seed_kind": parent.seed_kind,
                }
                work["pool_before"]["sha256"] = file_sha256(root / "pool.json")
                work["canonical_evaluation"] = validate_canonical_evaluation_summary(canonical_evaluation_summary_path)
                for key, source in (("static_split", static_split_path), ("rotating_split", rotating_split_path)):
                    source = Path(source).resolve()
                    split = read_json(source)
                    work[key] = {"path": str(source), "sha256": file_sha256(source), "split_sha256": split.get("split_sha256")}
                    if key == "static_split":
                        work[key]["paper_ids"] = [item["paper_id"] for item in split["records"]]
                records = read_json(rotating_split_path)["records"][(attempt - 1) * 5 : attempt * 5]
                work["rotating_batch"] = {
                    "schedule": "frozen-record-order-disjoint-5-v1",
                    "paper_ids": [item["paper_id"] for item in records],
                    "paper_paths": [item["path"] for item in records],
                    "records_sha256": value_sha256(records),
                }
                work["work_id"] = value_sha256({key: value for key, value in work.items() if key != "work_id"})
                atomic_write_json(path, work, immutable=False)
            state["status"] = "running"
            state.pop("winner_id", None)
            _write_state(root, state)
        controller.finish_recovery(completed_candidates=sorted(resolutions), champions=champions)
        for role in prepared:
            root = output / "evolution" / role / run_id
            state = _state(root, role=role, run_id=run_id)
            state["status"] = "running"
            _write_state(root, state)
