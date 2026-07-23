"""Combined post-trade demonstration (plan section 10).

Runs the ten demonstration scenarios end to end: native Roaring context
bitmaps over the deterministic post-trade dataset, bitmap algebra, the
top-priority intervention query, mock DeepSee membership bootstrap and
incremental delta, native+connector composition, REMOTE evidence join after
narrowing, prior-snapshot access, and full provenance.

Usage:
    python demo/post_trade_demo.py [--rows N]

The demo uses the mock DeepSee connector (no real DeepSee API exists yet —
see DEEPSEE_CONNECTOR.md). All data is synthetic.
"""
from __future__ import annotations

import argparse
from typing import Any, Dict

import contextql as cql
from contextql.datasets import generate_post_trade_dataset
from contextql.datasets.post_trade import DEFAULT_AS_OF, REFERENCE_CONTEXT_SQL
from contextql.semantic import TableCatalogEntry

from app.connectors.deepsee import (
    DeepSeeClient,
    DeepSeeRemoteProvider,
    DeepSeeSynchronizer,
    MockDeepSeeService,
)

AS_OF = DEFAULT_AS_OF

FLAGSHIP_DDL = f"""
CREATE CONTEXT settlement_intervention_required
ON transaction_id
SCORE intervention_priority
WITH (materialized = TRUE, storage = 'roaring')
AS
SELECT
    transaction_id,
    LEAST(
        1.0,
        predicted_fail_probability
        + CASE WHEN ssi_valid = FALSE THEN 0.20 ELSE 0 END
        + CASE WHEN match_status <> 'matched' THEN 0.15 ELSE 0 END
    ) AS intervention_priority
FROM transactions
WHERE settlement_status NOT IN ('settled', 'cancelled')
  AND (
      contractual_settle_date < CAST(TIMESTAMP '{AS_OF}' AS DATE)
      OR market_cutoff_at <= TIMESTAMP '{AS_OF}' + INTERVAL '2 hours'
  )
  AND (
      match_status <> 'matched'
      OR ssi_valid = FALSE
      OR confirmation_received = FALSE
      OR fields_mismatched > 0
      OR predicted_fail_probability >= 0.80
  );
"""

NATIVE_CONTEXTS = ["unmatched_trade", "high_notional", "invalid_ssi"]

COMBINED_QUERY = """
SELECT transaction_id, counterparty_id, notional_usd,
       CONTEXT_SCORE() AS priority
FROM transactions
WHERE CONTEXT IN (settlement_intervention_required, deepsee_settlement_risk)
ORDER BY CONTEXT DESC
LIMIT 20;
"""

EVIDENCE_QUERY = """
SELECT t.transaction_id, t.counterparty_id, t.notional_usd,
       d.break_type, d.recommended_action,
       CONTEXT_SCORE() AS priority
FROM transactions AS t
LEFT JOIN REMOTE(deepsee.settlement_cases) AS d
  ON t.transaction_id = d.transaction_id
WHERE CONTEXT ON t IN (deepsee_settlement_risk)
ORDER BY CONTEXT DESC
LIMIT 10;
"""


def _say(verbose: bool, message: str) -> None:
    if verbose:
        print(message)


