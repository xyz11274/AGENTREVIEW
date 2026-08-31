import unittest

from evolution_viewer.code_analysis import compare_programs
from evolution_viewer.render import (
    render_prompt_changes,
    render_schema_changes,
    render_transition_header,
)
from evolution_viewer.tests.test_code_analysis import AFTER, BEFORE


class RenderTest(unittest.TestCase):
    def test_prompt_view_has_three_modes_and_escapes_html(self):
        comparison = compare_programs(
            BEFORE,
            AFTER.replace(
                "Use relevant context and cite remembered signals.",
                "Use <script>alert(1)</script> context.",
            ),
        )

        value = render_prompt_changes(comparison)

        self.assertIn("完整文本", value)
        self.assertIn("仅看新增", value)
        self.assertIn("仅看删除", value)
        self.assertNotIn("<script>alert", value)
        self.assertIn("&lt;", value)

    def test_schema_view_states_exact_field_change(self):
        comparison = compare_programs(BEFORE, AFTER)

        value = render_schema_changes(comparison)

        self.assertIn("KnowledgeItem.signals", value)
        self.assertIn("新增", value)

    def test_failed_transition_is_visible(self):
        value = render_transition_header(
            {
                "iteration": 2,
                "status": "failed",
                "parent_id": "parent",
                "program_id": None,
                "change_summary": "Reflector failed",
                "change_details": [],
                "quality_passed": False,
                "admitted": False,
            },
            None,
        )

        self.assertIn("转变失败", value)
        self.assertIn("未生成候选", value)
        self.assertIn("Reflector failed", value)


if __name__ == "__main__":
    unittest.main()
