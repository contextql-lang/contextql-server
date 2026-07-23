"""Tests for the mock DeepSee connector (plan section 8.3 contract cases)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import contextql as cql
from contextql.membership import SetMembershipStore
from contextql.providers import EntityFilter, MCPProvider, RemoteProvider

from app.config import Settings
from app.connectors.deepsee import (
    CredentialResolutionError,
    DeepSeeAuthError,
    DeepSeeClient,
    DeepSeeContractError,
    DeepSeeMCPProvider,
    DeepSeeRemoteProvider,
    DeepSeeRetryableError,
    DeepSeeStaleDataError,
    DeepSeeSynchronizer,
    DeepSeeTerminalError,
    MockDeepSeeService,
    resolve_credential,
)
from app.connectors.deepsee.auth import DEFAULT_CREDENTIAL_REFERENCE
from app.providers.registry import register_deepsee_mock

CONTEXT_ID = "settlement_risk"
_FIXED_NOW = datetime(2026, 7, 2, 0, 0, 0, tzinfo=timezone.utc)


def _fixed_now() -> datetime:
    return _FIXED_NOW


class RecordingSleeper:
    """Injectable sleeper that records delays instead of sleeping."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def make_client(service: MockDeepSeeService, **kwargs) -> DeepSeeClient:
    kwargs.setdefault("sleeper", RecordingSleeper())
    kwargs.setdefault("now", _fixed_now)
    return DeepSeeClient(service, **kwargs)


def make_synchronizer(
    client: DeepSeeClient, store: SetMembershipStore | None = None
) -> tuple[DeepSeeSynchronizer, SetMembershipStore]:
    store = store if store is not None else SetMembershipStore()
    sync = DeepSeeSynchronizer(client, store, CONTEXT_ID, now=_fixed_now)
    return sync, store


def clean_rebuild(service: MockDeepSeeService) -> SetMembershipStore:
    """Bootstrap a fresh store from the service's current state."""
    rebuild_service = MockDeepSeeService(members=service.current_members())
    sync, store = make_synchronizer(make_client(rebuild_service))
    sync.bootstrap()
    return store


class TestAuth:
    def test_resolves_env_reference(self, monkeypatch):
        monkeypatch.setenv("CQL_DEEPSEE_TOKEN", "resolved-value")
        assert resolve_credential(DEFAULT_CREDENTIAL_REFERENCE) == "resolved-value"

    def test_missing_env_var_raises_clear_error(self, monkeypatch):
        monkeypatch.delenv("CQL_DEEPSEE_TOKEN", raising=False)
        with pytest.raises(CredentialResolutionError) as excinfo:
            resolve_credential(DEFAULT_CREDENTIAL_REFERENCE)
        assert "CQL_DEEPSEE_TOKEN" in str(excinfo.value)
        assert "not set" in str(excinfo.value)

    def test_unsupported_scheme_rejected(self):
        with pytest.raises(CredentialResolutionError):
            resolve_credential("vault:secret/deepsee")

    def test_empty_reference_rejected(self):
        with pytest.raises(CredentialResolutionError):
            resolve_credential("")

    def test_reference_without_variable_rejected(self):
        with pytest.raises(CredentialResolutionError):
            resolve_credential("env:")

    def test_auth_required_without_credential_fails(self):
        service = MockDeepSeeService(require_auth=True)
        client = make_client(service)
        with pytest.raises(DeepSeeAuthError):
            client.fetch_snapshot()

    def test_auth_required_with_resolved_credential_succeeds(self, monkeypatch):
        monkeypatch.setenv("CQL_DEEPSEE_TOKEN", "any-token")
        service = MockDeepSeeService(require_auth=True)
        client = make_client(
            service, credential_reference=DEFAULT_CREDENTIAL_REFERENCE
        )
        page = client.fetch_snapshot()
        assert page.entity_ids

    def test_auth_failure_is_terminal_and_not_retried(self):
        service = MockDeepSeeService()
        service.queue_failure("auth")
        sleeper = RecordingSleeper()
        client = make_client(service, sleeper=sleeper)
        with pytest.raises(DeepSeeAuthError):
            client.fetch_snapshot()
        assert sleeper.delays == []
        assert service.call_count == 1


