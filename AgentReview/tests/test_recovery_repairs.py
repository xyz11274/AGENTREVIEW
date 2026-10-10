"""Regression scenarios from the failed 0930 pilot; no model requests."""
from pathlib import Path

import pytest

from agentreview.research_v2 import program_validation as validation
from agentreview.research_v2.io import read_json
from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.program_patch import extract_program
from test_mstar_adapter import RecordedClient, runner


@pytest.mark.parametrize('condition', [
    'item.reviewer_dimension == "POL"',
    'len(self.seen) > 0',
])
def test_validation_exercises_other_roles_and_multiple_writes(tmp_path, condition):
    source = seed_program_path('vector_search').read_text().replace(
        '        text = str(raw_text or item.content or "").strip()',
        f'        if {condition}:\n            raise ValueError("hidden write violation")\n'
        '        text = str(raw_text or item.content or "").strip()')
    path = tmp_path / 'program.py'
    path.write_text(source)
    errors = validation.validate_program(path, require_bubblewrap=True)
    assert any('hidden write violation' in error for error in errors), errors


def test_unapplicable_patch_has_actionable_error_instead_of_empty_string():
    source = seed_program_path('vector_search').read_text()
    response = '*** Begin Patch\n*** Update File: program.py\n@@ imaginary context\n-missing\n+new\n*** End Patch'
    with pytest.raises(ValueError, match='operation 1.*program.py'):
        extract_program(source, response)


def test_runtime_repair_can_correct_a_malformed_first_response(tmp_path):
    source = seed_program_path('vector_search').read_text()
    client = RecordedClient('No usable patch.', '```python\n' + source + '\n```')
    adapter = runner(tmp_path, client)
    code, _ = adapter.run(code=source, prompt='repair', iteration=1, score=0,
                          violation='frozenset cannot be checkpointed')
    assert code.strip() == source.strip()
    trace = read_json(tmp_path / 'mstar-trace.json')
    assert trace['protocol'] == 'mstar-program-evolution-v4'
    assert len(trace['calls']) == 2


def test_generated_report_preserves_unmarked_history_and_user_notes(tmp_path):
    from evolution_round_report import write_generated_report
    path = tmp_path / 'report.md'
    original = '# 实验复核\n\n用户结论。\n\n## 原始逐轮记录（epoch 0 历史成绩）\n旧记录。\n'
    path.write_text(original)
    write_generated_report(path, '第一版自动报告。\n')
    assert path.read_text().startswith(original)
    with path.open('a') as handle:
        handle.write('\n用户后续备注。\n')
    write_generated_report(path, '第二版自动报告。\n')
    text = path.read_text()
    assert text.startswith(original) and text.endswith('\n用户后续备注。\n')
    assert '第一版自动报告' not in text and text.count('第二版自动报告') == 1


def test_malformed_report_markers_do_not_overwrite_notes(tmp_path):
    from evolution_round_report import write_generated_report
    path = tmp_path / 'report.md'
    original = '用户文字\n<!-- AGENTREVIEW_AUTO_ROUNDS_BEGIN -->\n标记缺少结束。\n'
    path.write_text(original)
    with pytest.raises(ValueError, match='markers'):
        write_generated_report(path, 'new')
    assert path.read_text() == original
