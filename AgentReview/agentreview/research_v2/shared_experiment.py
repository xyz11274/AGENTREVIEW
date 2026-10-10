"""Concrete generation, memory, CoCo and freeze stages for shared M★ experiments."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED, FIRST_EXCEPTION
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from .config import ModelConfig, ResearchConfig
from .constants import ROLES
from .development_coco import evaluate_final_reviews, validate_evaluation_receipt, validate_assessment_receipt
from .program_validation import validate_program
from .experiment_protocol import load_experiment_protocol
from .experiment_requests import PersistentCompletionClient
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import OpenAICompletionClient
from .memory import build_memory_snapshot, seed_program_path, validate_memory_snapshot_artifact
from .meta import generate_final_review
from .program_checks import program_capability_manifest
from .review import run_specialist_review
from .shared_evolution import SharedEvolution
from .execution_telemetry import timed


class CandidateContractError(ValueError):
    failure_category = "candidate"


def binding(path):
    source = Path(path).resolve()
    return {"path": str(source), "sha256": file_sha256(source)}


def bound(value):
    path = Path(value["path"]).resolve()
    if file_sha256(path) == value["sha256"]:
        return path
    raise ValueError(f"Frozen artifact identity differs: {path}")


def runtime_identity():
    root = Path(__file__).parent
    paths = sorted(root.glob("*.py"))
    paths.extend([root.parent / "reviewer_identity.py", root.parent / "paper_context.py", root.parent / "source_map.py"])
    paths.extend(sorted((root.parent / "reviewer_runtime").glob("*.py")))
    paths.extend(sorted((root.parents[1] / "memory_evolution").glob("embedding*.py")))
    paths.extend(sorted((root / "vendor/deepseek_v41").glob("*.*")))
    paths.extend(sorted((root / "vendor/mstar").glob("*")))
    from .mstar_adapter import source_identity
    identity = {str(path.resolve()): binding(path) for path in paths if path.is_file()}
    identity.update({item["path"]: item for item in source_identity()})
    return [identity[path] for path in sorted(identity)]


def generation_identity():
    """Keep the frozen generator distinct from repairable scoring orchestration."""
    import ast
    from agentreview.reviewer_identity import load_reviewer_suite, load_meta_synthesizer
    root = Path(__file__).parent
    sources = [root / name for name in ("memory.py", "review.py", "review_preparation.py", "meta.py", "coco_native.py", "experiment_requests.py", "llm.py", "streaming.py", "config.py", "token_accounting.py", "retry.py", "embedding.py", "local_service.py", "constants.py", "request_admission.py", "runtime_limits.py", "execution_telemetry.py")]
    sources.extend(root / name for name in ("io.py", "models.py", "s2.py", "data.py", "program_checks.py", "program_validation.py", "paper_pipeline.py", "embedding_remote.py"))
    sources.extend([root.parent / name for name in ("reviewer_identity.py", "paper_context.py", "source_map.py")])
    sources.extend(sorted((root.parent / "reviewer_runtime").glob("*.py")))
    sources.extend(sorted((root.parents[1] / "memory_evolution").glob("embedding*.py")))
    sources.extend(sorted((root / "vendor/deepseek_v41").glob("*.*")))
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    backend = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "APISharedBackend")
    methods = [node for node in backend.body if isinstance(node, ast.FunctionDef) and node.name in {"client", "embedding", "memory_runtime", "_paper", "_s2", "_bootstrap", "_candidate_check", "_generate_paper", "_assess_streaming", "_assess_coupled", "_assess_pipeline"}]
    # The generation section of assess ends when the score manifest is committed.
    assess = next(node for node in backend.body if isinstance(node, ast.FunctionDef) and node.name == "assess")
    generation_nodes = []
    for node in assess.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "manifest" for target in node.targets):
            break
        generation_nodes.append(node)
    normalized = [ast.dump(node, include_attributes=False) for node in [*methods, *generation_nodes]]
    return {"sources": [binding(path) for path in sources], "host_generation_ast_sha256": value_sha256(normalized),
            "roles": {role: load_reviewer_suite().identity(role) for role in ROLES},
            "meta": load_meta_synthesizer().identity()}


class APISharedBackend:
    def __init__(self, *, protocol, output_root):
        self.protocol = protocol
        self.config = protocol.document
        self.root = Path(output_root).resolve()
        self.generation = self.config["generation"]
        self.conditions = self.config["non_memory_conditions"]
        self.model = ModelConfig(**self.config["model"])
        self.scoring = read_json(bound(self.config["scoring_config"]))
        self.embedding_client = None
        self.require_bubblewrap = True
        self.frozen_snapshots = {}
        self.frozen_system = None
        self.execution = None
        if self.config.get("execution_profile"):
            ref = self.config["execution_profile"]
            path = bound(ref)
            os.environ["AGENTREVIEW_EXECUTION_PROFILE"] = str(path)
            os.environ["AGENTREVIEW_EXECUTION_PROFILE_SHA256"] = ref["sha256"]
            from .request_admission import profile
            self.execution = profile()

    def preflight(self):
        from .development_coco import _validate_config
        # Reload and validate all development assets, using only sealed identities.
        current = load_experiment_protocol(self.protocol.path)
        if current.protocol_sha256 != self.protocol.protocol_sha256:
            raise ValueError("Preflight requires the frozen experiment protocol")
        _validate_config(self.scoring)
        self.protocol.freeze(self.root / "protocol.json")
        policy = program_capability_manifest(path=bound(self.conditions["review_policy_program"]))
        for name in ("vector_search", "llm_summarizer", "experience_learner"):
            if program_capability_manifest(path=seed_program_path(name))["policy_ast_sha256"] == policy["policy_ast_sha256"]:
                continue
            raise ValueError("Initial seeds require the same frozen non-memory review policy")
        for split in ("static", "rotating"):
            refs = self.protocol.reference_assets(split, purpose={"static": "selection", "rotating": "reflection"}[split])
            first = refs[0]
            if all(ref == first for ref in refs):
                reference = read_json(bound(first))
                if reference["partition"] == split and set(reference["paper_ids"]) == {row["paper_id"] for row in self.protocol.records(split)}:
                    continue
            raise ValueError("Each evaluation split requires a matching partition-local CoCo reference manifest")

    @contextmanager
    def client(self, directory):
        client = OpenAICompletionClient(self.model)
        wrapped = PersistentCompletionClient(client, directory, identity=self.model.identity(),
                    max_attempts=self.config["failure_policy"]["request_attempts"])
        try:
            yield wrapped
        finally:
            client.close()

    @contextmanager
    def reflection_client(self, directory):
        # The upstream reflector requests high reasoning for mutation and fixes.
        model = replace(self.model, enable_thinking=True, reasoning_effort="high")
        client = OpenAICompletionClient(model)
        try:
            yield PersistentCompletionClient(client, directory, identity=model.identity(),
                        max_attempts=self.config["failure_policy"]["request_attempts"])
        finally:
            client.close()

    def embedding(self):
        if self.embedding_client is None:
            from .embedding import embedding_client
            config = self.config["embedding"]
            self.embedding_client = embedding_client(ResearchConfig(
                embedding_provider=config["provider"], embedding_model=config["model"],
                embedding_revision=config["revision"], embedding_cache_path=config["cache_path"],
                embedding_device=config["device"]), remote_service=config.get("remote_service"))
        config = self.config["embedding"]
        identity = {"provider": config["provider"], "model": config["model"],
                    "revision": config["revision"], "cache_path": str(Path(config["cache_path"]).resolve())}
        if config.get("remote_service") is not None:
            identity.update(compute_identity=self.embedding_client.embedding_spec,
                            compute_fingerprint=self.embedding_client.embedding_fingerprint)
        return self.embedding_client, identity

    def memory_runtime(self):
        return {"query_mode": self.generation["memory_query_mode"],
                "read_llm_calls": self.generation["memory_read_llm_calls"],
                "build_llm_calls": self.generation["memory_build_llm_calls"],
                "max_tokens": self.model.max_output_tokens,
                "context_max_tokens": self.model.max_model_len - 1,
                "summary_input_max_tokens": self.model.max_model_len - 1,
                "tokenizer": self.model.identity()["token_accounting"]["tokenizer"]}


    def _write_program(self, path, code):
        path.parent.mkdir(parents=True, exist_ok=True)
        # JSON is the immutable source record; the executable view is checked on replay.
        atomic_write_json(path.with_suffix(".source.json"), {"code": code})
        if path.is_file():
            if path.read_text(encoding="utf-8") == code:
                return
            raise ValueError("Candidate program source differs from its immutable record")
        with path.open("x", encoding="utf-8") as handle:
            handle.write(code)

    def seed(self, name):
        frozen_seeds = self.config.get("initial_memory_programs")
        source = bound(frozen_seeds[name]) if frozen_seeds is not None else seed_program_path(name)
        target = self.root / "programs" / f"seed-{name}" / "program.py"
        self._write_program(target, source.read_text(encoding="utf-8"))
        return {"candidate_id": f"seed-{name}", "seed": name, "seed_kind": name, "parent_id": None,
                "program": binding(target), "program_sha256": file_sha256(target),
                "lineage": [], "revision": 0, "round": 0}

    def native_identity(self):
        return {"candidate_id": "coco-native", "seed": None, "seed_kind": "coco_native",
                "parent_id": None, "program_sha256": None, "lineage": [], "round": 0}

    def _paper(self, row):
        raw = read_json(bound(row["assets"]["paper"]))
        if raw["paper_id"] != row["paper_id"] or raw.get("source", {}).get("source_partition") != row["source_partition"]:
            raise ValueError("Paper input requires its frozen split identity")
        source = dict(raw.get("source", {}))
        source["paper_context_max_chars"] = self.generation["paper_context_max_chars"]
        value = {**raw, "source": source}
        path = self.root / "inputs" / row["source_partition"] / f"{value_sha256(value)}.json"
        return atomic_write_json(path, value)

    def _s2(self, split):
        if self.conditions["s2_mode"] == "off":
            return None, {"protocol": "s2-intervention-v1", "mode": "forced_off", "label": "shared-frozen-off-v1"}
        return bound(self.conditions["frozen_s2"][split]), None

    @timed('experience_build')
    def _bootstrap(self, epoch):
        source = self.seed("vector_search")
        directory = self.root / "bootstrap" / f"e{epoch}"
        embedding, embedding_identity = self.embedding()
        snapshots = {}
        for role in ROLES:
            path = directory / "empty" / f"{role}.json"
            build_memory_snapshot(role=role, program_path=bound(source["program"]), program_version=source["candidate_id"],
                review_paths=[], evaluation_paths=[], output_path=path, seed_kind="vector_search", embedding_client=embedding,
                embedding_identity=embedding_identity, require_bubblewrap=self.require_bubblewrap, memory_runtime=self.memory_runtime())
            snapshots[role] = path
        paths = {role: [] for role in ROLES}
        s2, intervention = self._s2("build")
        rows = self.protocol.records("build")
        def generate_build_paper(row):
            from .execution_telemetry import span
            with span("build_paper", paper_id=row["paper_id"]), self.client(directory / "paper-requests" / value_sha256(row["paper_id"])) as client:
                paper = self._paper(row)
                for role in ROLES:
                    run_specialist_review(paper_path=paper, role=role, program_path=bound(source["program"]),
                        snapshot_path=snapshots[role], run_id="build", output_root=directory, completion_client=client,
                        model_identity=self.model.identity(), program_version=source["candidate_id"], frozen_s2_path=s2,
                        s2_intervention=intervention, embedding_client=embedding, embedding_identity=embedding_identity,
                        require_bubblewrap=self.require_bubblewrap, memory_enabled=False, memory_runtime=self.memory_runtime(),
                        max_tokens=self.model.max_output_tokens)
            return row["paper_id"]
        workers = self.execution["paper_workers"] if self.execution else 1
        completed = []
        pending_rows = iter(rows)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending = {executor.submit(generate_build_paper, row): row["paper_id"]
                for row in [next(pending_rows, None) for _ in range(workers)] if row is not None}
            try:
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        pending.pop(future)
                        completed.append(future.result())
                    atomic_write_json(directory / "progress.json", {"status": "running" if pending or len(completed) < len(rows) else "complete",
                        "completed_papers": completed, "expected_papers": len(rows), "complete_role_reviews": 5 * len(completed)}, immutable=False)
                    for _ in done:
                        row = next(pending_rows, None)
                        if row is not None:
                            pending[executor.submit(generate_build_paper, row)] = row["paper_id"]
            except BaseException as exc:
                for future in pending:
                    future.cancel()
                atomic_write_json(directory / "failure.json", {"status": "paused", "completed_papers": completed,
                    "error": str(exc), "zero_imputation": False}, immutable=False)
                raise
        for row in rows:
            for role in ROLES:
                paths[role].append(directory / "reviews" / "build" / row["paper_id"] / f"{role}.json")
        return paths

    def _candidate_check(self, candidate):
        source = bound(candidate["program"])
        errors = validate_program(source, require_bubblewrap=self.require_bubblewrap,
                                  memory_runtime=self.memory_runtime(), seed_kind=candidate["seed_kind"])
        if errors:
            raise CandidateContractError("; ".join(errors))
        actual = program_capability_manifest(path=source)
        expected = program_capability_manifest(path=bound(self.conditions["review_policy_program"]))
        if actual["policy_ast_sha256"] == expected["policy_ast_sha256"]:
            return
        raise CandidateContractError("Memory evolution preserves the frozen non-memory review policy")

    @timed('candidate_evaluation', 'epoch')
    def assess(self, recipe, *, epoch):
        candidate = recipe["candidate"]
        key, split = recipe["key"], recipe["split"]
        if split == "sealed_test" and self.frozen_system is None:
            raise ValueError("Final test requires a verified frozen system")
        directory = self.root / "assessments" / f"e{epoch}" / key / f"r{candidate.get('revision', 0)}"
        directory.mkdir(parents=True, exist_ok=True)
        generation_dir = self.root / "frozen-test-generation" / key if split == "sealed_test" else directory
        atomic_write_json(directory / "recipe.json", recipe)
        by_id = {row["paper_id"]: row for row in self.protocol.records(split)}
        rows = [by_id[paper_id] for paper_id in recipe["paper_ids"]]
        snapshots = {}
        embedding = embedding_identity = None
        if recipe["kind"] != "coco_native":
            self._candidate_check(candidate)
            embedding, embedding_identity = self.embedding()
            frozen = self.frozen_snapshots.get(candidate["candidate_id"])
            if frozen is not None:
                snapshots = {role: bound(frozen[role]) for role in ROLES}
            else:
                sources = self._bootstrap(epoch) if recipe["memory_enabled"] else {role: [] for role in ROLES}
                memory_dir = self.root / "memory" / f"e{epoch}" / candidate["candidate_id"] / f"r{candidate.get('revision', 0)}" / ("enabled" if recipe["memory_enabled"] else "empty")
                with self.client(memory_dir / "requests") as client:
                    for role in ROLES:
                        path = memory_dir / f"{role}.json"
                        build_memory_snapshot(role=role, program_path=bound(candidate["program"]),
                            program_version=candidate["candidate_id"], review_paths=sources[role], evaluation_paths=[],
                            output_path=path, seed_kind=candidate["seed_kind"], embedding_client=embedding,
                            embedding_identity=embedding_identity, completion_client=client, completion_identity=self.model.identity(),
                            require_bubblewrap=self.require_bubblewrap, memory_runtime=self.memory_runtime())
                        snapshots[role] = path
        if self.execution:
            return self._assess_streaming(recipe=recipe, epoch=epoch, directory=directory,
                generation_dir=generation_dir, rows=rows, snapshots=snapshots,
                embedding=embedding, embedding_identity=embedding_identity)
        s2, intervention = self._s2(split)
        reviews = []
        role_reviews = {role: [] for role in ROLES}
        with self.client(generation_dir / "review-requests") as client:
            for row in rows:
                paper = self._paper(row)
                if recipe["kind"] == "coco_native":
                    from .coco_native import generate_native_review
                    result = generate_native_review(paper_path=paper, coco_root=self.scoring["coco_root"],
                        run_id=key, output_root=generation_dir / "native-raw" / value_sha256(row["paper_id"]), completion_client=client, model_identity=self.model.identity(),
                        max_tokens=self.model.max_output_tokens, num_fewshot=self.generation["native_num_fewshot"])
                    path = atomic_write_json(generation_dir / "native" / f"{row['paper_id']}.json", result)
                else:
                    paper_reviews = []
                    for role in ROLES:
                        run_specialist_review(paper_path=paper, role=role, program_path=bound(candidate["program"]),
                            snapshot_path=snapshots[role], run_id=key, output_root=generation_dir, completion_client=client,
                            model_identity=self.model.identity(), program_version=candidate["candidate_id"], frozen_s2_path=s2,
                            s2_intervention=intervention, embedding_client=embedding, embedding_identity=embedding_identity,
                            require_bubblewrap=self.require_bubblewrap, memory_enabled=recipe["memory_enabled"],
                            memory_runtime=self.memory_runtime(), max_tokens=self.model.max_output_tokens)
                        path = generation_dir / "reviews" / key / row["paper_id"] / f"{role}.json"
                        paper_reviews.append(path)
                        role_reviews[role].append({"paper_id": row["paper_id"], **binding(path)})
                    generate_final_review(paper_path=paper, review_paths=paper_reviews, mode=self.conditions["final_mode"],
                        run_id=key, output_root=generation_dir, completion_client=client, model_identity=self.model.identity(),
                        max_tokens=self.model.max_output_tokens)
                    path = generation_dir / "final_reviews" / key / f"{row['paper_id']}.json"
                reviews.append({"paper_id": row["paper_id"], **binding(path)})
        identity = {"experiment_protocol_sha256": self.protocol.protocol_sha256, "candidate": candidate,
                    "snapshots": {role: binding(path) for role, path in snapshots.items()},
                    "memory_enabled": recipe["memory_enabled"], "non_memory_conditions": self.conditions,
                    "runtime": runtime_identity(), "generation_identity": generation_identity(), "role_reviews": role_reviews}
        manifest = atomic_write_json(directory / "reviews.json", {"protocol": "research-v2-coco-review-manifest-v1",
            "partition": split, "paper_ids": recipe["paper_ids"], "records": reviews})
        refs = rows[0]["assets"]["coco_reference"]
        if all(row["assets"]["coco_reference"] == refs for row in rows):
            reference = bound(refs)
        else:
            raise ValueError("Evaluation cohort requires one partition-local reference manifest")
        receipt = evaluate_final_reviews(review_manifest_path=manifest, reference_manifest_path=reference,
            scoring_config_path=bound(self.config["scoring_config"]), output_root=directory / "coco", partition=split,
            run_id=self.config["experiment_id"], candidate_id=key, evaluation_epoch=epoch, candidate_identity=identity,
            purpose="final_test" if split == "sealed_test" else "development")
        return {"status": "complete", "evaluation_epoch": epoch, "static_score": receipt["mean_score"],
                "assessment": binding(directory / "coco" / "assessment.json"), "identity": identity,
                "role_diagnostics": {}}

    def _generate_paper(self, *, recipe, row, generation_dir, snapshots, embedding, embedding_identity):
        candidate, key = recipe["candidate"], recipe["key"]
        paper = self._paper(row)
        s2, intervention = self._s2(recipe["split"])
        role_reviews = {role: [] for role in ROLES}
        request_dir = generation_dir / "paper-requests" / value_sha256(row["paper_id"])
        if recipe["kind"] == "coco_native":
            with self.client(request_dir) as client:
                from .coco_native import generate_native_review
                result = generate_native_review(paper_path=paper, coco_root=self.scoring["coco_root"],
                    run_id=key, output_root=generation_dir / "native-raw" / value_sha256(row["paper_id"]),
                    completion_client=client, model_identity=self.model.identity(),
                    max_tokens=self.model.max_output_tokens, num_fewshot=self.generation["native_num_fewshot"])
                path = atomic_write_json(generation_dir / "native" / f"{row['paper_id']}.json", result)
        else:
            def run_role(role):
                # Client, mutable completion metadata, Toolkit and sandbox are
                # role-local. Only immutable snapshot inputs/encoder are shared.
                with self.client(request_dir / role) as client:
                    run_specialist_review(paper_path=paper, role=role, program_path=bound(candidate["program"]),
                        snapshot_path=snapshots[role], run_id=key, output_root=generation_dir, completion_client=client,
                        model_identity=self.model.identity(), program_version=candidate["candidate_id"], frozen_s2_path=s2,
                        s2_intervention=intervention, embedding_client=embedding, embedding_identity=embedding_identity,
                        require_bubblewrap=self.require_bubblewrap, memory_enabled=recipe["memory_enabled"],
                        memory_runtime=self.memory_runtime(), max_tokens=self.model.max_output_tokens)
                return generation_dir / "reviews" / key / row["paper_id"] / f"{role}.json"
            workers = (self.execution or {}).get("role_workers", 2 if self.execution is None else 1)
            with ThreadPoolExecutor(max_workers=min(workers, len(ROLES))) as executor:
                futures = {role: executor.submit(run_role, role) for role in ROLES}
                try:
                    done, _ = wait(list(futures.values()), return_when=FIRST_EXCEPTION)
                    for future in done:
                        future.result()
                    paths = [futures[role].result() for role in ROLES]
                except BaseException:
                    for future in futures.values():
                        future.cancel()
                    raise
            for role, path in zip(ROLES, paths, strict=True):
                role_reviews[role].append({"paper_id": row["paper_id"], **binding(path)})
            with self.client(request_dir / "meta") as client:
                generate_final_review(paper_path=paper, review_paths=paths, mode=self.conditions["final_mode"],
                    run_id=key, output_root=generation_dir, completion_client=client, model_identity=self.model.identity(),
                    max_tokens=self.model.max_output_tokens)
                path = generation_dir / "final_reviews" / key / f"{row['paper_id']}.json"
        return {"paper_id": row["paper_id"], **binding(path)}, role_reviews

    def _assess_streaming(self, *, recipe, epoch, directory, generation_dir, rows, snapshots,
                          embedding, embedding_identity):
        kwargs = dict(recipe=recipe, epoch=epoch, directory=directory, generation_dir=generation_dir,
                      rows=rows, snapshots=snapshots, embedding=embedding, embedding_identity=embedding_identity)
        if self.execution["protocol"] == "shared-api-execution-v3":
            return self._assess_pipeline(**kwargs)
        return self._assess_coupled(**kwargs)

    def _assess_pipeline(self, *, recipe, epoch, directory, generation_dir, rows, snapshots,
                         embedding, embedding_identity):
        from .cohort_receipt import compose
        from .paper_pipeline import PaperPipeline
        refs = rows[0]["assets"]["coco_reference"]
        if any(row["assets"]["coco_reference"] != refs for row in rows):
            raise ValueError("Evaluation cohort requires one partition-local reference manifest")
        reference = bound(refs)
        base_identity = {"experiment_protocol_sha256": self.protocol.protocol_sha256, "candidate": recipe["candidate"],
            "snapshots": {role: binding(path) for role, path in snapshots.items()},
            "memory_enabled": recipe["memory_enabled"], "non_memory_conditions": self.conditions,
            "runtime": runtime_identity(), "generation_identity": generation_identity()}
        by_id = {row["paper_id"]: row for row in rows}
        def generate(pid):
            part = directory / "coco" / "parts" / value_sha256(pid)
            review, roles = self._generate_paper(recipe=recipe, row=by_id[pid], generation_dir=generation_dir,
                snapshots=snapshots, embedding=embedding, embedding_identity=embedding_identity)
            identity = {**base_identity, "role_reviews": roles}
            manifest = atomic_write_json(part / "reviews.json", {"protocol": "research-v2-coco-review-manifest-v1",
                "partition": recipe["split"], "paper_ids": [pid], "records": [review]})
            return {"review": review, "roles": roles, "identity": identity, "manifest": binding(manifest)}
        def verify_ticket(pid, payload):
            review = read_json(bound(payload["review"]))
            if review.get("paper_id") != pid or not str(review.get("review_text") or "").strip():
                raise ValueError("Handoff requires a complete final review for the exact paper")
            roles = payload["roles"]
            if payload["identity"] != {**base_identity, "role_reviews": roles}:
                raise ValueError("Handoff candidate/memory/runtime identity mismatch")
            bound(recipe["candidate"]["program"]) if recipe["kind"] != "coco_native" else None
            if recipe["kind"] != "coco_native":
                ids = []
                for role in ROLES:
                    if len(roles.get(role, [])) != 1 or roles[role][0]["paper_id"] != pid:
                        raise ValueError("Handoff requires each role for the exact paper")
                    value = read_json(bound(roles[role][0]))
                    snapshot = read_json(bound(base_identity["snapshots"][role]))
                    if (value["role"] != role or value["paper_id"] != pid or
                            value["program_version"] != recipe["candidate"]["candidate_id"] or
                            value["memory_snapshot_id"] != snapshot["snapshot_id"] or
                            value["model_identity"] != self.model.identity() or
                            not value["review_text"].strip()):
                        raise ValueError("Role handoff provenance differs from candidate snapshot")
                    ids.append(value["review_id"])
                if review["source_review_ids"] != ids or review["mode"] != self.conditions["final_mode"]:
                    raise ValueError("Meta handoff lost the original ordered five-role inputs")
            manifest = read_json(bound(payload["manifest"]))
            if manifest != {"protocol": "research-v2-coco-review-manifest-v1", "partition": recipe["split"],
                            "paper_ids": [pid], "records": [payload["review"]]}:
                raise ValueError("Handoff review manifest identity mismatch")
        def verify_result(pid, payload, value):
            if value["review"] != payload["review"] or value["roles"] != payload["roles"]:
                raise ValueError("Score result is associated with another final review")
            receipt = validate_assessment_receipt(bound(value["assessment"]), expected_paper_ids=[pid],
                candidate_identity=payload["identity"], expected_epoch=epoch)
            if receipt["candidate_id"] != recipe["key"] or receipt["partition"] != recipe["split"]:
                raise ValueError("Score result is associated with another candidate or partition")
        def score(pid, payload):
            part = directory / "coco" / "parts" / value_sha256(pid)
            evaluate_final_reviews(review_manifest_path=bound(payload["manifest"]), reference_manifest_path=reference,
                scoring_config_path=bound(self.config["scoring_config"]), output_root=part, partition=recipe["split"],
                run_id=self.config["experiment_id"], candidate_id=recipe["key"], evaluation_epoch=epoch,
                candidate_identity=payload["identity"], purpose="final_test" if recipe["split"] == "sealed_test" else "development")
            return {"review": payload["review"], "roles": payload["roles"], "assessment": binding(part / "assessment.json")}
        flow = PaperPipeline(directory / "pipeline", identity={"candidate": base_identity, "epoch": epoch,
            "reference": refs, "scoring": self.config["scoring_config"], "recipe": recipe},
            task_ids=recipe["paper_ids"], review_workers=self.execution["review_paper_workers"],
            score_workers=self.execution["coco_paper_workers"], ready_capacity=self.execution["review_ready_capacity"])
        results = flow.run(generate=generate, score=score, verify_ticket=verify_ticket, verify_result=verify_result)
        ordered = [results[pid] for pid in recipe["paper_ids"]]
        identity = {**base_identity, "role_reviews": {role: [ref for value in ordered for ref in value["roles"][role]] for role in ROLES}}
        manifest = atomic_write_json(directory / "reviews.json", {"protocol": "research-v2-coco-review-manifest-v1",
            "partition": recipe["split"], "paper_ids": recipe["paper_ids"], "records": [value["review"] for value in ordered]})
        receipt = compose(children=[value["assessment"] for value in ordered], review_manifest=manifest,
                          candidate_identity=identity, output=directory / "coco")
        return {"status": "complete", "evaluation_epoch": epoch, "static_score": receipt["mean_score"],
                "assessment": binding(directory / "coco" / "assessment.json"), "identity": identity, "role_diagnostics": {}}

    def _assess_coupled(self, *, recipe, epoch, directory, generation_dir, rows, snapshots,
                        embedding, embedding_identity):
        from .cohort_receipt import compose
        from .execution_telemetry import span
        refs = rows[0]["assets"]["coco_reference"]
        if any(row["assets"]["coco_reference"] != refs for row in rows):
            raise ValueError("Evaluation cohort requires one partition-local reference manifest")
        reference = bound(refs)
        base_identity = {"experiment_protocol_sha256": self.protocol.protocol_sha256, "candidate": recipe["candidate"],
            "snapshots": {role: binding(path) for role, path in snapshots.items()},
            "memory_enabled": recipe["memory_enabled"], "non_memory_conditions": self.conditions,
            "runtime": runtime_identity(), "generation_identity": generation_identity()}
        def evaluate_paper(row):
            pid = row["paper_id"]
            part = directory / "coco" / "parts" / value_sha256(pid)
            with span("paper_full_flow", paper_id=pid, candidate=recipe["key"]):
                review, roles = self._generate_paper(recipe=recipe, row=row, generation_dir=generation_dir,
                    snapshots=snapshots, embedding=embedding, embedding_identity=embedding_identity)
                identity = {**base_identity, "role_reviews": roles}
                manifest = atomic_write_json(part / "reviews.json", {"protocol": "research-v2-coco-review-manifest-v1",
                    "partition": recipe["split"], "paper_ids": [pid], "records": [review]})
                evaluate_final_reviews(review_manifest_path=manifest, reference_manifest_path=reference,
                    scoring_config_path=bound(self.config["scoring_config"]), output_root=part, partition=recipe["split"],
                    run_id=self.config["experiment_id"], candidate_id=recipe["key"], evaluation_epoch=epoch,
                    candidate_identity=identity, purpose="final_test" if recipe["split"] == "sealed_test" else "development")
                validate_assessment_receipt(part / "assessment.json", expected_paper_ids=[pid],
                                            candidate_identity=identity, expected_epoch=epoch)
                return review, roles, binding(part / "assessment.json")
        results = {}
        pending_rows = iter(rows)
        with ThreadPoolExecutor(max_workers=self.execution["paper_workers"]) as executor:
            pending = {executor.submit(evaluate_paper, row): row["paper_id"]
                       for row in [next(pending_rows, None) for _ in range(self.execution["paper_workers"])] if row is not None}
            try:
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        results[pending.pop(future)] = future.result()
                    for _ in done:
                        row = next(pending_rows, None)
                        if row is not None:
                            pending[executor.submit(evaluate_paper, row)] = row["paper_id"]
            except BaseException as exc:
                for future in pending:
                    future.cancel()
                atomic_write_json(directory / "pipeline.failure.json", {"status": "failed", "error": str(exc),
                    "completed_papers": sorted(results), "admitted": False}, immutable=False)
                raise
        ordered = [results[pid] for pid in recipe["paper_ids"]]
        identity = {**base_identity, "role_reviews": {role: [ref for _, roles, _ in ordered for ref in roles[role]] for role in ROLES}}
        manifest = atomic_write_json(directory / "reviews.json", {"protocol": "research-v2-coco-review-manifest-v1",
            "partition": recipe["split"], "paper_ids": recipe["paper_ids"], "records": [review for review, _, _ in ordered]})
        receipt = compose(children=[ref for _, _, ref in ordered], review_manifest=manifest,
                          candidate_identity=identity, output=directory / "coco")
        return {"status": "complete", "evaluation_epoch": epoch, "static_score": receipt["mean_score"],
                "assessment": binding(directory / "coco" / "assessment.json"), "identity": identity, "role_diagnostics": {}}

    def validate(self, result, *, recipe, epoch, generation_only=False):
        receipt = validate_assessment_receipt(read_json(bound(result["assessment"])),
                         expected_paper_ids=recipe["paper_ids"], expected_epoch=epoch)
        identity = result["identity"]
        if (receipt["candidate_identity"] == identity and identity["candidate"] == recipe["candidate"]
                and receipt["partition"] == recipe["split"] and receipt["candidate_id"] == recipe["key"]
                and identity["memory_enabled"] == recipe["memory_enabled"]
                and identity["experiment_protocol_sha256"] == self.protocol.protocol_sha256
                and result["static_score"] == receipt["mean_score"]):
            pass
        else:
            raise ValueError("Assessment identity differs from its shared candidate recipe")
        if generation_only:
            if identity["generation_identity"] != generation_identity():
                raise ValueError("Final test requires the frozen generation identity")
        else:
            if identity["runtime"] != runtime_identity():
                raise ValueError("Candidate comparison requires the current shared runtime identity")
            for value in identity["runtime"]:
                bound(value)
        for role, value in identity["snapshots"].items():
            snapshot = validate_memory_snapshot_artifact(read_json(bound(value)), path=value["path"])
            if snapshot["role"] == role and snapshot["program_sha256"] == recipe["candidate"]["program_sha256"]:
                continue
            raise ValueError("Candidate requires five separate role-local snapshots of its shared program")
        if recipe["kind"] != "coco_native" and set(identity["snapshots"]) != set(ROLES):
            raise ValueError("Shared candidate requires all five role snapshots")
        if recipe["kind"] != "coco_native" and (
                set(identity["role_reviews"]) != set(ROLES) or any(
                    [value["paper_id"] for value in identity["role_reviews"][role]] != recipe["paper_ids"]
                    for role in ROLES)):
            raise ValueError("Shared candidate requires five complete ordered role-review cohorts")
        for role, reviews in identity["role_reviews"].items():
            for value in reviews:
                review = read_json(bound(value))
                snapshot = read_json(bound(identity["snapshots"][role]))
                if (review["role"] != role or review["paper_id"] != value["paper_id"]
                        or review["program_version"] != recipe["candidate"]["candidate_id"]
                        or review["memory_snapshot_id"] != snapshot["snapshot_id"]):
                    raise ValueError("Role review requires its original paper, shared program and role snapshot")
        if result.get("role_diagnostics"):
            raise ValueError("Shared memory selection scores only the final complete review")
        return receipt

    def reflection_scores(self, feedback):
        receipt = validate_assessment_receipt(bound(feedback["assessment"]), expected_partition="rotating")
        return [{"paper_id": row["paper_id"], "score": row["metrics"]["Paper."]} for row in receipt["rows"]]

    @timed('reflection', 'directory')
    def propose(self, *, parent, feedback, history, directory):
        directory = Path(directory)
        source = bound(parent["program"])
        packet = {"parent": {name: parent.get(name) for name in ("candidate_id", "seed", "static_score", "lineage")},
                  "history": history}
        if "assessment" in feedback:
            receipt = read_json(bound(feedback["assessment"]))
            validate_assessment_receipt(receipt)
            if receipt.get("partition") != "rotating" or receipt.get("purpose") != "development":
                raise ValueError("Reflection accepts complete rotating development receipts only")
            packet["current_rotating_feedback"] = [{**row, "final_review": read_json(bound(row["review"]))} for row in receipt["rows"]]
            packet["trajectories"] = {
                role: [read_json(bound(value)) for value in reviews]
                for role, reviews in feedback["identity"]["role_reviews"].items()}
            if "reflection_case_ids" in feedback:
                packet["reflection_case_ids"] = feedback["reflection_case_ids"]
                packet["reflection_selection"] = feedback["reflection_selection"]
        else:
            packet["contract_failure"] = feedback
        atomic_write_json(directory / "feedback.json", packet)
        # Keep full artifacts above, but do not duplicate raw text and rendered text
        # (or request metadata) in the reflection context. No review is truncated.
        prompt_packet = {"parent": packet["parent"], "history": packet["history"]}
        if "current_rotating_feedback" in packet:
            prompt_packet["current_rotating_feedback"] = [
                {"paper_id": row["paper_id"], "metrics": row["metrics"],
                 "paper_judge_scores": row["paper_judge_scores"],
                 "final_review": row["final_review"]["review_text"], "review_artifact": row["review"]}
                for row in packet["current_rotating_feedback"]]
            prompt_packet["trajectories"] = {role: [
                {name: review[name] for name in ("paper_id", "role", "review_text", "retrieval_provenance")}
                for review in reviews] for role, reviews in packet["trajectories"].items()}
        else:
            prompt_packet["contract_failure"] = feedback
        if "reflection_case_ids" in packet:
            chosen = packet["reflection_case_ids"]
            expected_count = self.config["evolution"]["detailed_reflection_cases"]
            if len(chosen) != expected_count or len(set(chosen)) != expected_count:
                raise ValueError("Reflection requires the frozen number of distinct detailed cases")
            prompt_packet["required_case_ids"] = chosen
            prompt_packet["rotating_overview"] = [
                {key: row[key] for key in ("paper_id", "metrics", "paper_judge_scores", "review_artifact")}
                for row in prompt_packet["current_rotating_feedback"]]
            if not set(chosen) <= {row["paper_id"] for row in prompt_packet["rotating_overview"]}:
                raise ValueError("Detailed cases must belong to this complete rotating evaluation")
            prompt_packet["current_rotating_feedback"] = [row for row in prompt_packet["current_rotating_feedback"] if row["paper_id"] in chosen]
            prompt_packet["trajectories"] = {role: [row for row in reviews if row["paper_id"] in chosen]
                for role, reviews in prompt_packet["trajectories"].items()}
        from .mstar_adapter import MstarReflection
        def validate_mutation(path):
            try:
                self._candidate_check({**parent, "program": binding(path), "seed_kind": "evolved"})
            except CandidateContractError as exc:
                return str(exc)
            return None
        with self.reflection_client(directory / "requests") as client:
            runner = MstarReflection(client=client, model=self.model, runtime=self.memory_runtime(),
                                     directory=directory, validator=validate_mutation)
            code = source.read_text(encoding="utf-8")
            score = float(feedback.get("static_score", parent.get("static_score") or 0))
            iteration = int(parent.get("round", 0)) + 1
            prefix = runner.prompt(code, score, iteration)
            from .reflection_budget import pack_reflection_feedback
            prompt, prompt_packet, budget = pack_reflection_feedback(prompt_packet, prefix=prefix,
                count_chat_tokens=client.count_chat_tokens, input_limit=self.model.max_model_len - 1,
                output_budget=self.model.max_output_tokens)
            atomic_write_json(directory / "reflection-input.json", prompt_packet)
            budget["source_feedback"] = binding(directory / "feedback.json")
            atomic_write_json(directory / "reflection-budget.json", budget)
            violation = feedback.get("contract_failure")
            code, change = runner.run(code=code, prompt=prompt, iteration=iteration, score=score,
                                      violation=json.dumps(violation, ensure_ascii=False) if violation is not None else None)
        target = directory / "program.py"
        self._write_program(target, code)
        return {"program": binding(target), "program_sha256": file_sha256(target), "seed_kind": "evolved",
                "reflection": binding(directory / "mstar-trace.json"),
                "change": change, "seed": parent["seed"], "parent_id": parent["candidate_id"]}

    def freeze(self, winner, state):
        from .development_coco import _model_identity
        for model in [self.scoring["bert_score_model"], *(self.scoring["services"][stage]["model_identity"] for stage in ("split", "classify"))]:
            if _model_identity(model["root"], force_rehash=True) != model:
                raise ValueError("Frozen model bytes changed before Champion freeze")
        assets = {key: result for key, result in state["results"].items()
                  if state["recipes"][key]["split"] == "static" and result["status"] == "complete"}
        value = {"protocol": "research-v2-shared-frozen-system-v1", "status": "complete",
                 "experiment_protocol_sha256": self.protocol.protocol_sha256, "winner": winner,
                 "recipes": {key: state["recipes"][key] for key in assets}, "assessments": assets,
                 "runtime": runtime_identity(), "generation_identity": generation_identity(), "scoring_config": self.config["scoring_config"],
                 "promotion_policy": {**binding(self.root / "failure-policy.json"), "evaluation_epoch": winner["evaluation_epoch"]}}
        return {**value, "freeze_id": value_sha256(value)}

    def write_report_input(self, state):
        scopes = {}
        records = [{"recipe": state.get("result_recipes", {}).get(key, recipe),
                    "result": state["results"].get(key, {})} for key, recipe in state["recipes"].items()]
        records.extend(state.get("assessment_history", []))
        winner = state.get("winner")
        if winner and "winner" in winner:
            winner_key = winner["winner"]["key"]
            records.append({"recipe": state["recipes"][winner_key], "result": state["results"][winner_key], "champion": True})
        policy_path = self.root / "failure-policy.json"
        live_epoch = read_json(policy_path)["evaluation_epoch"] if policy_path.is_file() else 0
        for record in records:
            recipe, result = record["recipe"], record["result"]
            key = recipe["key"]
            epoch = result.get("evaluation_epoch", live_epoch)
            split = recipe["split"]
            assessment = result.get("assessment")
            # A scoring failure already persisted the incomplete raw assessment.
            if assessment is None:
                candidate = recipe["candidate"]
                path = self.root / "assessments" / f"e{epoch}" / key / f"r{candidate.get('revision', 0)}" / "coco" / "assessment.json"
                if path.is_file():
                    assessment = binding(path)
            scoring_identity = read_json(bound(assessment))["scoring_protocol"] if assessment else {
                "protocol": "research-v2-standard-coco-v1", "config_sha256": self.config["scoring_config"]["sha256"],
                "standard_source_sha256": value_sha256(self.scoring["source_files"])}
            scope_id = (split, epoch, tuple(recipe["paper_ids"]), value_sha256(scoring_identity))
            titles = {row["paper_id"]: row["title"] for row in self.protocol.records(split)}
            scope = scopes.setdefault(scope_id, {"partition": split, "purpose": {"static": "selection", "rotating": "reflection", "sealed_test": "final_test"}[split],
                "run_id": self.config["experiment_id"], "scoring_protocol": scoring_identity, "evaluation_epoch": epoch,
                "cohort_id": value_sha256(recipe["paper_ids"]), "papers": [{"paper_id": pid, "title": titles[pid]} for pid in recipe["paper_ids"]], "variants": []})
            candidate = recipe["candidate"]
            role_diagnostics = []
            for role, ref in result.get("role_diagnostics", {}).items():
                diagnostic = read_json(bound(ref))
                role_diagnostics.extend({"paper_id": item["paper_id"], "role": role,
                    "scoring_protocol": diagnostic["scoring_protocol"], "artifact": item} for item in diagnostic["papers"])
            directory = self.root / "assessments" / f"e{epoch}" / key / f"r{candidate.get('revision', 0)}"
            generated = self.root / "frozen-test-generation" / key if split == "sealed_test" else directory
            paper_outcomes = []
            for pid in recipe["paper_ids"]:
                review_path = generated / "native" / f"{pid}.json" if recipe["kind"] == "coco_native" else generated / "final_reviews" / key / f"{pid}.json"
                outcome = {"paper_id": pid, "status": "not_generated"}
                if review_path.is_file():
                    outcome.update(status="not_scored", review=binding(review_path))
                else:
                    native_raw = generated / "native-raw" / value_sha256(pid) / "raw.json"
                    if recipe["kind"] == "coco_native" and native_raw.is_file():
                        native = read_json(native_raw)
                        if native.get("status") == "failed":
                            outcome.update(status="failed", failure=native["failure"])
                    failures = list((generated / "reviews" / key / pid / "failures").glob("*/*.json"))
                    failures.extend((generated / "final_reviews" / key).glob(f"{pid}.failure.json"))
                    if failures:
                        outcome.update(status="failed", failure={"stage": "generation", "artifacts": [binding(path) for path in failures]})
                paper_outcomes.append(outcome)
            scope["variants"].append({"variant_id": "champion" if record.get("champion") else key,
                "kind": "champion" if record.get("champion") else "candidate" if recipe["kind"] == "parent_feedback" else recipe["kind"],
                "seed": candidate["seed"], "matched_seed": candidate["seed"] if recipe["kind"] == "no_memory" else None,
                "candidate_id": key, "parent_id": candidate.get("parent_id"), "round": candidate.get("round"),
                "program_sha256": candidate.get("program_sha256"), "assessment": assessment, "role_diagnostics": role_diagnostics,
                "paper_outcomes": paper_outcomes, "failure": result.get("failure"), "status": result.get("status", "pending")})
        atomic_write_json(self.root / "report-input.json", {"protocol": "research-v2-experiment-report-input-v1", "scopes": list(scopes.values())}, immutable=False)


def run_shared_experiment(*, protocol_path, output_root, repair_evidence=None):
    protocol = load_experiment_protocol(protocol_path)
    backend = APISharedBackend(protocol=protocol, output_root=output_root)
    return SharedEvolution(protocol=protocol, output_root=output_root, backend=backend).run(repair_evidence=repair_evidence)


def validate_study_freeze(protocol):
    """No member of a multi-search study may open final evaluation early."""
    study = protocol.document.get("study")
    if study is None:
        return
    gate = read_json(study["freeze_manifest"])
    expected = study["random_seeds"]
    if gate.get("protocol") != "three-search-freeze-v1" or gate.get("status") != "all_frozen" or gate.get("random_seeds") != expected:
        raise ValueError("Final evaluation requires all prespecified searches frozen")
    systems = gate.get("systems", [])
    if len(systems) != len(expected) or {row["seed"] for row in systems} != set(expected):
        raise ValueError("Final evaluation cannot omit a search Champion")
    current = generation_identity()
    if gate.get("generation_identity") != current:
        raise ValueError("All-search freeze requires the current frozen generator")
    members = []
    for member in systems:
        other = load_experiment_protocol(bound(member["protocol"]))
        frozen = read_json(bound(member["frozen_system"]))
        body = {key: value for key, value in frozen.items() if key != "freeze_id"}
        if (frozen.get("status") != "complete" or frozen.get("freeze_id") != value_sha256(body)
                or frozen.get("experiment_protocol_sha256") != other.protocol_sha256
                or frozen.get("generation_identity") != current
                or other.document["evolution"]["random_seed"] != member["seed"]
                or other.document["splits"] != protocol.document["splits"]):
            raise ValueError("All-search gate requires each actual frozen system and the same cohort")
        members.append(other.protocol_sha256)
    if protocol.protocol_sha256 not in members:
        raise ValueError("This run is not a member of the frozen study")


def run_shared_final_test(*, protocol_path, output_root, frozen_system_path, repair_evidence=None):
    """Evaluate frozen variants on the sealed cohort; all test feedback stays here."""
    import fcntl
    from .failure_policy import FailureController, FailureLimits, EvolutionBudgetExhausted, EvolutionPaused, failure_detail, validate_frozen_policy
    protocol = load_experiment_protocol(protocol_path)
    validate_study_freeze(protocol)
    frozen = read_json(frozen_system_path)
    body = {key: value for key, value in frozen.items() if key != "freeze_id"}
    if (frozen.get("protocol") == "research-v2-shared-frozen-system-v1" and frozen.get("status") == "complete"
            and frozen.get("freeze_id") == value_sha256(body) and frozen["experiment_protocol_sha256"] == protocol.protocol_sha256
            and frozen["generation_identity"] == generation_identity()):
        pass
    else:
        raise ValueError("Final test requires the exact complete frozen shared system and runtime")
    try:
        promotion_policy = read_json(bound(frozen["promotion_policy"]) if "sha256" in frozen["promotion_policy"] else frozen["promotion_policy"]["path"])
    except ValueError as exc:
        raise ValueError("Promotion policy differs from the current verified freeze") from exc
    validate_frozen_policy(promotion_policy)
    if promotion_policy["evaluation_epoch"] != frozen["promotion_policy"]["evaluation_epoch"]:
        raise ValueError("Final test requires the current verified evaluation epoch")
    backend = APISharedBackend(protocol=protocol, output_root=output_root)
    backend.frozen_system = binding(frozen_system_path)
    for key, result in frozen["assessments"].items():
        recipe = frozen["recipes"][key]
        backend.validate(result, recipe=recipe, epoch=result["evaluation_epoch"], generation_only=True)
        if recipe["memory_enabled"]:
            backend.frozen_snapshots[recipe["candidate"]["candidate_id"]] = result["identity"]["snapshots"]
    # All system checks precede opening the sealed reference or paper assets.
    protocol.paper_paths("sealed_test", purpose="final_test", frozen_system_sha256=frozen["freeze_id"])
    protocol.validate_final_test_assets(frozen_system_sha256=frozen["freeze_id"])
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "search.json").exists():
        raise ValueError("Final test requires its own output directory")
    with (root / "final-test.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state_path = root / "final-test.json"
        state = read_json(state_path) if state_path.is_file() else {"status": "running", "freeze_id": frozen["freeze_id"], "recipes": {}, "results": {}}
        if state["freeze_id"] != frozen["freeze_id"]:
            raise ValueError("Final test resume requires its original freeze identity")
        budgets = protocol.document["failure_policy"]
        controller = FailureController(root / "failure-policy.json", limits=FailureLimits(
            **{key: budgets[key] for key in ("candidate_repairs", "round_repairs", "candidate_failures")}))
        paper_ids = [row["paper_id"] for row in protocol.records("sealed_test")]
        selected = [key for key, recipe in frozen["recipes"].items() if recipe["kind"] in {"seed", "no_memory", "coco_native"}]
        for key in selected:
            state["recipes"].setdefault(key, {**frozen["recipes"][key], "split": "sealed_test", "paper_ids": paper_ids})
        winner = frozen["winner"]
        winner_recipe = frozen["recipes"][winner["key"]]
        state["recipes"].setdefault("champion", {**winner_recipe, "key": "champion", "kind": "champion", "split": "sealed_test", "paper_ids": paper_ids})
        atomic_write_json(state_path, state, immutable=False)
        try:
            if repair_evidence:
                controller.begin_recovery(repair_evidence_path=repair_evidence)
            recovering = controller.value["status"] == "reevaluating"
            if recovering is False:
                controller.require_running()
            epoch = controller.value["evaluation_epoch"]
            for key, recipe in state["recipes"].items():
                result = state["results"].get(key)
                if result and result["evaluation_epoch"] == epoch:
                    backend.validate(result, recipe=recipe, epoch=epoch)
                else:
                    state["results"][key] = backend.assess(recipe, epoch=epoch)
                    atomic_write_json(state_path, state, immutable=False)
            if recovering:
                controller.finish_recovery(completed_candidates=list(state["recipes"]))
            controller.require_running()
            state["status"] = "complete"
        except EvolutionBudgetExhausted as exc:
            controller.end(str(exc))
            state.update(status="ended", end_reason=str(exc))
        except EvolutionPaused:
            state["status"] = controller.value["status"]
        except Exception as exc:
            detail = failure_detail(exc, phase="final_test")
            controller.pause(operation_id=f"test:e{controller.value['evaluation_epoch']}:{detail['fingerprint']}",
                             detail=detail, affected=list(state["recipes"]))
            state.update(status=controller.value["status"], failure=detail)
        atomic_write_json(state_path, state, immutable=False)
        try:
            backend.write_report_input(state)
        except Exception as exc:
            atomic_write_json(root / "report-index.failure.json", {"error": str(exc)}, immutable=False)
        return state