class TestClientRetries:
    def test_retryable_errors_retried_then_succeed(self):
        service = MockDeepSeeService()
        service.queue_failure("retryable", count=2)
        sleeper = RecordingSleeper()
        client = make_client(service, sleeper=sleeper)
        page = client.fetch_snapshot()
        assert page.entity_ids
        assert len(sleeper.delays) == 2
        assert service.call_count == 3

    def test_retries_exhausted_raises_retryable_error(self):
        service = MockDeepSeeService()
        service.queue_failure("retryable", count=5)
        client = make_client(service, max_retries=2)
        with pytest.raises(DeepSeeRetryableError):
            client.fetch_snapshot()

    def test_terminal_error_propagates_without_retry(self):
        service = MockDeepSeeService()
        service.queue_failure("terminal")
        sleeper = RecordingSleeper()
        client = make_client(service, sleeper=sleeper)
        with pytest.raises(DeepSeeTerminalError):
            client.fetch_snapshot()
        assert sleeper.delays == []
        assert service.call_count == 1

    def test_timeout_is_retried(self):
        service = MockDeepSeeService()
        service.queue_failure("timeout")
        client = make_client(service)
        assert client.fetch_snapshot().entity_ids

    def test_rate_limit_honors_retry_after(self):
        service = MockDeepSeeService()
        service.queue_failure("rate_limit")
        sleeper = RecordingSleeper()
        client = make_client(service, sleeper=sleeper)
        assert client.fetch_snapshot().entity_ids
        assert sleeper.delays == [0.1]  # retry_after_ms=100

    def test_backoff_is_capped(self):
        service = MockDeepSeeService()
        service.queue_failure("retryable", count=3)
        sleeper = RecordingSleeper()
        client = make_client(
            service, sleeper=sleeper,
            max_retries=3, backoff_base_ms=400, backoff_cap_ms=500,
        )
        client.fetch_snapshot()
        assert sleeper.delays == [0.4, 0.5, 0.5]


class TestClientContractValidation:
    def test_stale_data_as_of_rejected(self):
        service = MockDeepSeeService(stale_data_as_of=True)
        client = make_client(service, max_data_age=timedelta(hours=24))
        with pytest.raises(DeepSeeStaleDataError):
            client.fetch_snapshot()

    def test_fresh_data_accepted_with_staleness_bound(self):
        service = MockDeepSeeService()
        client = make_client(service, max_data_age=timedelta(days=30))
        assert client.fetch_snapshot().entity_ids

    def test_invalid_key_type_rejected(self):
        service = MockDeepSeeService(invalid_key_type=True)
        client = make_client(service)
        with pytest.raises(DeepSeeContractError) as excinfo:
            client.fetch_snapshot()
        assert "INT64" in str(excinfo.value)

    def test_malformed_bitmap_rejected_at_decode(self):
        service = MockDeepSeeService(
            bitmap_snapshot=True, malformed_bitmap=True
        )
        client = make_client(service)
        page = client.fetch_snapshot()
        with pytest.raises(DeepSeeContractError) as excinfo:
            client.decode_membership_bitmap(page)
        assert "Malformed" in str(excinfo.value)

    def test_oversized_bitmap_rejected_before_decode(self):
        service = MockDeepSeeService(bitmap_snapshot=True)
        client = make_client(service, max_payload_bytes=4)
        with pytest.raises(DeepSeeContractError) as excinfo:
            client.fetch_snapshot()
        assert "refusing to decode" in str(excinfo.value)


