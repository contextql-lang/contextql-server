"""Combined demonstration scenarios at test scale (plan section 10, PR 13)."""
import pytest

from demo.post_trade_demo import run_demo


@pytest.fixture(scope="module")
def results():
    return run_demo(rows=20_000, verbose=False)


class TestCombinedDemonstration:
    def test_native_contexts_materialized(self, results):
        contexts = results["contexts"]
        assert contexts["settlement_intervention_required"]["cardinality"] > 0
        assert contexts["unmatched_trade"]["cardinality"] > 0
        for info in contexts.values():
            assert info["serialized_bytes"] > 0

    def test_bitmap_algebra(self, results):
        algebra = results["algebra"]
        assert algebra["union"] >= algebra["diff"]
        assert algebra["union"] > 0

    def test_top20_uses_pushdown(self, results):
        assert results["top20"]["rows"] == 20
        assert results["top20"]["pushdown"] is True
        assert 0 < results["top20"]["max_priority"] <= 1.0

    def test_deepsee_bootstrap(self, results):
        assert results["deepsee_bootstrap"]["members"] == 500
        assert results["deepsee_bootstrap"]["version"] == 1

    def test_deepsee_delta_promotes_new_version(self, results):
        delta = results["deepsee_delta"]
        assert delta["applied"] >= 4
        assert delta["version"] == 2
        assert delta["members"] == 500 + 3 - 1

    def test_combined_native_and_connector_query(self, results):
        assert results["combined_query"]["rows"] == 20
        assert results["combined_query"]["pushdown"] is True

    def test_evidence_join_after_narrowing(self, results):
        assert results["evidence_query"]["rows"] == 10
        assert results["evidence_query"]["has_actions"] is True
        assert results["evidence_query"]["requested_members"] == 502
        assert results["evidence_query"]["returned_ids_within_request"] is True

    def test_prior_snapshot_queryable(self, results):
        prior = results["prior_snapshot"]
        assert prior["v1_members"] == 500
        assert prior["current_members"] == 502
        assert prior["dropped_still_in_v1"] is True

    def test_provenance(self, results):
        provenance = results["provenance"]
        resolved = " ".join(provenance["contexts_resolved"])
        assert "settlement_intervention_required@v" in resolved
        assert "deepsee_settlement_risk@v" in resolved
        assert provenance["watermark"] is not None
        assert (
            provenance["snapshot_versions"]["settlement_intervention_required"]
            >= 1
        )
