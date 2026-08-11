"""Standalone, read-only Gradio viewer for AgentReview result artifacts."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, List

from agentreview.viewer_data import (
    SPECIALIST_REVIEWERS,
    VIEWABLE_ARTIFACTS,
    evolution_version_detail,
    load_evolution,
    load_sample,
    raw_artifact_text,
    scan_output_dir,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="View existing AgentReview JSON outputs without running reviews."
    )
    parser.add_argument(
        "--output_dir",
        default="outputs",
        help="Output root containing single_paper_review/ (default: outputs).",
    )
    parser.add_argument(
        "--evolution_dir",
        default=None,
        help="ReviewHarness evolution root containing manifest.json; defaults to --output_dir.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Viewer bind address.")
    parser.add_argument("--port", type=int, default=7861, help="Viewer port.")
    parser.add_argument(
        "--share", action="store_true", help="Ask Gradio to create a share link."
    )
    return parser.parse_args()


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _inline_code(value: Any) -> str:
    text = str(value or "—").replace("`", "\\`")
    return f"`{text}`"


def _review_text(value: str, role_name: str) -> str:
    if value and value.strip():
        return value
    return f"> No output is available for **{role_name}** in this artifact set."


def _overview_markdown(view: Dict[str, Any]) -> str:
    paper = view["paper"]
    memory = view["memory"]
    file_rows = []
    for name, state in view["files"].items():
        if state["valid"]:
            label = "Available"
        elif state["exists"]:
            label = "Invalid JSON"
        else:
            label = "Missing"
        file_rows.append(f"| `{name}` | {label} |")

    return "\n".join(
        [
            f"## {_escape(paper['title'] or view['sample_id'])}",
            "",
            f"- **Sample ID:** {_inline_code(view['sample_id'])}",
            f"- **Status:** `{view['status']}`",
            f"- **Review model:** {_inline_code(paper['review_model_name'])}",
            f"- **Keyword model:** {_inline_code(paper['keyword_model_name'])}",
            f"- **Memory mode:** {_inline_code(memory['mode'])}",
            f"- **Paper Markdown:** {_inline_code(paper['paper_md_path'])}",
            f"- **Semantic Scholar query:** {_inline_code(paper['semantic_scholar_query'])}",
            "",
            "### Artifact completeness",
            "",
            "| Artifact | State |",
            "|---|---|",
            *file_rows,
        ]
    )


def _warnings_markdown(view: Dict[str, Any]) -> str:
    if not view["warnings"]:
        return "✅ All required artifacts and review outputs are available."
    return "\n".join(["### Compatibility warnings", *[f"- {_escape(x)}" for x in view["warnings"]]])


def _authors_text(authors: Any) -> str:
    if not isinstance(authors, list):
        return ""
    values = []
    for author in authors:
        if isinstance(author, dict):
            name = author.get("name")
        else:
            name = author
        if name:
            values.append(str(name))
    return ", ".join(values)


def _references_markdown(view: Dict[str, Any]) -> str:
    papers = view["memory"]["retrieved_papers"]
    if not papers:
        return "> No Semantic Scholar retrieval records are stored in memory.json."

    blocks = [f"### Retrieved papers ({len(papers)})"]
    for index, paper in enumerate(papers, start=1):
        if not isinstance(paper, dict):
            continue
        title = _escape(paper.get("title") or f"Paper {index}")
        url = str(paper.get("url") or "")
        linked_title = f"[{title}]({url})" if url.startswith(("http://", "https://")) else title
        details = []
        if paper.get("year") is not None:
            details.append(f"year={paper.get('year')}")
        citations = paper.get("citationCount", paper.get("citation_counts"))
        if citations is not None:
            details.append(f"citations={citations}")
        authors = _authors_text(paper.get("authors"))
        if authors:
            details.append("authors=" + _escape(authors))
        abstract = " ".join(str(paper.get("abstract") or "").split())
        if len(abstract) > 700:
            abstract = abstract[:697] + "..."

        blocks.extend(["", f"**{index}. {linked_title}**"])
        if details:
            blocks.append("- " + "; ".join(details))
        if abstract:
            blocks.append(f"- {_escape(abstract)}")
    return "\n".join(blocks)


def _memory_markdown(view: Dict[str, Any]) -> str:
    contexts = view["memory"]["reviewer_contexts"]
    if not contexts:
        return (
            "> No reviewer-specific memory context is stored in this memory.json. "
            "This is expected for baseline or older outputs."
        )

    ordered_names = SPECIALIST_REVIEWERS + ["Final Meta-Reviewer"]
    ordered_names.extend(name for name in contexts if name not in ordered_names)
    blocks = ["### Reviewer memory contexts"]
    for reviewer_name in ordered_names:
        context = contexts.get(reviewer_name)
        if not isinstance(context, dict):
            continue
        entries = context.get("entries") if isinstance(context.get("entries"), list) else []
        blocks.extend(
            [
                "",
                f"#### {_escape(reviewer_name)}",
                "",
                f"- Dimension: {_inline_code(context.get('dimension'))}",
                f"- Available records: `{context.get('num_available_records', '—')}`",
                f"- Selected records: `{context.get('num_selected_records', len(entries))}`",
            ]
        )
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue
            blocks.append(
                f"- Memory {index}: `{_escape(entry.get('sample_id') or 'unknown')}` · "
                f"score `{entry.get('score', '—')}` · {_escape(entry.get('paper_title') or '')}"
            )
            excerpt = " ".join(str(entry.get("review_excerpt") or "").split())
            if excerpt:
                blocks.append(f"  - {_escape(excerpt)}")

        formatted = str(context.get("formatted_context") or "").strip()
        if formatted:
            formatted = formatted.replace("```", "''' ")
            blocks.extend(["", "<details><summary>Formatted injected context</summary>", "", "```text", formatted, "```", "", "</details>"])
    return "\n".join(blocks)


def _timing_markdown(view: Dict[str, Any]) -> str:
    timing = view["timing"]
    seconds = timing.get("duration_seconds")
    seconds_text = "—" if seconds is None else str(round(seconds, 3))
    return "\n".join(
        [
            "### Execution timing",
            "",
            f"- **Source:** `{view['timing_source']}`",
            f"- **Started:** {_inline_code(timing.get('started_at_local'))}",
            f"- **Ended:** {_inline_code(timing.get('ended_at_local'))}",
            f"- **Duration:** {_inline_code(timing.get('duration_text'))} ({seconds_text} seconds)",
        ]
    )


def _trace_rows(view: Dict[str, Any]) -> List[List[Any]]:
    rows = []
    for message in view["history"]["messages"]:
        content = " ".join(message["content"].split())
        if len(content) > 240:
            content = content[:237] + "..."
        visible_to = message["visible_to"]
        if isinstance(visible_to, list):
            visible_to = ", ".join(str(value) for value in visible_to)
        rows.append(
            [
                message["index"],
                message["agent_name"],
                message["turn"],
                visible_to,
                message["timestamp"],
                message["msg_type"],
                content,
            ]
        )
    return rows


def _checkpoint_markdown(view: Dict[str, Any]) -> str:
    checkpoint = view["checkpoint"]
    if not checkpoint["exists"]:
        return "> No specialist checkpoint is present. Successful runs remove this temporary artifact."
    completed = list(checkpoint["reviews"])
    missing = [name for name in SPECIALIST_REVIEWERS if name not in completed]
    return "\n".join(
        [
            "### Specialist checkpoint",
            "",
            f"- **Updated:** {_inline_code(checkpoint['updated_at_local'])}",
            f"- **Model:** {_inline_code(checkpoint['model_name'])}",
            f"- **Completed:** {_escape(', '.join(completed) or 'none')}",
            f"- **Missing:** {_escape(', '.join(missing) or 'none')}",
        ]
    )


def _evolution_overview(view: Dict[str, Any]) -> str:
    if not view.get("available"):
        return f"> {_escape(view.get('error') or 'No evolution run is available.')}"
    manifest = view.get("manifest") or {}
    terminal_failure = manifest.get("terminal_failure") or {}
    lines = [
            "### ReviewHarness evolution",
            "",
            f"- **Status:** {_inline_code(manifest.get('status'))}",
            f"- **Requested rounds:** `{manifest.get('rounds', len(view.get('scores') or []))}`",
            f"- **Attempted rounds:** `{manifest.get('attempted_rounds', len(view.get('scores') or []))}`",
            f"- **Completed valid rounds:** `{manifest.get('completed_rounds', 0)}`",
            f"- **Best version:** {_inline_code(manifest.get('best_version_id'))}",
            f"- **Best score:** `{manifest.get('best_score', '—')}`",
            f"- **Best Harness:** {_inline_code(manifest.get('best_harness_path'))}",
        ]
    if terminal_failure:
        lines.extend(
            [
                "",
                "#### Terminal failure",
                "",
                f"- **Round:** `{terminal_failure.get('round')}`",
                f"- **Version:** {_inline_code(terminal_failure.get('version_id'))}",
                f"- **Reason:** {_escape(terminal_failure.get('reason'))}",
            ]
        )
    return "\n".join(lines)


def _evolution_tree_markdown(view: Dict[str, Any]) -> str:
    versions = view.get("versions") or {}
    if not versions:
        return "> No lineage records are available."
    children: Dict[Any, List[str]] = {}
    for version_id, item in versions.items():
        children.setdefault(item.get("parent_version_id"), []).append(version_id)
    best = view.get("best_version_id")
    lines = ["### Evolution tree", ""]
    visited = set()

    def visit(version_id: str, depth: int):
        if version_id in visited:
            return
        visited.add(version_id)
        item = versions[version_id]
        marker = " ★ best" if version_id == best else ""
        score = item.get("score")
        score_text = "—" if score is None else score
        lines.append(
            "  " * depth
            + f"- `{version_id}` · score `{score_text}`{marker}"
        )
        for child in sorted(children.get(version_id, [])):
            visit(child, depth + 1)

    for root_id in sorted(children.get(None, [])):
        visit(root_id, 0)
    for version_id in sorted(set(versions) - visited):
        visit(version_id, 0)
    return "\n".join(lines)


def _evolution_score_rows(view: Dict[str, Any]) -> List[List[Any]]:
    rows = []
    best = view.get("best_version_id")
    for item in view.get("scores") or []:
        version_id = item.get("version_id")
        rows.append(
            [
                item.get("round"),
                item.get("round_status", "unknown"),
                version_id,
                item.get("parent_version_id"),
                item.get("score"),
                len(item.get("failures") or []),
                round(float(item.get("duration_seconds", 0.0) or 0.0), 3),
                bool(item.get("eligible_for_best", False)),
                version_id == best,
            ]
        )
    return rows


def _evolution_detail_markdown(detail: Dict[str, Any]) -> str:
    if not detail:
        return "> Select an evaluated Harness version."
    mutation = detail.get("mutation") or {}
    record = detail.get("round_record") or {}
    failures = (detail.get("evaluation") or {}).get("failures") or []
    blocks = [
        f"### {_escape(detail.get('version_id'))}",
        "",
        f"- **Parent:** {_inline_code(detail.get('parent_version_id'))}",
        f"- **Round status:** {_inline_code(record.get('round_status', 'unknown'))}",
        f"- **Eligible for best:** `{bool(record.get('eligible_for_best', False))}`",
        f"- **Score:** `{detail.get('score') if detail.get('score') is not None else '—'}`",
        f"- **Change reason:** {_escape(detail.get('change_reason') or mutation.get('change_reason'))}",
        f"- **Mutation analysis:** {_escape(mutation.get('analysis'))}",
        f"- **Mutation strategy:** {_escape(mutation.get('strategy'))}",
        f"- **Production code changed:** `{mutation.get('production_code_changed', False)}`",
        f"- **Duration:** `{round(float(record.get('duration_seconds', 0.0) or 0.0), 3)}s`",
        f"- **Failed cases:** `{len(failures)}`",
    ]
    if failures:
        blocks.extend(["", "#### Failure cases"])
        for failure in failures[:30]:
            blocks.append(
                "- "
                + _escape(
                    f"{failure.get('sample_id', 'run')} · "
                    f"{failure.get('dimension', '')} · {failure.get('failure_type')}: "
                    f"{failure.get('missing_labels', failure.get('details', ''))}"
                )
            )
    return "\n".join(blocks)


def _evolution_trace_rows(detail: Dict[str, Any], stage: Any) -> List[List[Any]]:
    rows = []
    stages = {stage} if isinstance(stage, str) else set(stage)
    mutation = detail.get("mutation") or {}
    if "llm" in stages and mutation.get("mutation_mode") == "llm":
        raw_response = str(mutation.get("mutator_raw_response") or "")
        if len(raw_response) > 900:
            raw_response = raw_response[:897] + "..."
        rows.append(
            [
                "evolution",
                "—",
                "harness_json_mutation",
                "ok",
                mutation.get("mutation_duration_seconds"),
                raw_response,
            ]
        )
    for trace in (detail.get("call_traces") or {}).get("traces", []):
        sample_label = trace.get("sample_id")
        if trace.get("attempt"):
            sample_label = f"{sample_label} · {trace.get('attempt')}"
        for event in trace.get("events", []):
            if event.get("stage") not in stages:
                continue
            details = json.dumps(event.get("details") or {}, ensure_ascii=False)
            if len(details) > 900:
                details = details[:897] + "..."
            rows.append(
                [
                    sample_label,
                    event.get("index"),
                    event.get("operation"),
                    event.get("status"),
                    event.get("duration_seconds"),
                    details,
                ]
            )
    return rows


def render_evolution_version(evolution_dir: str, version_id: str) -> List[Any]:
    view = load_evolution(evolution_dir)
    detail = evolution_version_detail(view, version_id)
    mutation = detail.get("mutation") or {}
    return [
        _evolution_detail_markdown(detail),
        mutation.get("config_diff", "No Harness config diff (seed version)."),
        mutation.get("prompt_diff", "No prompt diff (seed version)."),
        _evolution_trace_rows(detail, "retrieval"),
        _evolution_trace_rows(detail, "memory"),
        _evolution_trace_rows(detail, "llm"),
        _evolution_trace_rows(detail, {"validation", "workflow"}),
    ]


def _empty_render(message: str, artifact_name: str) -> List[Any]:
    unavailable = f"> {message}"
    return [
        "## No sample selected",
        unavailable,
        *[unavailable for _ in SPECIALIST_REVIEWERS],
        unavailable,
        unavailable,
        unavailable,
        unavailable,
        [],
        unavailable,
        f"Artifact not available: {artifact_name}",
    ]


def render_sample(output_dir: str, sample_id: str, artifact_name: str) -> List[Any]:
    if not sample_id:
        return _empty_render("No review sample was found in the configured output directory.", artifact_name)
    try:
        view = load_sample(output_dir, sample_id)
    except (OSError, ValueError) as exc:
        return _empty_render(f"Unable to load sample: {type(exc).__name__}: {exc}", artifact_name)

    return [
        _overview_markdown(view),
        _warnings_markdown(view),
        *[
            _review_text(view["reviewers"][name], name)
            for name in SPECIALIST_REVIEWERS
        ],
        _review_text(view["final_meta_review"], "Final Meta-Reviewer"),
        _references_markdown(view),
        _memory_markdown(view),
        _timing_markdown(view),
        _trace_rows(view),
        _checkpoint_markdown(view),
        raw_artifact_text(view, artifact_name),
    ]


def create_app(output_dir: str, evolution_dir: str = None):
    try:
        import gradio as gr
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Gradio is not installed. Install project dependencies with "
            "`pip install -r requirements.txt`."
        ) from exc

    output_dir = str(Path(output_dir).expanduser().resolve())
    evolution_dir = str(Path(evolution_dir or output_dir).expanduser().resolve())
    catalog = scan_output_dir(output_dir)
    sample_choices = [
        (f"{item['sample_id']} · {item['status']}", item["sample_id"])
        for item in catalog["samples"]
    ]
    initial_sample = sample_choices[0][1] if sample_choices else None
    initial_values = render_sample(output_dir, initial_sample, "reviews.json")
    evolution_view = load_evolution(evolution_dir)
    evolution_choices = sorted((evolution_view.get("versions") or {}).keys())
    initial_evolution_version = (
        evolution_view.get("best_version_id")
        if evolution_view.get("best_version_id") in evolution_choices
        else (evolution_choices[0] if evolution_choices else None)
    )
    initial_evolution_values = render_evolution_version(
        evolution_dir, initial_evolution_version
    )

    with gr.Blocks(title="AgentReview Result Viewer") as demo:
        gr.Markdown(
            "# AgentReview Result Viewer\n"
            "Read-only inspection of existing review JSON artifacts. "
            "This viewer never starts or modifies a review run."
        )
        with gr.Row():
            output_box = gr.Textbox(
                value=output_dir,
                label="Output directory",
                interactive=False,
                scale=3,
            )
            refresh_button = gr.Button("Refresh", scale=1)
        sample_selector = gr.Dropdown(
            choices=sample_choices,
            value=initial_sample,
            label="Review sample",
            interactive=True,
        )
        catalog_message = gr.Markdown(
            value=(
                f"> {catalog['error']}"
                if catalog["error"]
                else f"Found **{len(sample_choices)}** sample output directorie(s)."
            )
        )

        with gr.Tabs():
            with gr.Tab("Overview"):
                overview = gr.Markdown(initial_values[0])
                warnings = gr.Markdown(initial_values[1])

            with gr.Tab("Reviewer outputs"):
                reviewer_components = []
                for index, reviewer_name in enumerate(SPECIALIST_REVIEWERS):
                    with gr.Tab(reviewer_name.replace("Reviewer ", "")):
                        reviewer_components.append(gr.Markdown(initial_values[2 + index]))

            with gr.Tab("Final Meta Review"):
                final_meta = gr.Markdown(initial_values[7])

            with gr.Tab("Memory"):
                with gr.Tab("Retrieved papers"):
                    references = gr.Markdown(initial_values[8])
                with gr.Tab("Reviewer memory"):
                    memory_contexts = gr.Markdown(initial_values[9])

            with gr.Tab("History & timing"):
                timing = gr.Markdown(initial_values[10])
                checkpoint = gr.Markdown(initial_values[12])
                gr.Markdown("### review_history message trace")
                trace = gr.Dataframe(
                    headers=[
                        "#",
                        "Agent",
                        "Turn",
                        "Visible to",
                        "Timestamp",
                        "Type",
                        "Content preview",
                    ],
                    value=initial_values[11],
                    interactive=False,
                )

            with gr.Tab("Raw JSON"):
                artifact_selector = gr.Dropdown(
                    choices=VIEWABLE_ARTIFACTS,
                    value="reviews.json",
                    label="Artifact",
                )
                raw_json = gr.Code(
                    value=initial_values[13], language="json", interactive=False
                )

            with gr.Tab("Evolution"):
                evolution_overview = gr.Markdown(_evolution_overview(evolution_view))
                evolution_tree = gr.Markdown(_evolution_tree_markdown(evolution_view))
                gr.Markdown("### Score progression")
                evolution_scores = gr.Dataframe(
                    headers=[
                        "Round",
                        "Status",
                        "Version",
                        "Parent",
                        "Score",
                        "Failures",
                        "Seconds",
                        "Eligible",
                        "Best",
                    ],
                    value=_evolution_score_rows(evolution_view),
                    interactive=False,
                )
                evolution_version = gr.Dropdown(
                    choices=evolution_choices,
                    value=initial_evolution_version,
                    label="Harness version",
                )
                evolution_detail = gr.Markdown(initial_evolution_values[0])
                with gr.Tab("Harness / code diff"):
                    evolution_config_diff = gr.Code(
                        value=initial_evolution_values[1], interactive=False
                    )
                with gr.Tab("Prompt diff"):
                    evolution_prompt_diff = gr.Code(
                        value=initial_evolution_values[2], interactive=False
                    )
                with gr.Tab("Literature retrieval trace"):
                    evolution_retrieval_trace = gr.Dataframe(
                        headers=["Sample", "#", "Operation", "Status", "Seconds", "Details"],
                        value=initial_evolution_values[3],
                        interactive=False,
                    )
                with gr.Tab("Memory read/write trace"):
                    evolution_memory_trace = gr.Dataframe(
                        headers=["Sample", "#", "Operation", "Status", "Seconds", "Details"],
                        value=initial_evolution_values[4],
                        interactive=False,
                    )
                with gr.Tab("LLM call trace"):
                    evolution_llm_trace = gr.Dataframe(
                        headers=["Sample", "#", "Operation", "Status", "Seconds", "Details"],
                        value=initial_evolution_values[5],
                        interactive=False,
                    )
                with gr.Tab("Validation / workflow trace"):
                    evolution_validation_trace = gr.Dataframe(
                        headers=["Sample", "#", "Operation", "Status", "Seconds", "Details"],
                        value=initial_evolution_values[6],
                        interactive=False,
                    )

        content_outputs = [
            overview,
            warnings,
            *reviewer_components,
            final_meta,
            references,
            memory_contexts,
            timing,
            trace,
            checkpoint,
            raw_json,
        ]

        def refresh_catalog():
            refreshed = scan_output_dir(output_dir)
            choices = [
                (f"{item['sample_id']} · {item['status']}", item["sample_id"])
                for item in refreshed["samples"]
            ]
            selected = choices[0][1] if choices else None
            message = (
                f"> {refreshed['error']}"
                if refreshed["error"]
                else f"Found **{len(choices)}** sample output directorie(s)."
            )
            return gr.update(choices=choices, value=selected), message

        def render_selected(sample_id, artifact_name):
            return render_sample(output_dir, sample_id, artifact_name)

        def render_raw(sample_id, artifact_name):
            if not sample_id:
                return f"Artifact not available: {artifact_name}"
            try:
                return raw_artifact_text(load_sample(output_dir, sample_id), artifact_name)
            except (OSError, ValueError) as exc:
                return f"Unable to load artifact: {type(exc).__name__}: {exc}"

        sample_selector.change(
            render_selected,
            inputs=[sample_selector, artifact_selector],
            outputs=content_outputs,
        )
        artifact_selector.change(
            render_raw,
            inputs=[sample_selector, artifact_selector],
            outputs=raw_json,
        )
        refresh_button.click(
            refresh_catalog,
            outputs=[sample_selector, catalog_message],
        ).then(
            render_selected,
            inputs=[sample_selector, artifact_selector],
            outputs=content_outputs,
        )
        evolution_version.change(
            lambda version_id: render_evolution_version(evolution_dir, version_id),
            inputs=[evolution_version],
            outputs=[
                evolution_detail,
                evolution_config_diff,
                evolution_prompt_diff,
                evolution_retrieval_trace,
                evolution_memory_trace,
                evolution_llm_trace,
                evolution_validation_trace,
            ],
        )

    return demo


def main():
    args = parse_args()
    try:
        demo = create_app(args.output_dir, args.evolution_dir)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        show_error=True,
    )


if __name__ == "__main__":
    main()
