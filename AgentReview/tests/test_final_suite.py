import unittest

from agentreview.final_suite import (
    FINAL_ROLE_ORDER,
    ProtectedOfficialAccess,
    RoleWinner,
    assemble_specialist_review_bundle,
    freeze_final_suite,
)


def winners():
    return {
        role: RoleWinner(
            role=role,
            role_run_identity=f"run-{role}",
            candidate_id=f"candidate-{role}",
            program_identity_sha256=(str(index + 1) * 64),
            full_pool_fitness="3.000000000000",
        )
        for index, role in enumerate(FINAL_ROLE_ORDER)
    }


class FinalSuiteTest(unittest.TestCase):
    def test_suite_is_five_winners_in_fixed_order_with_meta_off(self):
        suite = freeze_final_suite(winners())
        self.assertEqual(tuple(item.role for item in suite.winners), FINAL_ROLE_ORDER)
        self.assertFalse(suite.meta_enabled)
        self.assertEqual(len(suite.suite_identity_sha256), 64)

    def test_protected_inputs_are_unreadable_before_suite_authorization(self):
        reads = []
        access = ProtectedOfficialAccess(lambda: reads.append("C") or {"C": "protected"})
        suite = freeze_final_suite(winners())
        with self.assertRaises(PermissionError):
            access.read_protected_inputs(suite)
        self.assertEqual(reads, [])
        access.authorize(suite)
        self.assertEqual(access.read_protected_inputs(suite), {"C": "protected"})
        self.assertTrue(access.sealed)
        access.complete(suite)
        with self.assertRaises(RuntimeError):
            access.read_protected_inputs(suite)

    def test_official_bundle_is_direct_five_role_composition_without_meta(self):
        values = {role: f"{role} review" for role in FINAL_ROLE_ORDER}
        bundle = assemble_specialist_review_bundle(
            paper_id="C-1", specialist_reviews=values
        )
        self.assertNotIn("META", bundle)
        positions = [bundle.index(f"role={role}") for role in FINAL_ROLE_ORDER]
        self.assertEqual(positions, sorted(positions))
        for role in FINAL_ROLE_ORDER:
            self.assertEqual(bundle.count(values[role]), 1)


if __name__ == "__main__":
    unittest.main()