class TestSynchronizerBootstrap:
    def test_full_snapshot_bootstrap(self):
        service = MockDeepSeeService()
        sync, store = make_synchronizer(make_client(service))
        snapshot = sync.bootstrap()
        assert snapshot.member_count == len(service.current_members())
        assert store.members(CONTEXT_ID) == set(service.current_members())
        assert store.scores(CONTEXT_ID) == service.current_members()
        assert sync.committed_watermark == service.committed_watermark()

    def test_cursor_pagination_reassembles_full_membership(self):
        members = {i: 0.5 for i in range(1, 101)}
        service = MockDeepSeeService(members=members, page_size=7)
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()
        assert store.members(CONTEXT_ID) == set(members)

    def test_bitmap_snapshot_bootstrap(self):
        service = MockDeepSeeService(bitmap_snapshot=True)
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()
        assert store.members(CONTEXT_ID) == set(service.current_members())

    def test_malformed_bitmap_fails_bootstrap(self):
        service = MockDeepSeeService(
            bitmap_snapshot=True, malformed_bitmap=True
        )
        sync, store = make_synchronizer(make_client(service))
        with pytest.raises(DeepSeeContractError):
            sync.bootstrap()
        assert sync.committed_watermark is None

    def test_sync_before_bootstrap_rejected(self):
        service = MockDeepSeeService()
        sync, _ = make_synchronizer(make_client(service))
        with pytest.raises(DeepSeeContractError):
            sync.sync_once()


class TestSynchronizerIncremental:
    def test_incremental_additions_removals_and_score_changes(self):
        service = MockDeepSeeService()
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()
        existing = sorted(service.current_members())

        service.add_member(9001, 0.91)
        service.add_member(9002, 0.72)
        service.remove_member(existing[0])
        service.change_score(existing[1], 0.99)

        report = sync.sync_once()
        assert report.applied_additions == 2
        assert report.applied_removals == 1
        assert report.applied_score_changes == 1
        assert store.members(CONTEXT_ID) == set(service.current_members())
        assert store.scores(CONTEXT_ID) == service.current_members()

    def test_incremental_equals_clean_rebuild(self):
        service = MockDeepSeeService()
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()
        existing = sorted(service.current_members())

        service.add_member(7001, 0.61)
        service.remove_member(existing[2])
        service.change_score(existing[3], 0.05)
        sync.sync_once()

        rebuilt = clean_rebuild(service)
        assert store.members(CONTEXT_ID) == rebuilt.members(CONTEXT_ID)
        assert store.scores(CONTEXT_ID) == rebuilt.scores(CONTEXT_ID)

    def test_duplicate_delivery_is_idempotent(self):
        service = MockDeepSeeService(duplicate_delivery=True)
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()

        service.add_member(8001, 0.8)
        service.add_member(8002, 0.7)
        report = sync.sync_once()
        assert report.applied_additions == 2
        assert report.duplicates_skipped == 2

        rebuilt = clean_rebuild(service)
        assert store.members(CONTEXT_ID) == rebuilt.members(CONTEXT_ID)
        assert store.scores(CONTEXT_ID) == rebuilt.scores(CONTEXT_ID)

    def test_out_of_order_delivery_applies_in_watermark_order(self):
        service = MockDeepSeeService(out_of_order=True)
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()

        # Delivered reversed: score change would precede the addition.
        service.add_member(8100, 0.4)
        service.change_score(8100, 0.9)
        report = sync.sync_once()
        assert report.applied_total >= 1
        assert store.scores(CONTEXT_ID)[8100] == 0.9

    def test_events_at_or_before_boundary_rejected(self):
        service = MockDeepSeeService()
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()

        service.add_member(8200, 0.5)
        sync.sync_once()
        before = (store.members(CONTEXT_ID), store.scores(CONTEXT_ID))

        # Redeliver the whole history: everything is at/before the boundary
        # or an already-seen event, so nothing may be re-applied.
        service.redeliver_from_start = True
        report = sync.sync_once()
        assert report.applied_total == 0
        assert report.duplicates_skipped + report.out_of_order_rejected >= 1
        assert (store.members(CONTEXT_ID), store.scores(CONTEXT_ID)) == before

    def test_sync_with_no_changes_is_a_noop(self):
        service = MockDeepSeeService()
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()
        version_before = store.get_snapshot(CONTEXT_ID).version
        report = sync.sync_once()
        assert report.applied_total == 0
        assert store.get_snapshot(CONTEXT_ID).version == version_before

    def test_change_feed_pagination(self):
        service = MockDeepSeeService(page_size=3)
        sync, store = make_synchronizer(make_client(service))
        sync.bootstrap()
        for offset in range(10):
            service.add_member(8300 + offset, 0.5)
        report = sync.sync_once()
        assert report.applied_additions == 10
        assert store.members(CONTEXT_ID) == set(service.current_members())

    def test_watermark_committed_only_after_promotion(self):
        service = MockDeepSeeService()

        class FailingOnceStore(SetMembershipStore):
            def __init__(self) -> None:
                super().__init__()
                self.fail_next_delta = False

            def stage_delta(self, *args, **kwargs):
                if self.fail_next_delta:
                    self.fail_next_delta = False
                    raise RuntimeError("promotion failed")
                return super().stage_delta(*args, **kwargs)

        store = FailingOnceStore()
        sync, _ = make_synchronizer(make_client(service), store=store)
        sync.bootstrap()
        boundary = sync.committed_watermark

        service.add_member(8400, 0.5)
        store.fail_next_delta = True
        with pytest.raises(RuntimeError):
            sync.sync_once()
        assert sync.committed_watermark == boundary

        # Retry succeeds and applies the same event exactly once.
        report = sync.sync_once()
        assert report.applied_additions == 1
        assert 8400 in store.members(CONTEXT_ID)


