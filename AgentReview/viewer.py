"""Standalone, read-only Gradio viewer for AgentReview result artifacts."""

from __future__ import annotations

import argparse
import html
from pathlib import Path
from typing import Any

from agentreview.viewer_data import (
    SPECIALIST_REVIEWERS,
    VIEWABLE_ARTIFACTS,
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


def _overview_markdown(view: dict[str, Any]) -> str:
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


def _warnings_markdown(view: dict[str, Any]) -> str:
    if not view["warnings"]:
        return "✅ All required artifacts and review outputs are available."
    return "\n".join(
        ["### Compatibility warnings", *[f"- {_escape(x)}" for x in view["warnings"]]]
    )


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


def _references_markdown(view: dict[str, Any]) -> str:
    papers = view["memory"]["retrieved_papers"]
    if not papers:
        return "> No Semantic Scholar retrieval records are stored in memory.json."

    blocks = [f"### Retrieved papers ({len(papers)})"]
    for index, paper in enumerate(papers, start=1):
        if not isinstance(paper, dict):
            continue
        title = _escape(paper.get("title") or f"Paper {index}")
        url = str(paper.get("url") or "")
        linked_title = (
            f"[{title}]({url})" if url.startswith(("http://", "https://")) else title
        )
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


def _memory_markdown(view: dict[str, Any]) -> str:
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
        entries = (
            context.get("entries") if isinstance(context.get("entries"), list) else []
        )
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
            blocks.extend(
                [
                    "",
                    "<details><summary>Formatted injected context</summary>",
                    "",
                    "```text",
                    formatted,
                    "```",
                    "",
                    "</details>",
                ]
            )
    return "\n".join(blocks)


def _timing_markdown(view: dict[str, Any]) -> str:
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


def _trace_rows(view: dict[str, Any]) -> list[list[Any]]:
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


def _checkpoint_markdown(view: dict[str, Any]) -> str:
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


def _empty_render(message: str, artifact_name: str) -> list[Any]:
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


def render_sample(output_dir: str, sample_id: str, artifact_name: str) -> list[Any]:
    if not sample_id:
        return _empty_render(
            "No review sample was found in the configured output directory.",
            artifact_name,
        )
    try:
        view = load_sample(output_dir, sample_id)
    except (OSError, ValueError) as exc:
        return _empty_render(
            f"Unable to load sample: {type(exc).__name__}: {exc}", artifact_name
        )

    return [
        _overview_markdown(view),
        _warnings_markdown(view),
        *[_review_text(view["reviewers"][name], name) for name in SPECIALIST_REVIEWERS],
        _review_text(view["final_meta_review"], "Final Meta-Reviewer"),
        _references_markdown(view),
        _memory_markdown(view),
        _timing_markdown(view),
        _trace_rows(view),
        _checkpoint_markdown(view),
        raw_artifact_text(view, artifact_name),
    ]


def create_app(output_dir: str):
    try:
        import gradio as gr
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Gradio is not installed. Install project dependencies with "
            "`pip install -r requirements-optional.txt`."
        ) from exc

    output_dir = str(Path(output_dir).expanduser().resolve())
    catalog = scan_output_dir(output_dir)
    sample_choices = [
        (f"{item['sample_id']} · {item['status']}", item["sample_id"])
        for item in catalog["samples"]
    ]
    initial_sample = sample_choices[0][1] if sample_choices else None
    initial_values = render_sample(output_dir, initial_sample, "reviews.json")
    with gr.Blocks(title="AgentReview Result Viewer") as demo:
        gr.Markdown(
            "# AgentReview Result Viewer\n"
            "Read-only inspection of existing review JSON artifacts. "
            "All controls preserve the stored review run."
        )
        with gr.Row():
            gr.Textbox(
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
                        reviewer_components.append(
                            gr.Markdown(initial_values[2 + index])
                        )

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
                return raw_artifact_text(
                    load_sample(output_dir, sample_id), artifact_name
                )
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
    return demo


def main():
    args = parse_args()
    try:
        demo = create_app(args.output_dir)
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
