"""Single-paper acceptance must close all bound reference assets, not only IDs."""
from pathlib import Path

from agentreview.research_v2.development_coco import _validate_inputs
from agentreview.research_v2.io import read_json, atomic_write_json
from test_research_v2_development_coco import experiment


def test_prepared_subset_preserves_reference_rows_and_passes_real_closure(experiment, tmp_path):
    from prepare_runtime_0930 import materialize_reference_subset
    source = experiment['reference_manifest_path']
    old_bytes = source.read_bytes()
    subset = materialize_reference_subset(source, ['paper-a'], tmp_path / 'subset')
    original = read_json(experiment['review_manifest_path'])
    original['paper_ids'] = ['paper-a']
    original['records'] = original['records'][:1]
    review = atomic_write_json(tmp_path / 'one-review.json', original)
    _, refs, candidates = _validate_inputs(review, subset, 'development')
    assert refs['paper_ids'] == ['paper-a'] and [row['id'] for row in candidates] == ['paper-a']
    assert read_json(Path(refs['reviewer_selection_file']['path'])) == {'paper-a': 'Reviewer 1'}
    assert source.read_bytes() == old_bytes
