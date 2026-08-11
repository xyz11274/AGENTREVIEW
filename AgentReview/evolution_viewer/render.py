"""HTML and text rendering for the standalone M★ evolution viewer."""

from __future__ import annotations

import html
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from .code_analysis import changed_sections, compare_programs, word_diff
except ImportError:  # Allows `python evolution_viewer/app.py`.
    from code_analysis import changed_sections, compare_programs, word_diff


STATUS_LABELS = {
    "seed": "初始 Seed",
    "success": "已生成候选",
    "failed": "转变失败",
    "quality_failed": "质量门禁失败",
    "missing_code": "缺少程序文件",
    "missing_lineage": "缺少 Lineage",
}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _number(value: Any, digits: int = 4) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    if not math.isfinite(float(value)):
        return "—"
    return f"{float(value):.{digits}f}"


def _delta(before: Any, after: Any) -> Tuple[str, str]:
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return "—", "neutral"
    value = float(after) - float(before)
    css = "positive" if value > 0 else ("negative" if value < 0 else "neutral")
    return f"{value:+.4f}", css


def warnings_markdown(warnings: Iterable[str]) -> str:
    values = [str(value) for value in warnings if value]
    if not values:
        return ""
    return "\n".join(["### 数据告警", *[f"- {value}" for value in values]])


def render_run_overview(run: Dict[str, Any]) -> str:
    config = run.get("config") or {}
    transformations = run.get("transformations") or []
    successful = [
        item
        for item in transformations
        if item.get("program_id") and item.get("status") in {"seed", "success"}
    ]
    failures = [
        item
        for item in transformations
        if item.get("status") not in {"seed", "success"}
    ]
    best_id = run.get("best_program_id")
    best = (run.get("programs") or {}).get(best_id, {})
    return f"""
    <section class="ev-hero">
      <div>
        <div class="ev-kicker">M★ EVOLUTION AUDIT</div>
        <h2>{_e(run.get("label"))}</h2>
        <p>只读呈现程序在每轮中发生的实际变化；解释来自代码事实与原始 change_log。</p>
      </div>
      <div class="ev-metrics">
        <div><span>任务</span><strong>{_e(config.get("task_id") or run.get("task"))}</strong></div>
        <div><span>Seed</span><strong>{_e(config.get("seed", "—"))}</strong></div>
        <div><span>记录</span><strong>{len(transformations)}</strong></div>
        <div><span>失败</span><strong>{len(failures)}</strong></div>
        <div><span>最佳候选</span><strong>{_e(best_id or "未选择")}</strong></div>
        <div><span>最佳 static</span><strong>{_number(best.get("static_score"))}</strong></div>
      </div>
      <div class="ev-config-strip">
        <span>mutation_scope <b>{_e(config.get("mutation_scope", "—"))}</b></span>
        <span>parent_policy <b>{_e(config.get("parent_policy", "—"))}</b></span>
        <span>temperature <b>{_e(config.get("temperature", "—"))}</b></span>
        <span>iterations <b>{_e(config.get("iterations", "—"))}</b></span>
      </div>
    </section>
    """


def _polyline_points(
    records: List[Dict[str, Any]],
    field: str,
    width: int,
    height: int,
    padding_x: int,
    padding_y: int,
    *,
    skip_seed_zero: bool = False,
) -> Tuple[List[str], List[Tuple[float, float, Dict[str, Any]]]]:
    values = []
    count = max(1, len(records) - 1)
    for index, item in enumerate(records):
        value = item.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if skip_seed_zero and int(item.get("iteration") or 0) == 0:
            continue
        x = padding_x + (width - 2 * padding_x) * index / count
        normalized = min(1.0, max(0.0, float(value)))
        y = height - padding_y - normalized * (height - 2 * padding_y)
        values.append((x, y, item))
    points = [f"{x:.1f},{y:.1f}" for x, y, _ in values]
    return points, values