def run_demo(
    rows: int = 10_000_000,
    database: str = ":memory:",
    verbose: bool = True,
) -> Dict[str, Any]:
    """Run all ten scenarios; returns per-scene results for verification."""
    results: Dict[str, Any] = {}

    # Scene 1 — native context bitmaps over the dataset
    engine = cql.Engine(database=database)
    conn = engine._adapter.conn
    _say(verbose, f"[1] generating {rows:,} deterministic transactions ...")
    generate_post_trade_dataset(conn, rows, as_of=AS_OF)
    engine._catalog.tables["transactions"] = TableCatalogEntry(
        name="transactions",
        primary_key_name="transaction_id",
        primary_key_type="INT64",
    )
    engine.execute(FLAGSHIP_DDL)
    for name in NATIVE_CONTEXTS:
        engine.execute(
            f"CREATE CONTEXT {name} ON transaction_id "
            "WITH (materialized = TRUE, storage = 'roaring') AS "
            + REFERENCE_CONTEXT_SQL[name].format(
                table="transactions", as_of=AS_OF
            )
            + ";"
        )
    engine.execute("REFRESH ALL CONTEXTS;")
    store = engine.membership

    # Scene 2 — cardinality and serialized size
    results["contexts"] = {
        name: {
            "cardinality": store.get_snapshot(name).member_count,
            "serialized_bytes": len(store.serialize(name)),
        }
        for name in ["settlement_intervention_required", *NATIVE_CONTEXTS]
    }
    for name, info in results["contexts"].items():
        _say(
            verbose,
            f"[2] {name}: {info['cardinality']:,} members, "
            f"{info['serialized_bytes']:,} serialized bytes",
        )

    # Scene 3 — union, intersection, difference
    compose = store.compose if hasattr(store, "compose") else None
    if compose is not None:
        union = compose(union_of=["unmatched_trade", "invalid_ssi"])
        inter = compose(
            intersect_of=["high_notional", "settlement_intervention_required"]
        )
        diff = compose(
            union_of=["unmatched_trade"], subtract=["invalid_ssi"]
        )
        results["algebra"] = {
            "union": len(union), "intersect": len(inter), "diff": len(diff),
        }
        _say(verbose, f"[3] algebra: {results['algebra']}")

    # Scene 4 — highest-priority interventions
    top = engine.execute(
        """
        SELECT transaction_id, notional_usd, CONTEXT_SCORE() AS priority
        FROM transactions
        WHERE CONTEXT IN (settlement_intervention_required)
        ORDER BY CONTEXT DESC LIMIT 20;
        """
    )
    results["top20"] = {
        "rows": len(top.to_pandas()),
        "pushdown": "__cql_members_0" in top.sql,
        "max_priority": float(top.to_pandas()["priority"].max()),
    }
    _say(verbose, f"[4] top-20 interventions: {results['top20']}")

    # Scene 5 — mock DeepSee settlement-risk bootstrap
    risky = conn.execute(
        "SELECT transaction_id, predicted_fail_probability FROM transactions "
        "ORDER BY predicted_fail_probability DESC, transaction_id "
        "LIMIT 500"
    ).fetchall()
    service = MockDeepSeeService(
        members={int(t): float(p) for t, p in risky}, page_size=200
    )
    client = DeepSeeClient(service)
    engine.register_snapshot_context(
        "deepsee_settlement_risk",
        entity_key="transaction_id",
        has_score=True,
        entity_key_type="INT64",
    )
    synchronizer = DeepSeeSynchronizer(
        client, store, "deepsee_settlement_risk"
    )
    snapshot = synchronizer.bootstrap()
    results["deepsee_bootstrap"] = {
        "members": snapshot.member_count, "version": snapshot.version,
    }
    _say(verbose, f"[5] DeepSee bootstrap: {results['deepsee_bootstrap']}")

    # Scene 6 — incremental DeepSee delta
    new_ids = conn.execute(
        "SELECT transaction_id FROM transactions "
        "WHERE settlement_status = 'pending' "
        "ORDER BY transaction_id LIMIT 3"
    ).fetchall()
    for (txn_id,) in new_ids:
        service.add_member(int(txn_id), 0.91)
    dropped = int(risky[-1][0])
    service.remove_member(dropped)
    report = synchronizer.sync_once()
    after = store.get_snapshot("deepsee_settlement_risk")
    results["deepsee_delta"] = {
        "applied": report.applied_total,
        "version": after.version,
        "members": after.member_count,
    }
    _say(verbose, f"[6] DeepSee delta: {results['deepsee_delta']}")

    # Scene 7 — compose native and connector-supplied membership
    combined = engine.execute(COMBINED_QUERY)
    results["combined_query"] = {
        "rows": len(combined.to_pandas()),
        "pushdown": "__cql_members_0" in combined.sql,
    }
    _say(verbose, f"[7] combined query: {results['combined_query']}")

    # Scene 8 — REMOTE evidence joined after narrowing
    engine.register_remote_provider("deepsee", DeepSeeRemoteProvider(client))
    evidence = engine.execute(EVIDENCE_QUERY)
    evidence_df = evidence.to_pandas()
    requested_filter = service.last_case_request["entity_filter"]
    requested_members = requested_filter.ids()
    results["evidence_query"] = {
        "rows": len(evidence_df),
        "has_actions": bool(
            evidence_df["recommended_action"].notna().any()
        ),
        "requested_members": requested_filter.cardinality,
        "returned_ids_within_request": set(
            int(value) for value in evidence_df["transaction_id"]
        ).issubset(requested_members),
    }
    _say(verbose, f"[8] evidence query: {results['evidence_query']}")

    # Scene 9 — prior snapshot remains queryable
    v1_result = engine.execute(
        "SELECT transaction_id FROM transactions "
        "WHERE CONTEXT IN (deepsee_settlement_risk AT VERSION 1);"
    )
    v1 = set(int(value) for value in v1_result.to_pandas()["transaction_id"])
    current = store.members("deepsee_settlement_risk")
    results["prior_snapshot"] = {
        "v1_members": len(v1),
        "current_members": len(current),
        "dropped_still_in_v1": dropped in v1 and dropped not in current,
    }
    _say(verbose, f"[9] prior snapshot: {results['prior_snapshot']}")

    # Scene 10 — provenance
    results["provenance"] = {
        "contexts_resolved": list(combined.trace.contexts_resolved),
        "snapshot_versions": {
            name: store.get_snapshot(name).version
            for name in results["contexts"]
        },
        "deepsee_data_as_of": str(after.data_as_of),
        "watermark": synchronizer.committed_watermark,
    }
    _say(verbose, f"[10] provenance: {results['provenance']}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10_000_000)
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args()
    run_demo(args.rows, args.db, verbose=True)
    print("\nAll ten scenarios completed. Synthetic data; mock connector.")


if __name__ == "__main__":
    main()