class TestMCPProvider:
    def test_implements_protocol(self):
        provider = DeepSeeMCPProvider(make_client(MockDeepSeeService()))
        assert isinstance(provider, MCPProvider)

    def test_small_result_returns_entity_ids(self):
        service = MockDeepSeeService()
        provider = DeepSeeMCPProvider(make_client(service))
        result = provider.resolve("transaction", {})
        assert result.entity_ids is not None
        assert result.membership_bitmap is None
        assert set(result.entity_ids) == set(service.current_members())
        assert result.entity_key_type == "INT64"
        assert result.data_as_of is not None
        assert result.source_watermark == service.committed_watermark()
        assert result.score_map() == service.current_members()
        assert result.evidence_refs
        assert all(
            ref.startswith("deepsee://case/")
            for ref in result.evidence_refs.values()
        )

    def test_limit_returns_top_scored_subset(self):
        members = {1: 0.1, 2: 0.9, 3: 0.5, 4: 0.8}
        service = MockDeepSeeService(members=members)
        provider = DeepSeeMCPProvider(make_client(service))
        result = provider.resolve("transaction", {}, limit=2)
        assert sorted(result.entity_ids) == [2, 4]

    def test_large_result_returns_roaring64_bitmap(self):
        members = {i: 0.5 for i in range(1, 51)}
        service = MockDeepSeeService(members=members)
        provider = DeepSeeMCPProvider(
            make_client(service), bitmap_threshold=10
        )
        result = provider.resolve("transaction", {})
        assert result.entity_ids is None
        assert result.membership_bitmap is not None
        assert result.bitmap_encoding == "roaring64"
        assert sorted(result.membership_array().tolist()) == sorted(members)
        assert result.score_map() == members

    def test_wire_bitmap_passed_through_without_decode(self):
        service = MockDeepSeeService(bitmap_snapshot=True)
        provider = DeepSeeMCPProvider(make_client(service))
        result = provider.resolve("transaction", {})
        assert result.membership_bitmap is not None
        assert result.bitmap_encoding == "roaring64"
        assert set(result.membership_array().tolist()) == \
            set(service.current_members())

    def test_paginated_snapshot_reassembled(self):
        members = {i: 0.5 for i in range(1, 30)}
        service = MockDeepSeeService(members=members, page_size=4)
        provider = DeepSeeMCPProvider(
            make_client(service), bitmap_threshold=1000
        )
        result = provider.resolve("transaction", {})
        assert sorted(result.entity_ids) == sorted(members)


