from agentreview.research_v2.memory import seed_program_path
from agentreview.research_v2.program_validation import validate_program


def test_string_fence_parsing_and_list_slices_are_valid_program_operations(tmp_path):
    source = seed_program_path('vector_search').read_text()
    parsing = '''        text = str(raw_text or item.content or "").strip()
        if text.startswith("```json") and text.endswith("```"):
            text = text[7:-3].strip()
        lines = text.splitlines()
        lines = lines[1:] if lines and lines[0] == "HEADER" else lines
        text = "\\n".join(lines)'''
    source = source.replace('        text = str(raw_text or item.content or "").strip()', parsing)
    path = tmp_path / 'program.py'
    path.write_text(source)
    assert validate_program(path, require_bubblewrap=True,
                            memory_runtime={'build_llm_calls': 2, 'read_llm_calls': 2}, seed_kind='evolved') == []
