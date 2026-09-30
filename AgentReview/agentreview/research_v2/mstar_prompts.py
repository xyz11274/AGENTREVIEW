"""M★ reflection prompts expressed directly against the AgentReview interface."""
from __future__ import annotations

from .program_checks import ALLOWED_FIELD_TYPES, DEFAULT_IMPORT_WHITELIST

PATCH_FORMAT = """Write a diagnosis and change summary followed by a V4A program edit:

*** Commit Message
Title: <what changes and why>
- <evidence-based diagnosis>
- <memory design and instruction changes>

*** Begin Patch
*** Update File: program.py
@@ <optional context hint>
 unchanged context
-removed line
+added line
 unchanged context
*** End Patch

Place the markers on their own lines. A space prefixes context, - prefixes removal,
and + prefixes addition. Supply context around each hunk. Apply hunks and repeated
program.py updates in sequence. An empty patch represents an unchanged program.
A complete fenced Python program is the upstream full-replacement representation.
"""


def interface_spec(runtime):
    write_calls = max(0, runtime["build_llm_calls"] - 1)
    return f"""AgentReview memory program interface:

Program structure:
- KnowledgeItem and Query are dataclasses. Field types: {', '.join(sorted(ALLOWED_FIELD_TYPES))}.
- Query includes query_text and reviewer_dimension.
- KnowledgeBase defines __init__(self, toolkit), write(self, item, raw_text),
  and read(self, query) -> str. Memory helpers belong to KnowledgeBase.
- Four strings control memory interaction: INSTRUCTION_KNOWLEDGE_ITEM,
  INSTRUCTION_QUERY, INSTRUCTION_RESPONSE, ALWAYS_ON_KNOWLEDGE.
- Preserve the supplied plan_review function and its imports verbatim. Reviewer
  identities come from the frozen QUAL, CLAR, SIGN, ORIG and POL role definitions.
- Available imports: {', '.join(sorted(DEFAULT_IMPORT_WHITELIST))}.

Memory toolkit:
- llm_completion(messages, **kwargs) -> str: the configured API model;
  the host calculates output capacity from the complete request.
- embed(list[str]) -> list[list[float]]: the configured embedding model.
- create_vector_collection(name): collection.add(texts, embeddings, metadatas, ids)
  and collection.query(embedding, top_k=...) -> ranked text/id/metadata/score records.
- db: in-memory SQLite connection; chroma: the collection facade.
- count_tokens(text), memory_token_limit(purpose='context'), and
  select_memory_entries(complete_entries, purpose='context') select whole entries
  within model context. Summary inputs use purpose='summary_input'.
- log(event, payload), logger.debug(message): recorded memory diagnostics.

Execution:
- The host supplies write's {write_calls} LLM-call allowance and read's
  {runtime['read_llm_calls']} allowance. KnowledgeItem induction consumes the
  first build call; these allowances are numeric configuration values.
- Each reviewer owns an independent memory instance built from its build reviews.
- Return complete selected entries and complete model responses. Propagate
  execution errors to the host so the reflection cycle can repair the program.
- Use task-general strategies grounded in the frozen reviewer responsibilities
  and the supplied development feedback. Treat retrieved experience as guidance;
  derive current-paper claims and evidence from the current Frozen Paper.
"""


def reflection_prompt(*, code, score, iteration, interface):
    return f"""You design executable memory programs using the M★ evolution method.
Identify the causes of low development scores from complete review trajectories,
retrieval records and CoCo feedback. Improve both memory design (schemas, storage,
retrieval) and the four memory interaction instructions. Explain why each change
addresses the observed failure and generalizes across papers.

{interface}

{PATCH_FORMAT}

<current_program iteration="{iteration}">
```python
{code}
```
</current_program>
<evaluation_score>{score:.3f}</evaluation_score>

Complete AgentReview rotating feedback and lineage:
"""


def repair_prompt(*, code, error_type, error_details, interface):
    return f"""Repair the memory program using the concrete execution feedback below.
Preserve the intended memory improvement and produce a complete, executable
program edit. Align each changed expression with the supplied host interface.

{interface}

{PATCH_FORMAT}

<current_program>
```python
{code}
```
</current_program>
<execution_feedback type="{error_type}">
{error_details}
</execution_feedback>
"""
