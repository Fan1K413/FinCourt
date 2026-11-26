import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import Connection

from . import schema_v1 as db
from .claims import ClaimInput, get_claim
from .documents import (
    DomainError,
    applicable_facts,
    digest,
    evidence_for,
    fact_rows,
    sha256,
    visible_evidence,
)
from .expressions import evaluate, fact_quantity
from .models import SHANGHAI, Cutoff, Decision, Identifier, Input, utc_now
from .schema_v5 import knowledge_state, projection

LOGGER = logging.getLogger(__name__)
BASE_RELEASE = "fincourt-disclosures-v1"
FIXTURE_VERSION = "fixture-candidates-v1"


class ReviewInput(Input):
    claim_id: Identifier
    cutoff: Cutoff | None = None
    execution_mode: Literal["fixture", "live"] = "fixture"
    knowledge_release_id: Identifier | None = None
    purpose: Literal["REVIEW", "PUBLIC_REPLAY", "CHANGE_REVIEW"] = "REVIEW"


def cutoff_boundary(cutoff: Cutoff) -> datetime:
    if cutoff.mode == "PUBLIC_INSTANT":
        return cutoff.at.astimezone(timezone.utc)
    return datetime.combine(cutoff.date + timedelta(days=1), time.min, SHANGHAI).astimezone(
        timezone.utc
    )


def resolve_cutoff(candidate: ClaimInput, requested: Cutoff | None) -> Cutoff:
    if candidate.timing == "FIXED":
        if requested is not None and requested != candidate.fixed_cutoff:
            raise DomainError("FIXED_CUTOFF", "固定观点必须沿用原截止时点")
        return candidate.fixed_cutoff
    return requested or Cutoff(date=datetime.now(SHANGHAI).date())


def prepare_snapshot(
    connection: Connection, candidate: ClaimInput, cutoff: Cutoff, release_id: str | None
) -> dict:
    if release_id is None:
        release_id = connection.scalar(
            select(knowledge_state.c.active_release).where(knowledge_state.c.id == 1)
        )
    release = (
        connection.execute(select(db.release).where(db.release.c.id == release_id))
        .mappings()
        .first()
    )
    if not release:
        raise DomainError("RELEASE_NOT_FOUND", "知识发布版本不存在", 404)
    documents, facts, corrections = visible_evidence(
        connection, cutoff, candidate.subject, candidate.source_kind
    )
    boundary = cutoff_boundary(cutoff)
    manifest = {
        "cutoff": cutoff.model_dump(mode="json", exclude_none=True),
        "subject": candidate.subject,
        "source_kind": candidate.source_kind,
        "knowledge_release": {"id": release_id, **release["manifest"]},
        "documents": [
            {
                "id": item["id"],
                "sha256": item["sha256"],
                "parses": item["parses"],
                "published_date": item["published_date"],
                "time_precision": item["time_precision"],
                "post_collected": datetime.fromisoformat(item["ingested_at"]) >= boundary
                if cutoff.mode == "PUBLIC_END_OF_DAY"
                else datetime.fromisoformat(item["ingested_at"]) > boundary,
            }
            for item in documents
        ],
        "facts": [{"id": item["id"], "evidence_id": item["evidence_id"]} for item in facts],
        "corrections": corrections,
        "agreement_projections": [
            dict(row)
            for row in connection.execute(
                select(projection.c.document_id, projection.c.value)
                .where(
                    projection.c.release_id == release_id,
                    projection.c.document_id.in_([item["id"] for item in documents]),
                )
                .order_by(projection.c.document_id)
            ).mappings()
        ],
    }
    identifier = digest(manifest)
    row = (
        connection.execute(select(db.snapshot).where(db.snapshot.c.id == identifier))
        .mappings()
        .first()
    )
    if row:
        return dict(row)
    record = dict(
        id=identifier, knowledge_release_id=release_id, manifest=manifest, created_at=utc_now()
    )
    # A repeated run or a concurrent request may produce the same content-addressed manifest.
    if connection.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    connection.execute(insert(db.snapshot).values(**record).on_conflict_do_nothing())
    return dict(
        connection.execute(select(db.snapshot).where(db.snapshot.c.id == identifier))
        .mappings()
        .one()
    )