class TestRemoteProvider:
    def test_implements_protocol(self):
        provider = DeepSeeRemoteProvider(make_client(MockDeepSeeService()))
        assert isinstance(provider, RemoteProvider)

    def test_settlement_cases_rows_and_metadata(self):
        service = MockDeepSeeService()
        provider = DeepSeeRemoteProvider(make_client(service))
        result = provider.query("settlement_cases", {}, [])
        assert len(result.rows) == len(service.current_members())
        expected_fields = {
            "transaction_id", "break_type", "recommended_action",
            "agent_decision", "confidence", "explanation", "evidence_ref",
            "case_status", "owner", "last_reviewed_at",
        }
        assert set(result.rows[0]) == expected_fields
        assert set(result.schema) == expected_fields
        assert result.data_as_of is not None
        assert result.source_watermark == service.committed_watermark()

    def test_reconciliation_evidence_resource(self):
        provider = DeepSeeRemoteProvider(make_client(MockDeepSeeService()))
        result = provider.query("reconciliation_evidence", {}, [])
        assert result.rows
        assert "Reconciliation" in result.rows[0]["explanation"]

    def test_filters_columns_and_limit_applied(self):
        service = MockDeepSeeService()
        provider = DeepSeeRemoteProvider(make_client(service))
        result = provider.query(
            "settlement_cases",
            {"case_status": "open"},
            ["transaction_id", "case_status"],
            limit=3,
        )
        assert len(result.rows) <= 3
        assert all(set(row) == {"transaction_id", "case_status"}
                   for row in result.rows)
        assert all(row["case_status"] == "open" for row in result.rows)

    def test_large_entity_filter_stays_roaring_through_transport(self):
        from pyroaring import BitMap64

        service = MockDeepSeeService(
            members={value: 0.5 for value in range(12_000)}
        )
        provider = DeepSeeRemoteProvider(make_client(service))
        requested = BitMap64(range(10_001))
        result = provider.query(
            "settlement_cases",
            {},
            ["transaction_id"],
            entity_filter=EntityFilter(
                column="transaction_id",
                membership_bitmap=requested.serialize(),
                bitmap_encoding="roaring64",
            ),
        )
        assert len(result.rows) == 10_001
        assert (
            service.last_case_request["entity_filter_encoding"]
            == "roaring64"
        )
        assert (
            service.last_case_request["entity_filter_cardinality"]
            == 10_001
        )
        assert "transaction_id" not in service.last_case_request["filters"]

    def test_unknown_resource_rejected(self):
        provider = DeepSeeRemoteProvider(make_client(MockDeepSeeService()))
        with pytest.raises(DeepSeeContractError):
            provider.query("unknown_resource", {}, [])


class TestRegistration:
    def test_register_deepsee_mock_registers_both_roles(self):
        engine = cql.Engine()
        register_deepsee_mock(engine)
        assert "deepsee_settlement_risk" in engine._mcp_providers
        assert "deepsee" in engine._remote_providers

    def test_flag_defaults_to_disabled(self):
        assert Settings().register_deepsee_mock is False


class TestHardening:
    """Security-review hardening: pagination caps and watermark parsing."""

    def test_endless_cursor_chain_rejected(self, monkeypatch):
        from app.connectors.deepsee import synchronizer as sync_mod
        service = MockDeepSeeService(page_size=1)
        client = make_client(service)
        store = SetMembershipStore()
        synchronizer = DeepSeeSynchronizer(client, store, "risk")

        class EndlessPage:
            def __getattr__(self, name):
                if name == "next_cursor":
                    return "again"
                if name in ("entity_ids",):
                    return [1]
                if name in ("scores", "evidence_refs"):
                    return {}
                if name == "membership_bitmap":
                    return None
                return "0"

        monkeypatch.setattr(
            client, "fetch_snapshot", lambda cursor=None: EndlessPage()
        )
        monkeypatch.setattr(sync_mod, "MAX_PAGES", 5)
        with pytest.raises(DeepSeeContractError, match="pagination"):
            synchronizer.bootstrap()

    def test_malformed_watermark_is_contract_error(self):
        from app.connectors.deepsee.synchronizer import parse_watermark
        with pytest.raises(DeepSeeContractError, match="watermark"):
            parse_watermark("not-a-number")
        assert parse_watermark("000000000004") == 4
