"""Render operation timings and live request progress from durable telemetry."""
from datetime import datetime
import json
from pathlib import Path
import time

from agentreview.research_v2.execution_telemetry import summarize
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.development_coco import failure_kind


def coco_attempt_summary(root):
    """Replay existing request spans, including retries absent from retry events."""
    path = Path(root) / 'telemetry/worker-events.jsonl'
    events, last = {}, {}
    if path.exists():
        # The telemetry reader already uses this lock to avoid partial JSON lines.
        import fcntl
        with path.open() as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            rows = handle.readlines()
        for line in rows:
            row = json.loads(line)
            if row.get('stage') != 'api_request' or not row.get('component', '').startswith('coco:'):
                continue
            span_id = row['span']
            if row['event'] == 'progress':
                last[span_id] = row
            elif row['event'] == 'end':
                events[span_id] = {**last.get(span_id, {}), **row}
    failures = {}
    for row in events.values():
        if row.get('status') == 'failed':
            error = row.get('error') or {}
            kind = failure_kind(error.get('type', ''), error.get('message', ''))
            failures[kind] = failures.get(kind, 0) + 1
    return {'attempts': len(events), 'repeated_attempts': sum(row.get('attempt', 1) > 1 for row in events.values()),
            'failed_attempts': sum(failures.values()), 'failure_kinds': failures,
            'scope': 'CoCo transport attempts; upstream cache-only parse retries are not network attempts'}


STAGES = {
    'pilot_preflight': '启动检查', 'pilot_search': '基线与自进化',
    'pilot_final_evaluation': '最终独立测试', 'pilot_held_out_round_trajectory': '逐轮独立测试',
    'experience_build': '构建审稿经验', 'build_paper': '单篇经验生成',
    'memory_build': '构建角色记忆', 'memory_retrieval': '检索角色记忆',
    'embedding': 'Embedding 编码与缓存', 'specialist_review': '角色审稿',
    'meta_review': 'Meta 汇总', 'reflection': '反思与程序修复',
    'program_validation': '程序验证', 'candidate_evaluation': '候选完整评价',
    'paper_full_flow': '单篇完整流程', 'coco_evaluation': 'CoCo 完整评分',
    'coco_split': 'CoCo Split', 'coco_classify': 'CoCo Classify',
    'coco_cpu_full_text_metrics': 'CoCo 文本指标', 'coco_category_judge': 'CoCo 分类维度 Judge',
    'coco_paper_judge': 'CoCo 整篇 Judge', 'coco_model_request': 'CoCo 模型请求',
    'api_request': 'API 请求尝试', 'api_admission': 'API 并发等待', 'api_retry_wait': 'API 重试间隔',
}


def report(root, state):
    root = Path(root)
    value = summarize(root / 'telemetry', now=state.get('finished_at'))
    value['updated_at'] = time.time()
    value.update(status=state.get('status', 'prepared'), phase=state.get('phase', 'awaiting_start'))
    value['coco_attempts'] = coco_attempt_summary(root)
    rounds_path = root / 'report/evolution-rounds.json'
    if rounds_path.exists():
        rounds = read_json(rounds_path)
        value['scoring_snapshot'] = {key: rounds.get(key) for key in (
            'current_epoch', 'displayed_epoch', 'complete_assessments_current_epoch',
            'valid_paper_evaluations_current_epoch', 'winner_frozen', 'recovery')}
        value['scoring_snapshot']['source'] = str(rounds_path)
    if value['status'] != 'running':
        for operation in value['active']:
            operation['status'] = 'interrupted'
    directory = root / 'report'
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(directory / 'runtime.json', value, immutable=False)
    updated = datetime.fromtimestamp(value['updated_at']).astimezone().isoformat(timespec='seconds')
    lines = ['# 实验流程耗时', '', f'更新时间：{updated}。状态：{value["status"]}；阶段：{value["phase"]}。', '',
             '耗时覆盖缓存读取和实际执行。各阶段含嵌套与并发操作，累计耗时用于阶段归因；总运行时长以外层阶段为准。',
             '下表“正常返回”仅表示函数调用结束，业务成功须以完整评分回执和正式 Winner 冻结为准。', '',
             '## 当前操作', '', '| 阶段 | 状态 | 已用秒数 | 距最近分片秒数 | 接收字符（正文 / 思考） |',
             '|---|---|---:|---:|---:|']
    for operation in value['active']:
        label = STAGES.get(operation['stage'], operation['stage'])
        context = [str(operation[key]) for key in ('component', 'role', 'program_version', 'run_id', 'paper_id', 'candidate')
                   if operation.get(key)]
        label += ' / ' + ', '.join(context) if context else ''
        lines.append(f'| {label} | {operation.get("status", "running")} | {operation["elapsed_seconds"]:.1f} | '
                     f'{operation["idle_seconds"]:.1f} | {operation.get("content_chars", 0)} / {operation.get("reasoning_chars", 0)} |')
    if not value['active']:
        lines.append('| 当前操作已结束 | — | — | — | — |')
    lines += ['', '## 阶段汇总', '', '| 阶段 | 正常返回 | 异常退出 | 中断 | 累计秒数 | 最长单次秒数 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for stage, stats in sorted(value['stages'].items()):
        lines.append(f'| {STAGES.get(stage, stage)} | {stats["complete"]} | {stats["failed"]} | '
                     f'{stats["interrupted"]} | {stats["seconds"]:.1f} | {stats["max_seconds"]:.1f} |')
    coco = value['coco_attempts']
    lines += ['', '## CoCo 请求诊断', '',
              f"尝试 {coco['attempts']} 次；重复尝试 {coco['repeated_attempts']} 次；失败 {coco['failed_attempts']} 次。",
              f"失败原因：{coco['failure_kinds']}。统计含官方客户端的实际重复请求，不含只读取缓存的解析重试。",
              '', '## 宿主最近重试', '']
    for retry in value['retries'][-20:]:
        lines.append(f'- {datetime.fromtimestamp(retry["time"]).astimezone().strftime("%H:%M:%S")} '
                     f'{retry["component"]}：第 {retry["attempt"]} 次结束，{retry["delay_seconds"]:.1f} 秒后进行第 '
                     f'{retry["next_attempt"]} 次；{retry["error_type"]}。')
    if not value['retries']:
        lines.append('宿主 retry 事件为 0；CoCo 内部请求尝试单独见上表。')
    lines += ['', '请求细节见各请求目录的 `attempt-N.progress.json`；收到的原始分片保存在 `attempt-N.stream.jsonl`。',
              '首包时间表示收到第一个 SSE 数据分片的时间。字符数统计正文和思考增量，实际 token 用量采用服务端 usage。', '']
    (directory / '流程耗时报告.md').write_text('\n'.join(lines))
    return value