def bound_facts(connection: Connection, snapshot: dict) -> list[dict]:
    manifest = snapshot["manifest"]
    allowed = {item["id"]: item["evidence_id"] for item in manifest["facts"]}
    facts = [
        item
        for item in fact_rows(connection, [item["id"] for item in manifest["documents"]])
        if item["id"] in allowed
    ]
    if {item["id"]: item["evidence_id"] for item in facts} != allowed:
        raise DomainError("SNAPSHOT_INTEGRITY", "事实或证据已偏离资料快照", 500)
    return facts


@dataclass
class ReviewAssessment:
    support: bool = False
    refute: bool = False
    checks: list[dict] = field(default_factory=list)
    citations: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def record(self, polarity: str, passed: bool, facts: set[str]) -> None:
        if passed:
            self.support |= polarity == "SUPPORT"
            self.refute |= polarity == "REFUTE"
        for fact_id in sorted(facts):
            side = polarity if passed else "CONTEXT"
            prior = self.citations.get(fact_id, "CONTEXT")
            if side == "CONTEXT":
                self.citations.setdefault(fact_id, side)
            else:
                self.citations[fact_id] = side if prior in {side, "CONTEXT"} else "BOTH"

    @property
    def decision(self) -> Decision:
        if self.support and self.refute:
            return "DISPUTED"
        if self.support:
            return "SUPPORTED"
        if self.refute:
            return "REFUTED"
        return "INSUFFICIENT"


def assess_claim(
    connection: Connection,
    identifier: str,
    snapshot: dict,
    facts: list[dict],
) -> ReviewAssessment:
    base_cutoff = Cutoff.model_validate(snapshot["manifest"]["cutoff"])
    edges = snapshot["manifest"]["corrections"]
    memo = {}

    def assess(claim_id: str, parent_cutoff: Cutoff, depth: int) -> ReviewAssessment:
        if depth > 48:
            raise DomainError("DEPENDENCY_LIMIT", "观点推导深度超出当前运行限制")
        candidate = ClaimInput.model_validate(get_claim(connection, claim_id)["definition"])
        cutoff = candidate.fixed_cutoff or parent_cutoff
        key = (claim_id, digest(cutoff.model_dump(mode="json")))
        if cutoff_boundary(cutoff) > cutoff_boundary(parent_cutoff):
            return ReviewAssessment(missing=["依赖观点的截止范围超出本次资料快照"])
        if key in memo:
            return memo[key]
        visible = {
            item["id"]: item
            for item in facts
            if cutoff.includes(item["published_date"], item["published_at"])
        }
        visible_edges = [
            edge
            for edge in edges
            if edge["replacement_id"] in visible and edge["original_id"] in visible
        ]
        original = candidate.check.document_id if candidate.kind == "DISCLOSURE_FACT" else None
        applicable = applicable_facts(visible.values(), visible_edges, original)
        outcome = ReviewAssessment()
        if candidate.kind in {"INTERPRETATION", "FORECAST"}:
            outcome.missing.append("需要可复核的因果前提、替代解释或观察期限")
        elif candidate.proofs:
            for proof in candidate.proofs:
                passed = True
                used = set()
                for premise in proof.premises:
                    if premise.claim_id:
                        child = assess(premise.claim_id, cutoff, depth + 1)
                        value = child.decision == "SUPPORTED"
                        used.update(child.citations)
                        outcome.checks.append(
                            {
                                "rule": proof.rule_id,
                                "claim_id": premise.claim_id,
                                "premise_decision": child.decision,
                                "result": value,
                                "steps": [],
                                "condition": proof.condition,
                            }
                        )
                    else:
                        try:
                            value, steps, referenced = evaluate(premise.expression, applicable)
                        except DomainError as error:
                            if error.code != "FACT_UNAVAILABLE":
                                raise
                            value, steps, referenced = False, [], set()
                            outcome.missing.append(str(error))
                        if not isinstance(value, bool):
                            raise DomainError("EXPRESSION_TYPE", "证明前提必须得出布尔值")
                        if not referenced:
                            value = False
                        used.update(referenced)
                        outcome.checks.append(
                            {
                                "rule": proof.rule_id,
                                "expression": premise.expression,
                                "result": value,
                                "steps": steps,
                                "condition": proof.condition,
                            }
                        )
                    passed &= value
                outcome.record(proof.polarity, passed and bool(used), used)
            if not outcome.support and not outcome.refute:
                outcome.missing.append("没有一组证明满足全部必要前提")
        elif candidate.check:
            _compare_metric(candidate.check, applicable, outcome)
        else:
            outcome.missing.append("需要至少一组具有引用的计算前提")
        memo[key] = outcome
        return outcome

    return assess(identifier, base_cutoff, 1)


