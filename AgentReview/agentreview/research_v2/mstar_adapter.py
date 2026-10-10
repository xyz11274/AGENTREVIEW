"""M★ reflection, local program mutation and validation for the API review host.

The explicit loop follows the source revision recorded in vendor/mstar/manifest.json:
reflection -> patch/Python extraction -> validation -> up to three repairs.
The provider and validator are injected; each completed step has an artifact.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from .io import atomic_write_json, file_sha256, value_sha256
from .mstar_prompts import MSTAR_PROTOCOL, interface_spec, reflection_prompt, repair_prompt
from .program_patch import commit_message, dependency_identity, extract_program
from .program_validation import validate_program

UPSTREAM = Path(__file__).parent / "vendor/mstar"


def _binding(path):
    return {"path": str(Path(path).resolve()), "sha256": file_sha256(path)}


def source_identity():
    return [_binding(UPSTREAM / name) for name in ("manifest.json", "LICENSE")] + dependency_identity()


class ReflectionFailure(ValueError):
    failure_category = "candidate"
    repair_exhausted = True

    def __init__(self, message, reflection=None):
        super().__init__(message)
        self.reflection = reflection


class MstarReflection:
    """Produce one validated program and retain every proposed source and error."""

    def __init__(self, *, client, model, runtime, directory, validator=None, max_fix_attempts=3):
        self.client = client
        self.model = model
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.runtime = runtime
        self.validator = validator or self._validate
        self.max_fix_attempts = max_fix_attempts
        self.interface = interface_spec(runtime)
        self.source = source_identity()
        self.calls, self.events = [], []
        self.status = "prepared"

    def prompt(self, code, score, iteration):
        return reflection_prompt(code=code, score=score, iteration=iteration, interface=self.interface)

    @staticmethod
    def _write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() != text:
            raise ValueError(f"Recorded Mstar artifact requires its original bytes: {path}")
        path.write_text(text)

    def _save(self):
        atomic_write_json(self.directory / "mstar-trace.json", {
            "protocol": MSTAR_PROTOCOL, "status": self.status,
            "adaptation": "Initial format errors, including runtime repair, share the finite repair budget; latest error is returned to the next repair.",
            "source": self.source, "max_fix_attempts": self.max_fix_attempts,
            "calls": self.calls, "events": self.events}, immutable=False)

    def _complete(self, prompt):
        messages = [{"role": "system", "content": " "}, {"role": "user", "content": prompt}]
        index = len(self.calls)
        raw = self.client.complete(messages, max_tokens=self.model.max_output_tokens,
                                   component="shared_mstar_reflection")
        path = self.directory / "responses" / f"{index:03d}.txt"
        self._write(path, raw)
        self.calls.append({"index": index, "messages_sha256": value_sha256(messages), **_binding(path)})
        self._save()
        return raw

    def _validate(self, path):
        return "\n".join(validate_program(path, require_bubblewrap=True,
            memory_runtime=self.runtime, seed_kind="evolved")) or None

    def _candidate(self, code, response):
        try:
            candidate, representation = extract_program(code, response)
        except ValueError as exc:
            self.events.append({"error_type": "Patch format error", "error": str(exc)})
            self._save()
            return code, ("Patch format error", str(exc))
        index = len(self.calls) - 1
        path = self.directory / "validation" / f"{index:03d}.py"
        self._write(path, candidate)
        diff = "".join(difflib.unified_diff(code.splitlines(True), candidate.splitlines(True),
                      fromfile="parent/program.py", tofile="candidate/program.py"))
        diff_path = path.with_suffix(".diff")
        self._write(diff_path, diff)
        error = self.validator(path)
        self.events.append({"validation_source": str(path), "program": _binding(path),
                            "diff": _binding(diff_path), "representation": representation, "error": error})
        self._save()
        return candidate, ("AgentReview compile/smoke error", error) if error else None

    def run(self, *, code, prompt, iteration, score, violation=None):
        self.status = "running"
        self.events.append({"iteration": iteration, "score": score, "operation": "repair" if violation else "reflection"})
        if violation is not None:
            prompt = repair_prompt(code=code, error_type="Runtime violation",
                                   error_details=violation, interface=self.interface)
        try:
            response = self._complete(prompt)
            change = commit_message(response)
            code, error = self._candidate(code, response)
            for _ in range(self.max_fix_attempts):
                if error is None:
                    break
                response = self._complete(repair_prompt(code=code, error_type=error[0],
                    error_details=error[1], interface=self.interface))
                repaired, next_error = self._candidate(code, response)
                code, error = repaired, next_error
            if error is None:
                self.status = "complete"
                return code, change
            self.status = "candidate_failed"
            self._save()
            raise ReflectionFailure(f"Mstar repair attempts exhausted: {error[1]}",
                                    reflection=_binding(self.directory / "mstar-trace.json"))
        except ReflectionFailure:
            raise
        except BaseException:
            self.status = "interrupted"
            raise
        finally:
            self._save()