def render_score_chart(run: Dict[str, Any]) -> str:
    records = list(run.get("transformations") or [])
    if not records:
        return '<div class="ev-empty">暂无分数记录。</div>'
    width, height = 980, 230
    pad_x, pad_y = 42, 28
    static_points, static_values = _polyline_points(
        records, "static_score", width, height, pad_x, pad_y
    )
    rotating_points, rotating_values = _polyline_points(
        records,
        "rotating_score",
        width,
        height,
        pad_x,
        pad_y,
        skip_seed_zero=True,
    )
    grid = []
    for value in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = height - pad_y - value * (height - 2 * pad_y)
        grid.append(
            f'<line x1="{pad_x}" y1="{y:.1f}" x2="{width-pad_x}" y2="{y:.1f}" '
            'stroke="currentColor" opacity=".18"/>'
            f'<text x="6" y="{y+4:.1f}" fill="currentColor" opacity=".82" '
            f'font-size="12" font-weight="600">{value:.2f}</text>'
        )

    def circles(values: List[Tuple[float, float, Dict[str, Any]]], css: str, field: str) -> str:
        blocks = []
        for x, y, item in values:
            title = (
                f"iteration {item.get('iteration')} · "
                f"{item.get('program_id') or 'failed'} · "
                f"{field}={_number(item.get(field))}"
            )
            blocks.append(
                f'<circle class="{css}" cx="{x:.1f}" cy="{y:.1f}" r="4.5">'
                f"<title>{_e(title)}</title></circle>"
            )
        return "".join(blocks)

    static_line = (
        f'<polyline points="{" ".join(static_points)}" class="ev-line-static"/>'
        if static_points
        else ""
    )
    rotating_line = (
        f'<polyline points="{" ".join(rotating_points)}" class="ev-line-rotating"/>'
        if rotating_points
        else ""
    )
    return f"""
    <section class="ev-panel">
      <div class="ev-panel-title">
        <div><span class="ev-dot static"></span>static score</div>
        <div><span class="ev-dot rotating"></span>rotating score（本轮父版本）</div>
        <small>分数是辅助证据，不代表代码变化与效果之间的因果关系。</small>
      </div>
      <svg class="ev-chart" viewBox="0 0 {width} {height}" role="img"
           aria-label="Static and rotating score progression">
        {''.join(grid)}
        {static_line}{rotating_line}
        {circles(static_values, "ev-point-static", "static_score")}
        {circles(rotating_values, "ev-point-rotating", "rotating_score")}
      </svg>
    </section>
    """