def _compare_metric(check, facts: dict[str, dict], outcome: ReviewAssessment) -> None:
    candidates = [
        item
        for item in facts.values()
        if (
            item["metric"] == check.metric
            and item["scope"] == check.scope
            and item["period_start"] == check.period.start.isoformat()
            and item["period_end"] == check.period.end.isoformat()
            and (not check.document_id or item["document_id"] == check.document_id)
        )
    ]
    observed = set()
    for item in candidates:
        fact_id = item["id"]
        if check.require_audited and item["audit_status"] != "AUDITED":
            outcome.record("CONTEXT", False, {fact_id})
            continue
        quantity = fact_quantity(item)
        _, dimension, currency = check.threshold.normalized()
        if (quantity.dimension, quantity.currency) != (dimension, currency):
            outcome.missing.append("适用事实缺少可直接比较的金额单位或币种")
            continue
        source_observation = (item["source_cluster"], quantity.value, item["audit_status"])
        if source_observation in observed:
            continue
        observed.add(source_observation)
        expression = {
            "op": check.operator,
            "args": [
                {"fact_id": fact_id},
                {"literal": check.threshold.model_dump(mode="json")},
            ],
        }
        passed, steps, used = evaluate(expression, facts)
        outcome.checks.append(
            {
                "rule": "DECIMAL_COMPARISON_V1",
                "expression": expression,
                "result": passed,
                "steps": steps,
                "original": {
                    key: item[key] for key in ("value", "unit", "currency", "audit_status")
                },
            }
        )
        outcome.record("SUPPORT" if passed else "REFUTE", True, used)
    if not outcome.support and not outcome.refute:
        if check.require_audited:
            outcome.missing.append("缺少相同报告期和合并范围的最终审计披露")
        elif not candidates:
            outcome.missing.append("缺少报告期、指标和口径一致的适用披露")


def review_history(connection: Connection, identifier: str) -> list[dict]:
    rows = connection.execute(
        select(db.review, db.snapshot.c.manifest)
        .join(db.snapshot, db.snapshot.c.id == db.review.c.snapshot_id)
        .where(db.review.c.claim_id == identifier)
        .order_by(db.review.c.created_at.desc(), db.review.c.id.desc())
    ).mappings()
    seen = set()
    result = []
    for row in rows:
        manifest = row["manifest"]
        group = digest(
            [manifest["cutoff"], manifest["knowledge_release"]["id"], row["mode"], row["purpose"]]
        )
        version = None
        if row["status"] == "SUCCEEDED":
            version = "SUPERSEDED" if group in seen else "CURRENT"
            seen.add(group)
        result.append({**row, "version_status": version})
    return result


def get_review(connection: Connection, identifier: str) -> dict:
    claim_id = connection.scalar(select(db.review.c.claim_id).where(db.review.c.id == identifier))
    if not claim_id:
        raise DomainError("RUN_NOT_FOUND", "审查运行不存在", 404)
    return next(item for item in review_history(connection, claim_id) if item["id"] == identifier)


