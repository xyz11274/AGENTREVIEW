"""Concrete generation, memory, CoCo and freeze stages for shared M★ experiments."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from .config import ModelConfig, ResearchConfig
from .constants import ROLES
from .development_coco import evaluate_final_reviews, validate_evaluation_receipt, validate_assessment_receipt
from .evolution import validate_program
from .experiment_protocol import load_experiment_protocol
from .experiment_requests import PersistentCompletionClient
from .io import atomic_write_json, file_sha256, read_json, value_sha256
from .llm import OpenAICompletionClient
from .memory import build_memory_snapshot, seed_program_path, validate_memory_snapshot_artifact
from .meta import generate_final_review
from .program_checks import program_capability_manifest
from .review import run_specialist_review
from .shared_evolution import SharedEvolution


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
    paths = [root / name for name in ("shared_experiment.py", "shared_evolution.py", "experiment_requests.py",
             "memory.py", "review.py", "review_preparation.py", "meta.py", "development_coco.py",
             "development_coco_worker.py", "experiment_protocol.py", "program_checks.py", "coco_native.py", "data.py", "llm.py", "config.py", "constants.py", "coco_integrity.py")]
    paths.extend([root.parent / "reviewer_identity.py", root.parent / "paper_context.py", root.parent / "source_map.py"])
    paths.extend(sorted((root.parent / "reviewer_runtime").glob("*.py")))
    paths.extend(sorted((root.parents[1] / "memory_evolution").glob("embedding*.py")))
    return [binding(path) for path in paths]


def generation_identity():
    """Keep the frozen generator distinct from repairable scoring orchestration."""
    import ast
    from agentreview.reviewer_identity import load_reviewer_suite, load_meta_synthesizer
    root = Path(__file__).parent
    sources = [root / name for name in ("memory.py", "review.py", "review_preparation.py", "meta.py", "coco_native.py", "experiment_requests.py", "llm.py", "config.py", "constants.py")]
    sources.extend([root.parent / name for name in ("reviewer_identity.py", "paper_context.py", "source_map.py")])
    sources.extend(sorted((root.parent / "reviewer_runtime").glob("*.py")))
    sources.extend(sorted((root.parents[1] / "memory_evolution").glob("embedding*.py")))
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    backend = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LocalSharedBackend")
    methods = [node for node in backend.body if isinstance(node, ast.FunctionDef) and node.name in {"client", "embedding", "memory_runtime", "_paper", "_s2", "_bootstrap", "_candidate_check"}]
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


class LocalSharedBackend:
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
                    max_attempts=self.config["failure_policy"]["request_attempts"], input_max_tokens=self.generation["input_max_tokens"])
        try:
            yield wrapped
        finally:
            client.close()

    def embedding(self):
        if self.embedding_client is None:
            from .cli import _embedding_client
            config = self.config["embedding"]
            self.embedding_client = _embedding_client(ResearchConfig(
                embedding_provider=config["provider"], embedding_model=config["model"],
                embedding_revision=config["revision"], embedding_cache_path=config["cache_path"],
                embedding_device=config["device"]))
        config = self.config["embedding"]
        return self.embedding_client, {"provider": config["provider"], "model": config["model"],
                                      "revision": config["revision"], "cache_path": str(Path(config["cache_path"]).resolve())}

    def memory_runtime(self):
        return {"query_mode": self.generation["memory_query_mode"],
                "read_llm_calls": self.generation["memory_read_llm_calls"],
                "build_llm_calls": self.generation["memory_build_llm_calls"],
                "max_tokens": self.generation["memory_max_tokens"]}

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
        source = seed_program_path(name)
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
        with self.client(directory / "requests") as client:
            for row in self.protocol.records("build"):
                paper = self._paper(row)
                for role in ROLES:
                    run_specialist_review(paper_path=paper, role=role, program_path=bound(source["program"]),
                        snapshot_path=snapshots[role], run_id="build", output_root=directory, completion_client=client,
                        model_identity=self.model.identity(), program_version=source["candidate_id"], frozen_s2_path=s2,
                        s2_intervention=intervention, embedding_client=embedding, embedding_identity=embedding_identity,
                        require_bubblewrap=self.require_bubblewrap, memory_enabled=False, memory_runtime=self.memory_runtime(),
                        max_tokens=self.generation["review_max_tokens"])
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
                        max_tokens=self.generation["native_max_tokens"], num_fewshot=self.generation["native_num_fewshot"])
                    path = atomic_write_json(generation_dir / "native" / f"{row['paper_id']}.json", result)
                else:
                    paper_reviews = []
                    for role in ROLES:
                        run_specialist_review(paper_path=paper, role=role, program_path=bound(candidate["program"]),
                            snapshot_path=snapshots[role], run_id=key, output_root=generation_dir, completion_client=client,
                            model_identity=self.model.identity(), program_version=candidate["candidate_id"], frozen_s2_path=s2,
                            s2_intervention=intervention, embedding_client=embedding, embedding_identity=embedding_identity,
                            require_bubblewrap=self.require_bubblewrap, memory_enabled=recipe["memory_enabled"],
                            memory_runtime=self.memory_runtime(), max_tokens=self.generation["review_max_tokens"])
                        path = generation_dir / "reviews" / key / row["paper_id"] / f"{role}.json"
                        paper_reviews.append(path)
                        role_reviews[role].append({"paper_id": row["paper_id"], **binding(path)})
                    generate_final_review(paper_path=paper, review_paths=paper_reviews, mode=self.conditions["final_mode"],
                        run_id=key, output_root=generation_dir, completion_client=client, model_identity=self.model.identity(),
                        max_tokens=self.generation["meta_max_tokens"])
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

    def propose(self, *, parent, feedback, history, directory):
        directory = Path(directory)
        source = bound(parent["program"])
        packet = {"parent": {name: parent.get(name) for name in ("candidate_id", "seed", "static_score", "lineage")},
                  "history": history}
        if "assessment" in feedback:
            receipt = read_json(bound(feedback["assessment"]))
            packet["current_rotating_feedback"] = [{**row, "final_review": read_json(bound(row["review"]))} for row in receipt["rows"]]
            packet["trajectories"] = {
                role: [read_json(bound(value)) for value in reviews]
                for role, reviews in feedback["identity"]["role_reviews"].items()}
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
        atomic_write_json(directory / "reflection-input.json", prompt_packet)
        prompt = ("Evolve one shared MemoryProgram for five fixed specialist reviewers. Optimize final complete-review "
                  "standard CoCo Table-1 Paper. using this iteration's development feedback. Modify KnowledgeItem and Query "
                  "schemas, KnowledgeBase read/write logic, and the four memory instruction constants. Preserve the exact "
                  "plan_review function and its imports, the fixed host task, and role-local memory boundaries. Each revised "
                  "program builds fresh role-local knowledge bases. Keep canonical Query query_text and reviewer_dimension "
                  "fields for host planning. Toolkit model API: toolkit.llm_completion(messages, max_tokens=N), "
                  "where messages is a list of objects with role and content; the return value is text. "
                  "There is no toolkit.llm method. Other APIs include toolkit.embed(list_of_texts), "
                  "toolkit.create_vector_collection(name), and toolkit.log(event, payload). Never catch and hide "
                  "model, embedding, capability or budget failures. Keep any parsing fallback separate from "
                  "the model call. Return read results "
                  "within 6000 characters, and respect the configured memory call budgets. For evolved programs, "
                  "reserve one build LLM call for host KnowledgeItem induction; KnowledgeBase.write may use only "
                  "the remaining build_llm_calls minus one calls. Program imports and methods "
                  "must satisfy the existing sandbox interface. Return a JSON object with change (text) and program "
                  "(the complete Python source).\n\nMemory runtime: " + json.dumps(self.memory_runtime()) +
                  "\n\nParent source:\n" + source.read_text(encoding="utf-8") +
                  "\n\nDevelopment feedback:\n" + json.dumps(prompt_packet, ensure_ascii=False))
        with self.client(directory / "requests") as client:
            raw = client.complete([{"role": "user", "content": prompt}],
                    max_tokens=self.config["evolution"]["max_reflection_tokens"], component="shared_mstar_reflection",
                    response_format={"type": "json_object"})
        atomic_write_json(directory / "response.json", {"raw_model_output": raw})
        try:
            parsed = json.loads(raw)
            code, change = parsed["program"], parsed["change"]
            if isinstance(code, str) and isinstance(change, str) and code.strip() and change.strip():
                pass
            else:
                raise ValueError("Reflection requires complete source and a change description")
        except (ValueError, TypeError, KeyError) as exc:
            code, change = "", f"Reflection contract failure: {exc}"
        target = directory / "program.py"
        self._write_program(target, code)
        return {"program": binding(target), "program_sha256": file_sha256(target), "seed_kind": "evolved",
                "change": change, "seed": parent["seed"], "parent_id": parent["candidate_id"]}

    def freeze(self, winner, state):
        assets = {key: result for key, result in state["results"].items()
                  if state["recipes"][key]["split"] == "static" and result["status"] == "complete"}
        value = {"protocol": "research-v2-shared-frozen-system-v1", "status": "complete",
                 "experiment_protocol_sha256": self.protocol.protocol_sha256, "winner": winner,
                 "recipes": {key: state["recipes"][key] for key in assets}, "assessments": assets,
                 "runtime": runtime_identity(), "generation_identity": generation_identity(), "scoring_config": self.config["scoring_config"],
                 "promotion_policy": {"path": str(self.root / "failure-policy.json"), "evaluation_epoch": winner["evaluation_epoch"]}}
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
    backend = LocalSharedBackend(protocol=protocol, output_root=output_root)
    return SharedEvolution(protocol=protocol, output_root=output_root, backend=backend).run(repair_evidence=repair_evidence)


def run_shared_final_test(*, protocol_path, output_root, frozen_system_path, repair_evidence=None):
    """Evaluate frozen variants on the sealed cohort; all test feedback stays here."""
    import fcntl
    from .failure_policy import FailureController, FailureLimits, EvolutionBudgetExhausted, EvolutionPaused, failure_detail, validate_promotion_policy
    protocol = load_experiment_protocol(protocol_path)
    frozen = read_json(frozen_system_path)
    body = {key: value for key, value in frozen.items() if key != "freeze_id"}
    if (frozen.get("protocol") == "research-v2-shared-frozen-system-v1" and frozen.get("status") == "complete"
            and frozen.get("freeze_id") == value_sha256(body) and frozen["experiment_protocol_sha256"] == protocol.protocol_sha256
            and frozen["generation_identity"] == generation_identity()):
        pass
    else:
        raise ValueError("Final test requires the exact complete frozen shared system and runtime")
    promotion_policy = read_json(frozen["promotion_policy"]["path"])
    validate_promotion_policy(promotion_policy)
    if promotion_policy["evaluation_epoch"] != frozen["promotion_policy"]["evaluation_epoch"]:
        raise ValueError("Final test requires the current verified evaluation epoch")
    backend = LocalSharedBackend(protocol=protocol, output_root=output_root)
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
