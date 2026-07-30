"""Independent Gradio application for read-only M★ evolution inspection."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .data_loader import ancestor_root, load_run, load_trace_diagnostics, scan_runs
    from .render import (
        comparison_for_transition,
        comparison_payload,
        default_transition_uid,
        find_transformation,
        raw_record_json,
        render_ab_header,
        render_diagnostics,
        render_helper_changes,
        render_parse_warnings,
        render_prompt_changes,
        render_read_changes,
        render_run_overview,
        render_schema_changes,
        render_score_chart,
        render_transition_header,
        render_write_changes,
        transition_choices,
        warnings_markdown,
    )
except ImportError:  # Allows `python evolution_viewer/app.py`.
    from data_loader import ancestor_root, load_run, load_trace_diagnostics, scan_runs
    from render import (
        comparison_for_transition,
        comparison_payload,
        default_transition_uid,
        find_transformation,
        raw_record_json,
        render_ab_header,
        render_diagnostics,
        render_helper_changes,
        render_parse_warnings,
        render_prompt_changes,
        render_read_changes,
        render_run_overview,
        render_schema_changes,
        render_score_chart,
        render_transition_header,
        render_write_changes,
        transition_choices,
        warnings_markdown,
    )


CSS = r"""
:root {
  --ev-ink: #101713;
  --ev-muted: #202c26;
  --ev-paper: #f7f5ef;
  --ev-panel: #ffffff;
  --ev-border: #dfe5df;
  --ev-green: #177c5b;
  --ev-green-soft: #dff4e8;
  --ev-red: #b04435;
  --ev-red-soft: #fbe6e1;
  --ev-amber: #b97916;
  --ev-blue: #286e9b;
}
.gradio-container {
  max-width: 1480px !important;
  background: var(--ev-paper);
  color: var(--ev-ink) !important;
  --body-text-color: #101713;
  --body-text-color-subdued: #202c26;
  --block-label-text-color: #17201b;
  --input-placeholder-color: #334039;
}
.gradio-container .prose,
.gradio-container .prose p,
.gradio-container .prose li,
.gradio-container .prose h1,
.gradio-container .prose h2,
.gradio-container .prose h3,
.gradio-container .prose h4 {
  color: var(--ev-ink) !important;
}
.gradio-container label,
.gradio-container label *,
.gradio-container .block-info,
.gradio-container .block-title {
  color: #17201b !important;
  opacity: 1 !important;
}
.gradio-container input,
.gradio-container textarea,
.gradio-container button {
  color: var(--ev-ink);
}
.ev-filter-bar {
  align-items: flex-end !important;
  gap: 14px !important;
  margin: 14px 0 18px !important;
  padding: 15px 16px !important;
  border: 1px solid #9dafA5 !important;
  border-radius: 14px !important;
  background: #e8f0eb !important;
  box-shadow: 0 6px 20px rgba(20, 49, 37, .07);
}
.ev-filter-bar .form {
  gap: 13px !important;
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
}
.ev-filter.block {
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  overflow: visible !important;
}
.ev-filter [data-testid="block-info"] {
  display: block !important;
  margin: 0 0 7px 2px !important;
  color: #101b15 !important;
  font-size: 12px !important;
  font-weight: 850 !important;
  letter-spacing: .035em;
  line-height: 1.2;
  opacity: 1 !important;
}
.ev-filter .secondary-wrap {
  min-height: 46px !important;
  border: 2px solid #52665b !important;
  border-radius: 9px !important;
  background: #fff !important;
  box-shadow: 0 1px 0 rgba(16, 32, 24, .04);
  transition: border-color .15s ease, box-shadow .15s ease;
}
.ev-filter .secondary-wrap:focus-within {
  border-color: #087755 !important;
  box-shadow: 0 0 0 3px rgba(8, 119, 85, .16) !important;
}
.ev-filter input {
  min-height: 42px !important;
  padding: 0 13px !important;
  color: #0e1813 !important;
  font-size: 14px !important;
  font-weight: 750 !important;
  opacity: 1 !important;
}
.ev-filter .dropdown-arrow {
  color: #15261d !important;
  fill: #15261d !important;
}
.ev-refresh {
  align-self: flex-end !important;
  min-height: 46px !important;
  margin: 0 !important;
  border: 2px solid #164c3a !important;
  border-radius: 9px !important;
  background: #164c3a !important;
  color: #fff !important;
  font-size: 14px !important;
  font-weight: 850 !important;
  box-shadow: 0 4px 12px rgba(22, 76, 58, .18) !important;
}
.ev-refresh:hover {
  border-color: #0d382a !important;
  background: #0d382a !important;
}
.ev-header {
  padding: 25px 27px 20px; border: 1px solid var(--ev-border);
  border-radius: 18px; background: linear-gradient(128deg, #122c23, #1b5843);
  color: #f7fff9; margin-bottom: 14px;
}
.ev-header, .ev-header * { color: #f7fff9 !important; }
.ev-header .eyebrow, .ev-kicker {
  font-size: 11px; letter-spacing: .16em; font-weight: 800; opacity: .92;
}
.ev-header h1 { margin: 5px 0 4px; font-size: 30px; }
.ev-header p { margin: 0; opacity: .78; }
.ev-hero, .ev-panel, .ev-transition-head, .ev-diff-card, .ev-code-card,
.ev-diagnostics section, .ev-fact-box {
  background: var(--ev-panel); border: 1px solid var(--ev-border);
  border-radius: 14px; box-shadow: 0 7px 25px rgba(24, 42, 34, .04);
}
.ev-hero { padding: 22px; }
.ev-hero h2 { margin: 4px 0 2px; color: var(--ev-ink); }
.ev-hero p, .ev-muted { color: var(--ev-muted); font-weight: 500; }
.ev-metrics {
  display: grid; grid-template-columns: repeat(6, minmax(115px, 1fr));
  gap: 9px; margin-top: 18px;
}
.ev-metrics div {
  padding: 12px; border-radius: 10px; background: #f1f5f1;
  display: flex; flex-direction: column; gap: 4px;
}
.ev-metrics span, .ev-config-strip span { color: var(--ev-muted); font-size: 12px; font-weight: 600; }
.ev-metrics strong { color: var(--ev-ink); font-size: 15px; overflow-wrap: anywhere; }
.ev-config-strip { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 13px; }
.ev-config-strip b { color: var(--ev-ink); font-family: ui-monospace, monospace; }
.ev-panel { padding: 14px 18px; }
.ev-panel-title { display: flex; align-items: center; gap: 18px; color: var(--ev-ink); }
.ev-panel-title small { margin-left: auto; color: var(--ev-muted); font-weight: 600; }
.ev-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 6px; }
.ev-dot.static { background: var(--ev-blue); }
.ev-dot.rotating { background: var(--ev-amber); }
.ev-chart { width: 100%; height: auto; color: var(--ev-ink); overflow: visible; }
.ev-line-static, .ev-line-rotating { fill: none; stroke-width: 2.4; }
.ev-line-static { stroke: var(--ev-blue); }
.ev-line-rotating { stroke: var(--ev-amber); stroke-dasharray: 5 4; }
.ev-point-static { fill: #fff; stroke: var(--ev-blue); stroke-width: 2.5; }
.ev-point-rotating { fill: #fff; stroke: var(--ev-amber); stroke-width: 2.5; }
.transition-radio label {
  border: 1px solid var(--ev-border) !important; border-radius: 11px !important;
  padding: 11px 13px !important; background: #fff !important;
  margin-bottom: 7px !important; align-items: flex-start !important;
}
.transition-radio label:has(input:checked) {
  border-color: var(--ev-green) !important; box-shadow: 0 0 0 2px rgba(23,124,91,.12);
}
.ev-transition-head { padding: 20px; margin-bottom: 12px; }
.ev-transition-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.ev-iteration { font-size: 11px; letter-spacing: .12em; font-weight: 800; color: var(--ev-muted); }
.ev-badge, .ev-tag, .ev-change-state {
  display: inline-block; border-radius: 999px; font-size: 11px; font-weight: 750;
  padding: 3px 8px; margin-left: 7px;
}
.ev-badge.ok { color: var(--ev-green); background: var(--ev-green-soft); }
.ev-badge.bad { color: var(--ev-red); background: var(--ev-red-soft); }
.ev-tag { color: #405d50; background: #edf1ed; }
.ev-change-state.added, .ev-change-state.modified { color: var(--ev-green); background: var(--ev-green-soft); }
.ev-change-state.removed { color: var(--ev-red); background: var(--ev-red-soft); }
.ev-change-state.unchanged { color: var(--ev-muted); background: #edf0ed; }
.ev-score-delta { font: 800 23px/1 ui-monospace, monospace; }
.ev-score-delta.positive { color: var(--ev-green); }
.ev-score-delta.negative { color: var(--ev-red); }
.ev-score-delta.neutral { color: var(--ev-muted); }
.ev-transition-head h3 { margin: 14px 0 8px; }
.ev-transition-head code { font-size: .92em; }
.ev-arrow { color: var(--ev-amber); padding: 0 10px; }
.ev-score-row { display: flex; gap: 8px; flex-wrap: wrap; }
.ev-score-row span { padding: 6px 9px; border-radius: 7px; background: #edf2ee; color: #2f3d36; font-weight: 600; }
.ev-score-row b { color: var(--ev-ink); font-family: ui-monospace, monospace; }
.ev-reflector { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--ev-border); }
.ev-reflector > span { display: block; font-size: 10px; letter-spacing: .12em; color: var(--ev-muted); }
.ev-reflector > strong { display: block; margin-top: 5px; color: var(--ev-ink); }
.ev-reflector ul { margin-bottom: 0; color: #3d4a44; }
.ev-diff-card, .ev-code-card { margin-bottom: 9px; overflow: hidden; }
.ev-diff-card summary, .ev-code-card summary {
  cursor: pointer; padding: 13px 15px; font-weight: 700; background: #fbfcfa;
}
.ev-side-by-side { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--ev-border); }
.ev-side-by-side > div { min-width: 0; background: #fff; }
.ev-side-by-side h4 { margin: 0; padding: 9px 12px; color: #26342d; background: #edf2ee; font-weight: 800; }
.ev-side-by-side pre {
  margin: 0; padding: 14px; max-height: 480px; overflow: auto;
  white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.65 ui-monospace, monospace;
}
.ev-word-del { background: #ffd9d1; color: #7d231a; text-decoration: line-through; }
.ev-word-add { background: #ccefdc; color: #0c573c; }
.ev-prompt-modes { padding: 10px 12px 0; }
.ev-prompt-modes > input { position: absolute; opacity: 0; pointer-events: none; }
.ev-prompt-modes > label {
  display: inline-block; cursor: pointer; margin: 0 5px 9px 0; padding: 4px 9px;
  border: 1px solid var(--ev-border); border-radius: 999px; font-size: 11px;
}
.ev-prompt-modes > input:checked + label { color: #fff; background: var(--ev-green); border-color: var(--ev-green); }
.ev-prompt-modes > input[id$="-added"]:checked ~ .ev-prompt-sides .ev-prompt-before { display: none; }
.ev-prompt-modes > input[id$="-added"]:checked ~ .ev-prompt-sides .ev-prompt-after .ev-word-equal { display: none; }
.ev-prompt-modes > input[id$="-removed"]:checked ~ .ev-prompt-sides .ev-prompt-after { display: none; }
.ev-prompt-modes > input[id$="-removed"]:checked ~ .ev-prompt-sides .ev-prompt-before .ev-word-equal { display: none; }
.ev-prompt-modes > input[id$="-added"]:checked ~ .ev-prompt-sides,
.ev-prompt-modes > input[id$="-removed"]:checked ~ .ev-prompt-sides { grid-template-columns: 1fr; }
.ev-no-controls { padding-top: 0; }
.ev-table-wrap { overflow-x: auto; border: 1px solid var(--ev-border); border-radius: 12px; }
.ev-table { width: 100%; border-collapse: collapse; background: #fff; }
.ev-table th, .ev-table td { padding: 10px 12px; border-bottom: 1px solid var(--ev-border); text-align: left; vertical-align: top; }
.ev-table th { color: var(--ev-muted); font-size: 11px; text-transform: uppercase; background: #f5f7f4; }
.ev-table td { font-size: 13px; }
.ev-code-card ul { margin: 5px 18px 14px; }
.ev-fact-box { padding: 13px 16px; margin-bottom: 9px; }
.ev-alert { padding: 11px 14px; border: 1px solid #efc46f; border-radius: 10px; background: #fff6df; }
.ev-empty { padding: 18px; color: #2f3d36; font-weight: 600; border: 1px dashed #a9b5ae; border-radius: 11px; background: #fff; }
.ev-diagnostics { display: grid; grid-template-columns: 1fr 1.4fr; gap: 10px; }
.ev-diagnostics section { padding: 15px; min-width: 0; }
.ev-diagnostics h4 { margin-top: 0; }
@media (max-width: 900px) {
  .ev-filter-bar { align-items: stretch !important; }
  .ev-refresh { min-height: 46px !important; }
  .ev-metrics { grid-template-columns: repeat(2, 1fr); }
  .ev-side-by-side, .ev-diagnostics { grid-template-columns: 1fr; }
  .ev-panel-title { align-items: flex-start; flex-direction: column; gap: 6px; }
  .ev-panel-title small { margin-left: 0; }
}
"""


EMPTY_HTML = '<div class="ev-empty">没有可展示的数据。</div>'


class ViewerController:
    """Small state holder for Gradio callbacks."""

    def __init__(self, runs_dir: str):
        self.runs_dir = str(Path(runs_dir).expanduser().resolve())
        self.catalog = scan_runs(self.runs_dir)

    @property
    def entries(self) -> List[Dict[str, Any]]:
        return list(self.catalog.get("entries") or [])

    def refresh(self) -> None:
        self.catalog = scan_runs(self.runs_dir)

    def suites(self) -> List[str]:
        return sorted({str(item["suite"]) for item in self.entries})

    def tasks(self, suite: Optional[str]) -> List[str]:
        return sorted(
            {
                str(item["task"])
                for item in self.entries
                if str(item["suite"]) == str(suite)
            }
        )

    def runs(self, suite: Optional[str], task: Optional[str]) -> List[Tuple[str, str]]:
        return [
            (f"{item['run']} · {item['id']}", str(item["id"]))
            for item in self.entries
            if str(item["suite"]) == str(suite)
            and str(item["task"]) == str(task)
        ]

    def first_selection(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        suites = self.suites()
        suite = suites[0] if suites else None
        tasks = self.tasks(suite)
        task = tasks[0] if tasks else None
        runs = self.runs(suite, task)
        run_id = runs[0][1] if runs else None
        return suite, task, run_id

    def load(self, run_id: Optional[str]) -> Dict[str, Any]:
        if not run_id:
            return {
                "label": "未找到运行",
                "config": {},
                "transformations": [],
                "programs": {},
                "warnings": list(self.catalog.get("errors") or []),
            }
        try:
            return load_run(self.runs_dir, run_id)
        except (OSError, ValueError) as exc:
            return {
                "label": str(run_id),
                "config": {},
                "transformations": [],
                "programs": {},
                "warnings": [f"{type(exc).__name__}: {exc}"],
            }

    def program_defaults(
        self, run: Dict[str, Any]
    ) -> Tuple[List[Tuple[str, str]], Optional[str], Optional[str]]:
        values = []
        for program_id, item in (run.get("programs") or {}).items():
            if not item.get("source_path"):
                continue
            score = item.get("static_score")
            label = f"{program_id} · static={score:.4f}" if isinstance(score, (int, float)) else program_id
            values.append((label, program_id))
        values.sort(key=lambda pair: int((run["programs"][pair[1]].get("iteration") or 0)))
        best = run.get("best_program_id")
        if best not in {value for _, value in values}:
            best = values[-1][1] if values else None
        root = ancestor_root(run.get("programs") or {}, best)
        if root not in {value for _, value in values}:
            root = values[0][1] if values else None
        return values, root, best


def parse_args():
    parser = argparse.ArgumentParser(
        description="Read-only Gradio viewer for M★ evolution artifacts."
    )
    parser.add_argument(
        "--runs-dir",
        default="runs",
        help="Root directory to scan for lineage.jsonl and scores.jsonl.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7862)
    parser.add_argument("--share", action="store_true")
    return parser.parse_args()


def create_app(runs_dir: str):
    try:
        import gradio as gr
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "未安装 Gradio。请执行：pip install -r evolution_viewer/requirements.txt"
        ) from exc

    controller = ViewerController(runs_dir)
    initial_suite, initial_task, initial_run_id = controller.first_selection()
    initial_run = controller.load(initial_run_id)
    initial_choices = transition_choices(initial_run)
    initial_transition = default_transition_uid(initial_run)
    program_choices, initial_a, initial_b = controller.program_defaults(initial_run)

    def render_run_state(run_id):
        run = controller.load(run_id)
        choices = transition_choices(run)
        selected = default_transition_uid(run)
        programs, before_id, after_id = controller.program_defaults(run)
        combined_warnings = list(controller.catalog.get("errors") or []) + list(
            run.get("warnings") or []
        )
        warning_text = warnings_markdown(combined_warnings)
        return (
            render_run_overview(run),
            render_score_chart(run),
            gr.update(value=warning_text, visible=bool(warning_text)),
            gr.update(choices=choices, value=selected),
            gr.update(choices=programs, value=before_id),
            gr.update(choices=programs, value=after_id),
        )

    def render_transition(run_id, uid):
        run = controller.load(run_id)
        item = find_transformation(run, uid)
        comparison = comparison_for_transition(run, item)
        trace = load_trace_diagnostics(controller.runs_dir, run_id, item) if item else {}
        return (
            render_transition_header(item, comparison),
            render_parse_warnings(comparison),
            render_prompt_changes(comparison),
            render_schema_changes(comparison),
            render_write_changes(comparison),
            render_read_changes(comparison),
            render_helper_changes(comparison),
            comparison.get("raw_diff", "没有可用的父子代码 diff。") if comparison else "没有可用的父子代码 diff。",
            render_diagnostics(item, trace),
            trace.get("raw_excerpt", "该迭代没有 trace 文件。"),
            raw_record_json(item),
        )

    def render_ab(run_id, before_id, after_id):
        run = controller.load(run_id)
        comparison = comparison_payload(run, before_id, after_id)
        return (
            render_ab_header(run, before_id, after_id, comparison),
            render_parse_warnings(comparison),
            render_prompt_changes(comparison),
            render_schema_changes(comparison),
            render_write_changes(comparison),
            render_read_changes(comparison),
            render_helper_changes(comparison),
            comparison.get("raw_diff", "没有可用的代码 diff。") if comparison else "没有可用的代码 diff。",
        )

    def update_for_suite(suite):
        tasks = controller.tasks(suite)
        task = tasks[0] if tasks else None
        runs = controller.runs(suite, task)
        run_id = runs[0][1] if runs else None
        return (
            gr.update(choices=tasks, value=task),
            gr.update(choices=runs, value=run_id),
        )

    def update_for_task(suite, task):
        runs = controller.runs(suite, task)
        run_id = runs[0][1] if runs else None
        return gr.update(choices=runs, value=run_id)

    def refresh_selectors(current_run):
        controller.refresh()
        current_entry = next(
            (item for item in controller.entries if str(item["id"]) == str(current_run)),
            None,
        )
        if current_entry:
            suite, task, run_id = (
                current_entry["suite"],
                current_entry["task"],
                current_entry["id"],
            )
        else:
            suite, task, run_id = controller.first_selection()
        return (
            gr.update(choices=controller.suites(), value=suite),
            gr.update(choices=controller.tasks(suite), value=task),
            gr.update(choices=controller.runs(suite, task), value=run_id),
        )

    initial_detail = render_transition(initial_run_id, initial_transition)
    initial_ab = render_ab(initial_run_id, initial_a, initial_b)
    initial_warnings = warnings_markdown(
        list(controller.catalog.get("errors") or [])
        + list(initial_run.get("warnings") or [])
    )

    with gr.Blocks(css=CSS, title="M★ Evolution Viewer") as demo:
        gr.HTML(
            """
            <header class="ev-header">
              <div class="eyebrow">STANDALONE · READ ONLY · DETERMINISTIC</div>
              <h1>M★ 程序转变审计台</h1>
              <p>关注 Prompt、Schema、Write、Read 与辅助代码到底发生了什么变化。</p>
            </header>
            """
        )
        with gr.Row(elem_classes=["ev-filter-bar"]):
            suite_selector = gr.Dropdown(
                choices=controller.suites(),
                value=initial_suite,
                label="实验套件",
                scale=2,
                elem_classes=["ev-filter"],
            )
            task_selector = gr.Dropdown(
                choices=controller.tasks(initial_suite),
                value=initial_task,
                label="角色任务",
                scale=2,
                elem_classes=["ev-filter"],
            )
            run_selector = gr.Dropdown(
                choices=controller.runs(initial_suite, initial_task),
                value=initial_run_id,
                label="运行 / Seed",
                scale=4,
                elem_classes=["ev-filter"],
            )
            refresh_button = gr.Button(
                "刷新产物",
                variant="primary",
                scale=1,
                elem_classes=["ev-refresh"],
            )

        overview = gr.HTML(render_run_overview(initial_run))
        score_chart = gr.HTML(render_score_chart(initial_run))
        warnings = gr.Markdown(initial_warnings, visible=bool(initial_warnings))

        with gr.Tabs():
            with gr.Tab("逐轮转变"):
                gr.Markdown(
                    "### 转变卡片流\n按 iteration 排列。选择一张卡片查看该轮实际修改内容。"
                )
                transition_selector = gr.Radio(
                    choices=initial_choices,
                    value=initial_transition,
                    label="转变记录",
                    elem_classes=["transition-radio"],
                )
                transition_header = gr.HTML(initial_detail[0])
                parse_warning = gr.HTML(initial_detail[1])
                with gr.Tabs():
                    with gr.Tab("Prompt"):
                        prompt_view = gr.HTML(initial_detail[2])
                    with gr.Tab("Schema"):
                        schema_view = gr.HTML(initial_detail[3])
                    with gr.Tab("Write"):
                        write_view = gr.HTML(initial_detail[4])
                    with gr.Tab("Read"):
                        read_view = gr.HTML(initial_detail[5])
                    with gr.Tab("辅助代码"):
                        helper_view = gr.HTML(initial_detail[6])
                    with gr.Tab("完整 Python diff"):
                        raw_diff = gr.Code(
                            value=initial_detail[7],
                            label="父版本 → 子版本",
                            interactive=False,
                        )
                    with gr.Tab("诊断证据"):
                        diagnostics = gr.HTML(initial_detail[8])
                        with gr.Accordion("截断后的原始 trace", open=False):
                            raw_trace = gr.Code(
                                value=initial_detail[9],
                                language="json",
                                interactive=False,
                            )
                        with gr.Accordion("原始 lineage / score 记录", open=False):
                            raw_record = gr.Code(
                                value=initial_detail[10],
                                language="json",
                                interactive=False,
                            )

            with gr.Tab("Run 内累计 A/B"):
                gr.Markdown(
                    "### 任意候选对比\n仅比较当前 run 中具有实际 Python 文件的候选。"
                )
                with gr.Row():
                    before_selector = gr.Dropdown(
                        choices=program_choices, value=initial_a, label="A · 修改前"
                    )
                    after_selector = gr.Dropdown(
                        choices=program_choices, value=initial_b, label="B · 修改后"
                    )
                ab_header = gr.HTML(initial_ab[0])
                ab_parse_warning = gr.HTML(initial_ab[1])
                with gr.Tabs():
                    with gr.Tab("Prompt"):
                        ab_prompt = gr.HTML(initial_ab[2])
                    with gr.Tab("Schema"):
                        ab_schema = gr.HTML(initial_ab[3])
                    with gr.Tab("Write"):
                        ab_write = gr.HTML(initial_ab[4])
                    with gr.Tab("Read"):
                        ab_read = gr.HTML(initial_ab[5])
                    with gr.Tab("辅助代码"):
                        ab_helper = gr.HTML(initial_ab[6])
                    with gr.Tab("完整 Python diff"):
                        ab_diff = gr.Code(value=initial_ab[7], interactive=False)

        run_outputs = [
            overview,
            score_chart,
            warnings,
            transition_selector,
            before_selector,
            after_selector,
        ]
        detail_outputs = [
            transition_header,
            parse_warning,
            prompt_view,
            schema_view,
            write_view,
            read_view,
            helper_view,
            raw_diff,
            diagnostics,
            raw_trace,
            raw_record,
        ]
        ab_outputs = [
            ab_header,
            ab_parse_warning,
            ab_prompt,
            ab_schema,
            ab_write,
            ab_read,
            ab_helper,
            ab_diff,
        ]

        def bind_run_render(event):
            return (
                event.then(render_run_state, inputs=[run_selector], outputs=run_outputs)
                .then(
                    render_transition,
                    inputs=[run_selector, transition_selector],
                    outputs=detail_outputs,
                )
                .then(
                    render_ab,
                    inputs=[run_selector, before_selector, after_selector],
                    outputs=ab_outputs,
                )
            )

        suite_event = suite_selector.change(
            update_for_suite,
            inputs=[suite_selector],
            outputs=[task_selector, run_selector],
        )
        bind_run_render(suite_event)
        task_event = task_selector.change(
            update_for_task,
            inputs=[suite_selector, task_selector],
            outputs=[run_selector],
        )
        bind_run_render(task_event)
        bind_run_render(run_selector.change(lambda value: value, inputs=[run_selector]))
        transition_selector.change(
            render_transition,
            inputs=[run_selector, transition_selector],
            outputs=detail_outputs,
        )
        before_selector.change(
            render_ab,
            inputs=[run_selector, before_selector, after_selector],
            outputs=ab_outputs,
        )
        after_selector.change(
            render_ab,
            inputs=[run_selector, before_selector, after_selector],
            outputs=ab_outputs,
        )
        refresh_event = refresh_button.click(
            refresh_selectors,
            inputs=[run_selector],
            outputs=[suite_selector, task_selector, run_selector],
        )
        bind_run_render(refresh_event)

    return demo


def main():
    args = parse_args()
    try:
        demo = create_app(args.runs_dir)
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
