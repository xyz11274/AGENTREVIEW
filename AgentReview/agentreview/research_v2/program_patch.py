"""Apply M★ program edits as ordered, atomic operations on one virtual file."""
from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path

from codex_apply_patch import apply_patch_in_memory
import codex_apply_patch

from .io import file_sha256

DEPENDENCY_VERSION = "0.4.0"
VIRTUAL_FILE = "program.py"


def dependency_identity():
    version = metadata.version("codex-apply-patch")
    if version != DEPENDENCY_VERSION:
        raise ValueError(f"Program editing requires codex-apply-patch=={DEPENDENCY_VERSION}; installed={version}")
    root = Path(codex_apply_patch.__file__).parent
    return [{"path": str(path.resolve()), "sha256": file_sha256(path)}
            for path in sorted(root.iterdir()) if path.suffix in {".py", ".so", ".pyd"}]


def apply_program_patch(source: str, patch_body: str) -> str:
    """Apply each file operation to the result of its predecessor.

    The native dependency returns a file map. Repeated updates in a single call
    replace that map entry, so sequential calls implement the ordered edit
    semantics. The caller receives the final string after every operation succeeds.
    """
    operations = []
    for line in patch_body.splitlines(keepends=True):
        if line.startswith("*** ") and line.rstrip() != "*** End of File":
            if line.rstrip() != f"*** Update File: {VIRTUAL_FILE}":
                raise ValueError("Program edits use *** Update File: program.py with contextual hunks")
            operations.append([line])
        elif operations:
            operations[-1].append(line)
        elif line.strip():
            raise ValueError("A program edit starts with *** Update File: program.py")
    result = source
    for index, operation in enumerate(operations, 1):
        text = "*** Begin Patch\n" + "".join(operation).rstrip("\n") + "\n*** End Patch\n"
        try:
            result = apply_patch_in_memory(text, {VIRTUAL_FILE: result}).files[VIRTUAL_FILE]
        except (ValueError, RuntimeError, KeyError) as exc:
            hints = [line.strip() for line in operation if line.startswith("@@")]
            detail = str(exc).strip() or "hunk context did not match the current source"
            raise ValueError(f"operation {index} on {VIRTUAL_FILE}: {type(exc).__name__}: "
                             f"{detail}; context={hints[:3]}") from exc
    return result


def extract_program(source: str, response: str) -> tuple[str, str]:
    """Use the last M★ patch, with the upstream Python-fence replacement path."""
    patches = re.findall(r"\*\*\* Begin Patch\n(.*?)\*\*\* End Patch", response, re.DOTALL)
    error = ValueError("Supply a complete *** Begin Patch / *** End Patch block")
    if patches:
        try:
            return apply_program_patch(source, patches[-1]), "patch"
        except (ValueError, RuntimeError, KeyError) as exc:
            error = ValueError(str(exc))
    full_code = re.findall(r"```python\n(.*?)```", response, re.DOTALL)
    if full_code:
        return full_code[-1].strip(), "python"
    raise error


def commit_message(response: str) -> str:
    match = re.search(r"\*\*\* Commit Message\n(.*?)(?=\n\*\*\*|\Z)", response, re.DOTALL)
    if match:
        return "\n".join(line.strip() for line in match[1].strip().splitlines())
    return "Mstar memory program update"