def cited_result(connection: Connection, claim: dict, snapshot: dict, storage: Path) -> dict:
    facts = bound_facts(connection, snapshot)
    outcome = assess_claim(connection, claim["id"], snapshot, facts)
    by_id = {item["id"]: item for item in facts}
    docs = {item["id"]: item for item in snapshot["manifest"]["documents"]}
    citation_checks = {}
    cited = [
        {
            **validate_citation(connection, by_id[fact_id], docs, storage, citation_checks),
            "role": role,
        }
        for fact_id, role in outcome.citations.items()
    ]
    return {
        "decision": outcome.decision,
        "claim_text": claim["definition"]["text"],
        "claim_version": claim["version"],
        "checks": outcome.checks,
        "evidence": cited,
        "missing": sorted(set(outcome.missing)),
        "source_clusters": sorted(
            {item["source_cluster"] for item in cited if item["source_cluster"]}
        ),
    }


def validate_citation(connection, fact, documents, storage, checks):
    span = evidence_for(connection, fact)
    document = documents[fact["document_id"]]
    file_hash = document["sha256"]
    if file_hash not in checks:
        original = storage / file_hash
        if not original.is_file() or sha256(original.read_bytes()) != file_hash:
            raise DomainError("STORAGE_INTEGRITY", "引用的原文件缺失或哈希不一致", 500)
        checks[file_hash] = True
    if span["parse_version"] not in {item["version"] for item in document["parses"]}:
        raise DomainError("SNAPSHOT_INTEGRITY", "引用解析版本超出资料快照", 500)
    return {**span, "fact_id": fact["id"], "source_cluster": fact["source_cluster"]}


def run_review(
    connection: Connection, request: ReviewInput, storage: Path, *, run_id: str | None = None
) -> dict:
    if request.execution_mode != "fixture":
        raise DomainError(
            "NEXENT_CONFIGURATION_MISSING", "此实例尚未配置经过验证的 Nexent 集成", 503
        )
    claim = get_claim(connection, request.claim_id)
    candidate = ClaimInput.model_validate(claim["definition"])
    cutoff = resolve_cutoff(candidate, request.cutoff)
    snapshot = prepare_snapshot(connection, candidate, cutoff, request.knowledge_release_id)
    previous = next(
        (
            run
            for run in review_history(connection, claim["id"])
            if run["status"] == "SUCCEEDED"
            and run["mode"] == request.execution_mode
            and run["manifest"]["knowledge_release"]["id"] == snapshot["knowledge_release_id"]
            and run["purpose"] != "PUBLIC_REPLAY"
            and cutoff_boundary(Cutoff.model_validate(run["manifest"]["cutoff"]))
            <= cutoff_boundary(cutoff)
        ),
        None,
    )
    identifier = run_id or f"run-{uuid4().hex}"
    record = dict(
        id=identifier,
        claim_id=claim["id"],
        snapshot_id=snapshot["id"],
        mode=request.execution_mode,
        status="RUNNING",
        purpose=request.purpose,
        created_at=utc_now(),
    )
    connection.execute(db.review.insert().values(**record))
    try:
        result = {
            **cited_result(connection, claim, snapshot, storage),
            "execution_config": FIXTURE_VERSION,
            "change": None,
        }
        if previous:
            old = previous["result"]
            before = {item["fact_id"] for item in old["evidence"]}
            after = {item["fact_id"] for item in result["evidence"]}
            result["change"] = {
                "previous_run_id": previous["id"],
                "previous_decision": old["decision"],
                "added_facts": sorted(after - before),
                "removed_facts": sorted(before - after),
            }
        values = {"status": "SUCCEEDED", "result": result}
    except DomainError as error:
        values = {"status": "FAILED", "error": {"code": error.code, "message": str(error)}}
    except Exception:
        LOGGER.exception("Fixture execution failed for %s", identifier)
        values = {
            "status": "FAILED",
            "error": {"code": "EXECUTION_FAILED", "message": "审查运行失败"},
        }
    connection.execute(db.review.update().where(db.review.c.id == identifier).values(**values))
    return get_review(connection, identifier)