def comparison_for_transition(
    run: Dict[str, Any], transformation: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    program_id = transformation.get("program_id")
    parent_id = transformation.get("parent_id")
    if not program_id:
        return None
    if not parent_id:
        return compare_programs(
            "",
            transformation.get("source", ""),
            before_name="（起点）",
            after_name=f"{program_id}.py",
        )
    parent = (run.get("programs") or {}).get(parent_id)
    if not isinstance(parent, dict) or not parent.get("source"):
        return None
    return compare_programs(
        parent.get("source", ""),
        transformation.get("source", ""),
        before_name=f"{parent_id}.py",
        after_name=f"{program_id}.py",
    )


def transition_choices(run: Dict[str, Any]) -> List[Tuple[str, str]]:
    choices: List[Tuple[str, str]] = []
    for item in run.get("transformations") or []:
        iteration = int(item.get("iteration") or 0)
        program_id = item.get("program_id") or "未生成候选"
        parent_id = item.get("parent_id") or "起点"
        status = STATUS_LABELS.get(item.get("status"), str(item.get("status")))
        comparison = comparison_for_transition(run, item)
        sections = changed_sections(comparison) if comparison else []
        section_text = " · ".join(sections) if sections else status
        delta_text, _ = _delta(item.get("parent_static_score"), item.get("static_score"))
        summary = " ".join(str(item.get("change_summary") or "").split())
        if len(summary) > 92:
            summary = summary[:89] + "..."
        label = (
            f"迭代 {iteration:03d}｜{parent_id} → {program_id}｜"
            f"{section_text}｜Δ {delta_text}"
        )
        if summary:
            label += f"｜{summary}"
        choices.append((label, str(item["uid"])))
    return choices


def default_transition_uid(run: Dict[str, Any]) -> Optional[str]:
    values = list(run.get("transformations") or [])
    non_seed = [item for item in values if item.get("status") != "seed"]
    selected = non_seed[-1] if non_seed else (values[0] if values else None)
    return str(selected["uid"]) if selected else None


def find_transformation(run: Dict[str, Any], uid: Optional[str]) -> Dict[str, Any]:
    for item in run.get("transformations") or []:
        if str(item.get("uid")) == str(uid):
            return item
    return {}


def _status_badge(status: str) -> str:
    css = "ok" if status in {"seed", "success"} else "bad"
    return f'<span class="ev-badge {css}">{_e(STATUS_LABELS.get(status, status))}</span>'


def render_transition_header(item: Dict[str, Any], comparison: Optional[Dict[str, Any]]) -> str:
    if not item:
        return '<div class="ev-empty">请选择一条转变记录。</div>'
    delta_text, delta_css = _delta(
        item.get("parent_static_score"), item.get("static_score")
    )
    sections = changed_sections(comparison) if comparison else []
    tags = "".join(f'<span class="ev-tag">{_e(value)}</span>' for value in sections)
    details = item.get("change_details") or []
    detail_list = (
        "<ul>"
        + "".join(f"<li>{_e(value)}</li>" for value in details)
        + "</ul>"
        if details
        else '<p class="ev-muted">未记录 change_log.details。</p>'
    )
    return f"""
    <section class="ev-transition-head">
      <div class="ev-transition-top">
        <div>
          <span class="ev-iteration">ITERATION {int(item.get("iteration") or 0):03d}</span>
          {_status_badge(str(item.get("status")))}
          {tags}
        </div>
        <div class="ev-score-delta {delta_css}">{_e(delta_text)}</div>
      </div>
      <h3><code>{_e(item.get("parent_id") or "起点")}</code>
          <span class="ev-arrow">→</span>
          <code>{_e(item.get("program_id") or "未生成候选")}</code></h3>
      <div class="ev-score-row">
        <span>parent static <b>{_number(item.get("parent_static_score"))}</b></span>
        <span>child static <b>{_number(item.get("static_score"))}</b></span>
        <span>parent rotating <b>{_number(item.get("rotating_score"))}</b></span>
      </div>
      <div class="ev-reflector">
        <span>REFLECTOR CHANGE LOG</span>
        <strong>{_e(item.get("change_summary") or "没有变更摘要")}</strong>
        {detail_list}
      </div>
    </section>
    """


def _tagged_tokens(tokens: Iterable[Tuple[str, str]]) -> str:
    parts = []
    for status, token in tokens:
        css = {
            "delete": "ev-word-del",
            "insert": "ev-word-add",
            "equal": "ev-word-equal",
        }.get(status, "ev-word-equal")
        parts.append(f'<span class="{css}">{_e(token)}</span>')
    return "".join(parts)


def render_prompt_changes(comparison: Optional[Dict[str, Any]]) -> str:
    if comparison is None:
        return '<div class="ev-empty">该记录没有可用的父子程序，无法生成 Prompt 对比。</div>'
    blocks = []
    for item in comparison.get("prompts") or []:
        status = item.get("status")
        changed = status != "unchanged"
        old_tokens, new_tokens = word_diff(item.get("before", ""), item.get("after", ""))
        open_attr = " open" if changed else ""
        label = {
            "added": "新增",
            "removed": "删除",
            "modified": "已修改",
            "unchanged": "未变化",
        }.get(status, status)
        control_id = "prompt-" + "".join(
            character.lower() if character.isalnum() else "-"
            for character in str(item.get("name"))
        )
        controls = (
            f"""
            <div class="ev-prompt-modes">
              <input type="radio" id="{control_id}-full" name="{control_id}" checked>
              <label for="{control_id}-full">完整文本</label>
              <input type="radio" id="{control_id}-added" name="{control_id}">
              <label for="{control_id}-added">仅看新增</label>
              <input type="radio" id="{control_id}-removed" name="{control_id}">
              <label for="{control_id}-removed">仅看删除</label>
            """
            if changed
            else '<div class="ev-prompt-modes ev-no-controls">'
        )
        blocks.append(
            f"""
            <details class="ev-diff-card"{open_attr}>
              <summary><code>{_e(item.get("name"))}</code>
                <span class="ev-change-state {status}">{_e(label)}</span></summary>
              {controls}
                <div class="ev-side-by-side ev-prompt-sides">
                  <div class="ev-prompt-before"><h4>修改前</h4><pre>{_tagged_tokens(old_tokens) or "（空）"}</pre></div>
                  <div class="ev-prompt-after"><h4>修改后</h4><pre>{_tagged_tokens(new_tokens) or "（空）"}</pre></div>
                </div>
              </div>
            </details>
            """
        )
    return "".join(blocks)


def _field_description(value: Optional[Dict[str, Any]]) -> str:
    if not value:
        return "—"
    annotation = value.get("annotation") or "未标注类型"
    default = value.get("default")
    return f"{annotation}" + (f" = {default}" if default else "")


def render_schema_changes(comparison: Optional[Dict[str, Any]]) -> str:
    if comparison is None:
        return '<div class="ev-empty">没有可比较的 Schema。</div>'
    changes = comparison.get("schema") or []
    if not changes:
        return '<div class="ev-empty">KnowledgeItem 与 Query 的字段没有变化。</div>'
    rows = []
    labels = {"added": "新增", "removed": "删除", "modified": "修改"}
    for item in changes:
        rows.append(
            "<tr>"
            f"<td><code>{_e(item.get('class'))}.{_e(item.get('field'))}</code></td>"
            f"<td><span class='ev-change-state {item.get('status')}'>{_e(labels.get(item.get('status'), item.get('status')))}</span></td>"
            f"<td><code>{_e(_field_description(item.get('before')))}</code></td>"
            f"<td><code>{_e(_field_description(item.get('after')))}</code></td>"
            "</tr>"
        )
    return (
        '<div class="ev-table-wrap"><table class="ev-table">'
        "<thead><tr><th>字段</th><th>变化</th><th>修改前</th><th>修改后</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _function_facts(item: Dict[str, Any]) -> str:
    facts = []
    if item.get("signature_before") != item.get("signature_after"):
        facts.append(
            f"函数签名：{item.get('signature_before') or '不存在'} → "
            f"{item.get('signature_after') or '不存在'}"
        )
    calls = item.get("calls") or {}
    if calls.get("added"):
        facts.append("新增调用：" + ", ".join(calls["added"]))
    if calls.get("removed"):
        facts.append("删除调用：" + ", ".join(calls["removed"]))
    if item.get("branches_before") != item.get("branches_after"):
        before = ", ".join(
            f"{key}={value}" for key, value in item.get("branches_before", {}).items()
        )
        after = ", ".join(
            f"{key}={value}" for key, value in item.get("branches_after", {}).items()
        )
        facts.append(f"控制结构计数：[{before}] → [{after}]")
    if not facts:
        facts.append("函数体文本发生变化。")
    return "".join(f"<li>{_e(value)}</li>" for value in facts)


def _render_function_changes(
    values: List[Dict[str, Any]], empty_message: str
) -> str:
    if not values:
        return f'<div class="ev-empty">{_e(empty_message)}</div>'
    blocks = []
    labels = {"added": "新增", "removed": "删除", "modified": "修改"}
    for item in values:
        blocks.append(
            f"""
            <details class="ev-code-card" open>
              <summary><code>{_e(item.get("name"))}</code>
                <span class="ev-change-state {item.get('status')}">{_e(labels.get(item.get("status"), item.get("status")))}</span>
              </summary>
              <ul>{_function_facts(item)}</ul>
              <div class="ev-side-by-side ev-code-side">
                <div><h4>修改前</h4><pre>{_e(item.get("source_before") or "（不存在）")}</pre></div>
                <div><h4>修改后</h4><pre>{_e(item.get("source_after") or "（不存在）")}</pre></div>
              </div>
            </details>
            """
        )
    return "".join(blocks)


def render_write_changes(comparison: Optional[Dict[str, Any]]) -> str:
    if comparison is None:
        return '<div class="ev-empty">没有可比较的 write()。</div>'
    return _render_function_changes(
        comparison.get("write") or [], "KnowledgeBase.write() 没有变化。"
    )


def render_read_changes(comparison: Optional[Dict[str, Any]]) -> str:
    if comparison is None:
        return '<div class="ev-empty">没有可比较的 read()。</div>'
    return _render_function_changes(
        comparison.get("read") or [], "KnowledgeBase.read() 没有变化。"
    )


def render_helper_changes(comparison: Optional[Dict[str, Any]]) -> str:
    if comparison is None:
        return '<div class="ev-empty">没有可比较的辅助代码。</div>'
    imports = comparison.get("imports") or {}
    import_facts = []
    if imports.get("added"):
        import_facts.append("新增 import：" + ", ".join(imports["added"]))
    if imports.get("removed"):
        import_facts.append("删除 import：" + ", ".join(imports["removed"]))
    import_html = (
        '<div class="ev-fact-box"><strong>Import 变化</strong><ul>'
        + "".join(f"<li>{_e(value)}</li>" for value in import_facts)
        + "</ul></div>"
        if import_facts
        else ""
    )
    helpers = _render_function_changes(
        comparison.get("helpers") or [], "辅助函数和 import 均没有变化。"
    )
    return import_html + helpers


def render_parse_warnings(comparison: Optional[Dict[str, Any]]) -> str:
    if not comparison:
        return ""
    errors = comparison.get("parse_errors") or {}
    values = [
        f"{side}: {message}" for side, message in errors.items() if message
    ]
    if not values:
        return ""
    return (
        '<div class="ev-alert"><strong>AST 解析失败，已保留文本 diff：</strong><ul>'
        + "".join(f"<li>{_e(value)}</li>" for value in values)
        + "</ul></div>"
    )


def render_diagnostics(
    item: Dict[str, Any], trace: Dict[str, Any]
) -> str:
    if not item:
        return '<div class="ev-empty">请选择转变记录。</div>'
    quality = item.get("quality") or {}
    errors = (
        list(item.get("compile_errors") or [])
        + list(quality.get("static_errors") or [])
        + list(quality.get("smoke_errors") or [])
    )
    if item.get("error"):
        errors.insert(0, item["error"])
    unique_errors = list(dict.fromkeys(str(value) for value in errors if value))
    error_html = (
        "<ul>" + "".join(f"<li><code>{_e(value)}</code></li>" for value in unique_errors) + "</ul>"
        if unique_errors
        else '<p class="ev-muted">未记录编译或质量门禁错误。</p>'
    )
    counts = trace.get("counts") or {}
    trace_counts = " · ".join(f"{_e(key)}={value}" for key, value in counts.items())
    failures = trace.get("failures") or []
    failure_rows = ""
    for row in failures[:30]:
        failure_rows += (
            "<tr>"
            f"<td>{row.get('index')}</td><td>{_e(row.get('phase'))}</td>"
            f"<td><code>{_e(row.get('case_id'))}</code></td>"
            f"<td>{_number(row.get('duration_seconds'), 3)}</td>"
            f"<td><code>{_e(row.get('error'))}</code></td>"
            "</tr>"
        )
    failures_html = (
        '<div class="ev-table-wrap"><table class="ev-table"><thead><tr>'
        "<th>#</th><th>阶段</th><th>Case</th><th>秒</th><th>错误</th>"
        f"</tr></thead><tbody>{failure_rows}</tbody></table></div>"
        if failure_rows
        else '<p class="ev-muted">trace 中没有失败记录。</p>'
    )
    return f"""
    <div class="ev-diagnostics">
      <section>
        <h4>质量门禁</h4>
        <div class="ev-score-row">
          <span>passed <b>{_e(item.get("quality_passed", "—"))}</b></span>
          <span>compile fix <b>{_e(item.get("compile_fix_attempts", 0))}</b></span>
          <span>admitted <b>{_e(item.get("admitted"))}</b></span>
        </div>
        {error_html}
      </section>
      <section>
        <h4>Rotating trace 摘要</h4>
        <p>{trace_counts or "该迭代没有 trace 文件。"}</p>
        {failures_html}
      </section>
    </div>
    """


def raw_record_json(item: Dict[str, Any]) -> str:
    if not item:
        return "{}"
    return json.dumps(
        {
            "lineage": item.get("raw_lineage") or {},
            "score": item.get("raw_score") or {},
        },
        ensure_ascii=False,
        indent=2,
    )


def comparison_payload(
    run: Dict[str, Any], before_id: Optional[str], after_id: Optional[str]
) -> Optional[Dict[str, Any]]:
    programs = run.get("programs") or {}
    before = programs.get(before_id)
    after = programs.get(after_id)
    if not isinstance(before, dict) or not isinstance(after, dict):
        return None
    return compare_programs(
        before.get("source", ""),
        after.get("source", ""),
        before_name=f"{before_id}.py",
        after_name=f"{after_id}.py",
    )


def render_ab_header(
    run: Dict[str, Any],
    before_id: Optional[str],
    after_id: Optional[str],
    comparison: Optional[Dict[str, Any]],
) -> str:
    if comparison is None:
        return '<div class="ev-empty">请选择两个具有 Python 文件的候选程序。</div>'
    before = (run.get("programs") or {}).get(before_id, {})
    after = (run.get("programs") or {}).get(after_id, {})
    delta_text, delta_css = _delta(
        before.get("static_score"), after.get("static_score")
    )
    tags = "".join(
        f'<span class="ev-tag">{_e(label)}</span>'
        for label in changed_sections(comparison)
    )
    return f"""
    <section class="ev-transition-head">
      <div class="ev-transition-top">
        <div><span class="ev-iteration">RUN 内累计对比</span>{tags}</div>
        <div class="ev-score-delta {delta_css}">{_e(delta_text)}</div>
      </div>
      <h3><code>{_e(before_id)}</code><span class="ev-arrow">→</span>
          <code>{_e(after_id)}</code></h3>
      <div class="ev-score-row">
        <span>A static <b>{_number(before.get("static_score"))}</b></span>
        <span>B static <b>{_number(after.get("static_score"))}</b></span>
      </div>
    </section>
    """
