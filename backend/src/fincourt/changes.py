from sqlalchemy import select
from sqlalchemy.engine import Connection

from . import schema_v1 as db
from .claims import claim_graph, list_claims, topological_order
from .documents import DomainError, digest, fact_rows, get_document
from .models import Cutoff, Identifier, Input, utc_now
from .reviews import ReviewInput, cutoff_boundary, get_review, review_history, run_review
from .schema_v4 import change_event, impact


class ChangeInput(Input):
    document_id: Identifier


def _fact_summary(item):
    return {
        key: item[key]
        for key in (
            "id",
            "document_id",
            "metric",
            "period_start",
            "period_end",
            "scope",
            "currency",
            "unit",
            "value",
            "audit_status",
        )
    }


def disclosure_changes(connection, document_id: str) -> list[dict]:
    new = fact_rows(connection, [document_id])
    edges = list(
        connection.execute(
            select(db.correction)
            .where(db.correction.c.replacement_id.in_([item["id"] for item in new]))
            .order_by(db.correction.c.original_id)
        ).mappings()
    )
    originals = {}
    if edges:
        originals = {
            row["id"]: dict(row)
            for row in connection.execute(
                select(db.fact).where(db.fact.c.id.in_([edge["original_id"] for edge in edges]))
            ).mappings()
        }
    changes = []
    for item in new:
        previous = [
            originals[edge["original_id"]] for edge in edges if edge["replacement_id"] == item["id"]
        ]
        after = _fact_summary(item)
        changes.extend({"before": _fact_summary(old), "after": after} for old in previous)
        if not previous:
            changes.append({"before": None, "after": after})
    return changes


def _explicit_fact_ids(definition: dict) -> set[str]:
    ids = set()
    stack = [
        condition["expression"]
        for proof in definition["proofs"]
        for condition in proof["premises"]
        if condition.get("expression")
    ]
    while stack:
        node = stack.pop()
        if "fact_id" in node:
            ids.add(node["fact_id"])
        stack.extend(node.get("args", []))
    return ids


def _metric_matches(check, fact):
    return (
        check["metric"] == fact["metric"]
        and check["scope"] == fact["scope"]
        and check["period"]["start"] == fact["period_start"]
        and check["period"]["end"] == fact["period_end"]
        and check["threshold"]["currency"] == fact["currency"]
    )


def impacted_claims(connection, document, changes):
    graph = claim_graph(connection)
    candidates = {}
    fixed = []
    old_ids = {change["before"]["id"] for change in changes if change["before"]}
    new_facts = [change["after"] for change in changes]
    claims = {claim["id"]: claim for claim in list_claims(connection)}
    for identifier in topological_order(graph):
        claim = claims[identifier]
        if (
            claim["subject"] != document["subject"]
            or claim["source_kind"] != document["source_kind"]
        ):
            continue
        definition = claim["definition"]
        if definition["kind"] == "DISCLOSURE_FACT":
            continue
        check = definition["check"]
        reasons = []
        if old_ids.intersection(_explicit_fact_ids(definition)):
            reasons.append("CORRECTED_FACT_DEPENDENCY")
        if check and any(_metric_matches(check, fact) for fact in new_facts):
            reasons.append("MATCHING_SUBJECT_METRIC_PERIOD")
        if graph[identifier].intersection(candidates):
            reasons.append("TRANSITIVE_CLAIM_DEPENDENCY")
        history = review_history(connection, identifier)
        if old_ids and any(
            evidence["fact_id"] in old_ids
            for run in history
            if run["result"]
            for evidence in run["result"]["evidence"]
        ):
            reasons.append("CORRECTED_REVIEW_EVIDENCE")
        if not reasons:
            continue
        if claim["timing"] == "FIXED":
            fixed.append(identifier)
            continue
        candidates[identifier] = {"reasons": reasons, "history": history}
    return candidates, fixed


def confirm_event(
    connection: Connection, document_id: str, storage, *, mode: str = "fixture"
) -> dict:
    document = get_document(connection, document_id)
    event_id = digest(["DISCLOSURE_CHANGE_V1", document_id])
    existing = connection.scalar(select(change_event.c.id).where(change_event.c.id == event_id))
    if existing:
        return get_impact(connection, event_id)
    changes = (
        disclosure_changes(connection, document_id)
        if document["disclosure_type"] != "COMMENTARY"
        else []
    )
    if connection.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    inserted = connection.execute(
        insert(change_event)
        .values(
            id=event_id,
            document_id=document_id,
            created_at=utc_now(),
            changes=changes,
            fixed_claims_preserved=[],
        )
        .on_conflict_do_nothing()
        .returning(change_event.c.id)
    ).scalar_one_or_none()
    if not inserted:
        return get_impact(connection, event_id)
    candidates, fixed = impacted_claims(connection, document, changes)
    connection.execute(
        change_event.update()
        .where(change_event.c.id == event_id)
        .values(
            fixed_claims_preserved=fixed,
        )
    )
    cutoff = (
        Cutoff(mode="PUBLIC_INSTANT", at=document["published_at"])
        if document["published_at"]
        else Cutoff(date=document["published_date"])
    )
    for position, (claim_id, entry) in enumerate(candidates.items()):
        key = {"event_id": event_id, "claim_id": claim_id}
        connection.execute(
            impact.insert().values(
                **key,
                position=position,
                status="PENDING_REVIEW",
                reasons=entry["reasons"],
            )
        )
        if mode != "fixture":
            continue
        prior_cutoffs = [
            Cutoff.model_validate(run["manifest"]["cutoff"])
            for run in entry["history"]
            if run["status"] == "SUCCEEDED" and run["purpose"] != "PUBLIC_REPLAY"
        ]
        next_cutoff = max([cutoff, *prior_cutoffs], key=cutoff_boundary)
        next_run = run_review(
            connection,
            ReviewInput(claim_id=claim_id, cutoff=next_cutoff, purpose="CHANGE_REVIEW"),
            storage,
            run_id=f"change-{digest([event_id, claim_id])}",
        )
        connection.execute(
            impact.update()
            .where(
                impact.c.event_id == event_id,
                impact.c.claim_id == claim_id,
            )
            .values(run_id=next_run["id"], status=next_run["status"])
        )
    return get_impact(connection, event_id)


def get_impact(connection: Connection, event_id: str) -> dict:
    event = (
        connection.execute(select(change_event).where(change_event.c.id == event_id))
        .mappings()
        .first()
    )
    if not event:
        raise DomainError("CHANGE_NOT_FOUND", "更正或新增事件不存在", 404)
    affected = [
        {**row, "review": get_review(connection, row["run_id"]) if row["run_id"] else None}
        for row in connection.execute(
            select(impact).where(impact.c.event_id == event_id).order_by(impact.c.position)
        ).mappings()
    ]
    document = get_document(connection, event["document_id"])
    requires_confirmation = document["disclosure_type"] == "CORRECTION" and (
        not event["changes"] or any(change["before"] is None for change in event["changes"])
    )
    return {
        **event,
        "document": document,
        "claims": affected,
        "requires_confirmation": requires_confirmation,
    }


def event_list(connection: Connection) -> list[dict]:
    rows = connection.execute(
        select(
            change_event.c.id,
            change_event.c.document_id,
            change_event.c.created_at,
            db.document.c.title,
        )
        .join(db.document, db.document.c.id == change_event.c.document_id)
        .order_by(change_event.c.created_at.desc(), change_event.c.id)
    ).mappings()
    return list(map(dict, rows))
