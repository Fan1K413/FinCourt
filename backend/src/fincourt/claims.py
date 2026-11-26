from collections import defaultdict, deque
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Connection

from . import schema_v1 as db
from .documents import DomainError
from .expressions import validate_expression
from .models import Amount, Cutoff, Identifier, Input, Period, Scope, SourceKind, utc_now
from .schema_v2 import depends_on, proof

ClaimType = Literal["DISCLOSURE_FACT", "AS_OF_METRIC", "CALCULATION", "INTERPRETATION", "FORECAST"]


class MetricCheck(Input):
    metric: Identifier
    period: Period
    scope: Scope
    operator: Literal["eq", "gt", "ge", "lt", "le"] = "gt"
    threshold: Amount | None = None
    require_audited: bool = False
    document_id: Identifier | None = None

    @model_validator(mode="after")
    def check_basis(self):
        if self.scope == "UNKNOWN":
            raise ValueError("观点须指定财务口径")
        if not self.require_audited and self.threshold is None:
            raise ValueError("数值核验须指定比较阈值")
        if self.require_audited and self.threshold is None:
            raise ValueError("最终审计金额核验须指定待核验数值")
        return self


class Premise(Input):
    claim_id: Identifier | None = None
    expression: dict | None = None

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.claim_id is None) == (self.expression is None):
            raise ValueError("前提须指定一个观点或一个受限表达式")
        if self.expression is not None:
            validate_expression(self.expression)
        return self


class ProofInput(Input):
    rule_id: Literal["DECLARATIVE_ALL_V1"] = "DECLARATIVE_ALL_V1"
    polarity: Literal["SUPPORT", "REFUTE"] = "SUPPORT"
    premises: Annotated[list[Premise], Field(min_length=1, max_length=12)]
    condition: Annotated[str, Field(max_length=400)] = ""


class ClaimInput(Input):
    id: Identifier | None = None
    text: Annotated[str, Field(min_length=3, max_length=1000)]
    subject: Identifier
    source_kind: SourceKind
    kind: ClaimType
    timing: Literal["FIXED", "ROLLING"] = "ROLLING"
    fixed_cutoff: Cutoff | None = None
    check: MetricCheck | None = None
    proofs: Annotated[list[ProofInput], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_shape(self):
        if (self.timing == "FIXED") != (self.fixed_cutoff is not None):
            raise ValueError("固定观点必须有截止时间，滚动观点由运行指定截止时间")
        if self.kind in {"DISCLOSURE_FACT", "AS_OF_METRIC"} and self.check is None:
            raise ValueError("此观点需要结构化的指标和报告期")
        if self.kind == "DISCLOSURE_FACT" and not self.check.document_id:
            raise ValueError("历史披露观点须选择原公告")
        if self.kind != "DISCLOSURE_FACT" and self.check and self.check.document_id:
            raise ValueError("按时点审查应由资料快照选择披露")
        return self


def topological_order(graph: dict[str, set[str]]) -> list[str]:
    levels = {node: len(deps) for node, deps in graph.items()}
    children = defaultdict(list)
    for node, deps in graph.items():
        for parent in sorted(deps):
            if parent not in graph:
                raise DomainError("DEPENDENCY_MISSING", f"引用的观点 {parent} 不存在")
            children[parent].append(node)
    ready = deque(sorted(node for node, count in levels.items() if not count))
    order = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for child in sorted(children[node]):
            levels[child] -= 1
            if not levels[child]:
                ready.append(child)
    if len(order) != len(graph):
        raise DomainError("DEPENDENCY_CYCLE", "推导依赖含有环")
    return order


def claim_graph(connection: Connection) -> dict[str, set[str]]:
    graph = {identifier: set() for identifier in connection.scalars(select(db.claim.c.id))}
    for row in connection.execute(select(depends_on)).mappings():
        graph[row["claim_id"]].add(row["dependency_id"])
    return graph


def create_claim(connection: Connection, candidate: ClaimInput) -> dict:
    identifier = candidate.id or f"claim-{uuid4().hex}"
    definition = candidate.model_dump(mode="json", exclude={"id"})
    current = (
        connection.execute(select(db.claim).where(db.claim.c.id == identifier)).mappings().first()
    )
    if current:
        if current["definition"] != definition:
            raise DomainError("CLAIM_CONFLICT", "观点 ID 已存在，请为新观点使用新 ID", 409)
        return dict(current)
    graph = claim_graph(connection)
    dependencies = {
        premise.claim_id
        for item in candidate.proofs
        for premise in item.premises
        if premise.claim_id
    }
    graph[identifier] = dependencies
    topological_order(graph)
    if dependencies:
        dependency_rows = connection.execute(
            select(db.claim.c.subject, db.claim.c.source_kind).where(
                db.claim.c.id.in_(dependencies)
            )
        ).mappings()
        if any(
            row["subject"] != candidate.subject or row["source_kind"] != candidate.source_kind
            for row in dependency_rows
        ):
            raise DomainError("DEPENDENCY_SCOPE", "当前版本的观点依赖必须属于同一主体和来源类别")
    record = dict(
        id=identifier,
        subject=candidate.subject,
        source_kind=candidate.source_kind,
        timing=candidate.timing,
        version=1,
        definition=definition,
        created_at=utc_now(),
    )
    connection.execute(db.claim.insert().values(**record))
    if dependencies:
        connection.execute(
            depends_on.insert(),
            [
                {"claim_id": identifier, "dependency_id": dependency_id}
                for dependency_id in sorted(dependencies)
            ],
        )
    if candidate.proofs:
        connection.execute(
            proof.insert(),
            [
                {"claim_id": identifier, "position": position, "rule_id": item.rule_id}
                for position, item in enumerate(candidate.proofs)
            ],
        )
    return record


def list_claims(connection: Connection) -> list[dict]:
    return [
        dict(row) for row in connection.execute(select(db.claim).order_by(db.claim.c.id)).mappings()
    ]


def get_claim(connection: Connection, identifier: str) -> dict:
    row = connection.execute(select(db.claim).where(db.claim.c.id == identifier)).mappings().first()
    if not row:
        raise DomainError("CLAIM_NOT_FOUND", "观点不存在", 404)
    return dict(row)
