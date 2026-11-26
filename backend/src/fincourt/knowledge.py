import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Connection

from . import schema_v1 as db
from .database import ROOT
from .documents import NUMBER, DomainError, annotate_page, digest, get_document, get_page, sha256
from .models import Amount, Cutoff, Identifier, Input, utc_now
from .schema_v5 import history, knowledge_state, ontology_patch, projection

CONCEPT = "conditional_agreement"
REGRESSION_CASES = ROOT / "examples" / "knowledge-regressions.json"


class Reference(Input):
    document_id: Identifier
    version: Annotated[str, Field(min_length=1, max_length=64)]
    page: Annotated[int, Field(ge=1, le=300)]
    quote: Annotated[str, Field(min_length=3, max_length=120)]


class TypeAddition(Input):
    op: Literal["ADD_TYPE"]
    id: Literal["conditional_agreement"]
    definition: Annotated[str, Field(min_length=10, max_length=500)]
    fields: list[Literal["stage", "amount_role", "amount", "recognition_basis", "conditions"]]

    @model_validator(mode="after")
    def validate_fields(self):
        if sorted(self.fields) != [
            "amount",
            "amount_role",
            "conditions",
            "recognition_basis",
            "stage",
        ]:
            raise ValueError("协议类型需要金额、口径、阶段、收入确认依据和条件")
        return self


class AgreementCase(Input):
    amount: Amount
    amount_reference: Reference
    stage_reference: Reference
    recognition_reference: Reference
    amount_role: Literal["MEMORANDUM_CEILING"]
    stage: Literal["AWARDED_NOT_EFFECTIVE"]
    recognition_basis: Literal["NET_AFTER_FORMAL_CONTRACT"]
    conditions: Annotated[
        list[Literal["SIGN_AND_SEAL", "FORMAL_CONTRACT"]], Field(min_length=2, max_length=2)
    ]

    @model_validator(mode="after")
    def same_document(self):
        references = [self.amount_reference, self.stage_reference, self.recognition_reference]
        if len({(ref.document_id, ref.version) for ref in references}) != 1:
            raise ValueError("案例依据须来自同一份协议披露的同一解析版本")
        if set(self.conditions) != {"SIGN_AND_SEAL", "FORMAL_CONTRACT"}:
            raise ValueError("必须同时记录协议生效及正式合同条件")
        if self.amount.currency != "CNY" or self.amount.normalized()[0] <= 0:
            raise ValueError("协议金额须为人民币正值")
        return self


class PatchInput(Input):
    base_release: Identifier
    gap: Annotated[str, Field(min_length=10, max_length=600)]
    scope: Literal["AGREEMENT"]
    migration: Annotated[str, Field(min_length=10, max_length=600)]
    operations: Annotated[list[TypeAddition], Field(min_length=1, max_length=1)]
    example: AgreementCase


def current_state(connection: Connection) -> dict:
    return dict(
        connection.execute(select(knowledge_state).where(knowledge_state.c.id == 1))
        .mappings()
        .one()
    )


def lock_state(connection: Connection) -> dict:
    connection.execute(
        knowledge_state.update()
        .where(knowledge_state.c.id == 1)
        .values(
            data_revision=knowledge_state.c.data_revision,
        )
    )
    return current_state(connection)


def get_patch(connection: Connection, patch_id: str) -> dict:
    row = (
        connection.execute(select(ontology_patch).where(ontology_patch.c.id == patch_id))
        .mappings()
        .first()
    )
    if not row:
        raise DomainError("PATCH_NOT_FOUND", "本体补丁不存在", 404)
    return dict(row)


def propose_patch(connection: Connection, candidate: PatchInput) -> dict:
    state = current_state(connection)
    if state["active_release"] != candidate.base_release:
        raise DomainError("VERSION_CONFLICT", "补丁的基准版本已变化", 409)
    reference = candidate.example.amount_reference
    source = get_document(connection, reference.document_id)
    if source["disclosure_type"] != "AGREEMENT":
        raise DomainError("PATCH_SCOPE", "该案例需要协议公告作为依据")
    payload = candidate.model_dump(mode="json")
    content_hash = digest(payload)
    identifier = content_hash
    if connection.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    connection.execute(
        insert(ontology_patch)
        .values(
            id=identifier,
            base_release=candidate.base_release,
            content_hash=content_hash,
            status="DRAFT",
            payload=payload,
            created_at=utc_now(),
        )
        .on_conflict_do_nothing()
    )
    return get_patch(connection, identifier)


def _extract_refs(connection: Connection, case: AgreementCase, storage: Path) -> list[dict]:
    source = get_document(connection, case.amount_reference.document_id)
    path = storage / source["sha256"]
    if not path.is_file() or sha256(path.read_bytes()) != source["sha256"]:
        raise DomainError("STORAGE_INTEGRITY", "协议原文件缺失或哈希不一致")
    spans = []
    for item in (case.amount_reference, case.stage_reference, case.recognition_reference):
        page = get_page(connection, item.document_id, item.page, item.version)
        if sha256(page["text"]) != page["sha256"]:
            raise DomainError("INVALID_CITATION", "协议解析页校验失败")
        spans.append(
            annotate_page(item.document_id, item.version, item.page, page["text"], item.quote)
        )
    return spans


def _validate_case(connection: Connection, case: AgreementCase, storage: Path):
    from decimal import Decimal

    spans = _extract_refs(connection, case, storage)
    ceiling, stage, recognition = [item["text"] for item in spans]
    quoted_values = {Decimal(item.replace(",", "")) for item in NUMBER.findall(ceiling)}
    if (
        Decimal(case.amount.value) not in quoted_values
        or "最高金额" not in ceiling
        or case.amount.unit != "yuan"
        or "元" not in ceiling
        or "万元" in ceiling
        or "亿元" in ceiling
    ):
        raise DomainError("CASE_AMOUNT", "原文未匹配备忘录上限金额")
    if "暂未生效" not in stage or "签字盖章" not in stage:
        raise DomainError("CASE_STAGE", "原文未确认尚未生效及签署条件")
    if "正式合同" not in recognition or "净额法" not in recognition:
        raise DomainError("CASE_RECOGNITION", "原文未确认正式合同及净额法口径")
    return {
        **case.model_dump(
            mode="json", exclude={"amount_reference", "stage_reference", "recognition_reference"}
        ),
        "concept": CONCEPT,
        "recognized_revenue": None,
        "citations": spans,
    }


def _load_regressions():
    return json.loads(REGRESSION_CASES.read_text(encoding="utf-8"))


def fingerprints():
    module_dir = Path(__file__).parent
    modules = ("knowledge.py", "documents.py", "models.py", "reviews.py", "expressions.py")
    return {
        "code_hash": digest({name: sha256((module_dir / name).read_bytes()) for name in modules}),
        "test_hash": sha256(REGRESSION_CASES.read_bytes()),
        "execution_config": "knowledge-regression-v1",
    }


def _run_regressions() -> list[dict]:
    results = []
    for case in _load_regressions():
        if case["kind"] == "equivalent_amounts":
            left, right = [Amount.model_validate(item).normalized() for item in case["inputs"]]
            ok = (left == right) == case["expected"]
        else:
            cutoff = Cutoff.model_validate(case["cutoff"])
            ok = cutoff.includes(case["published_date"], case["published_at"]) == case["expected"]
        results.append({"id": case["id"], "passed": ok})
    return results


def validate_patch(connection: Connection, patch_id: str, storage: Path) -> dict:
    patch = get_patch(connection, patch_id)
    state = lock_state(connection)
    if state["active_release"] != patch["base_release"]:
        raise DomainError("VERSION_CONFLICT", "当前知识版本已变化，请基于当前版本提交", 409)
    if patch["status"] in {"PUBLISHED", "REJECTED"}:
        raise DomainError("PATCH_STATE", "该补丁不接受再次验证", 409)
    connection.execute(
        ontology_patch.update()
        .where(ontology_patch.c.id == patch_id)
        .values(
            status="VALIDATING",
            approval=None,
        )
    )
    candidate = PatchInput.model_validate(patch["payload"])
    documents = list(
        connection.scalars(
            select(db.document.c.id)
            .where(db.document.c.disclosure_type == "AGREEMENT")
            .order_by(db.document.c.id)
        )
    )
    document_id = candidate.example.amount_reference.document_id
    regressions = _run_regressions()
    original_release = connection.scalar(
        select(db.release.c.manifest).where(db.release.c.id == patch["base_release"])
    )
    baseline_has_concept = CONCEPT in original_release.get("concepts", {})
    projected = None
    error = None
    try:
        if baseline_has_concept:
            raise DomainError("CONCEPT_EXISTS", "基准结构中已存在该类型")
        projected = _validate_case(connection, candidate.example, storage)
    except DomainError as problem:
        error = {"code": problem.code, "message": str(problem)}
    passed = error is None and bool(regressions) and all(case["passed"] for case in regressions)
    validation = {
        **fingerprints(),
        "content_hash": patch["content_hash"],
        "base_release": patch["base_release"],
        "data_revision": state["data_revision"],
        "validated_at": utc_now(),
        "checks": regressions,
        "passed": passed,
        "new_case_error": error,
        "before": {"concept_present": baseline_has_concept, "projected_record": None},
        "after": projected,
        "unmapped": [identifier for identifier in documents if identifier != document_id],
    }
    connection.execute(
        ontology_patch.update()
        .where(ontology_patch.c.id == patch_id)
        .values(
            status="READY_FOR_REVIEW" if passed else "VALIDATION_FAILED",
            validation=validation,
        )
    )
    return get_patch(connection, patch_id)


def _require_current_validation(state, patch):
    validation = patch["validation"]
    if not validation or not validation["passed"]:
        raise DomainError("VALIDATION_REQUIRED", "需要服务端通过的验证记录", 409)
    expected = {
        **fingerprints(),
        "content_hash": digest(patch["payload"]),
        "base_release": state["active_release"],
        "data_revision": state["data_revision"],
    }
    if any(validation[key] != value for key, value in expected.items()):
        raise DomainError("VALIDATION_STALE", "知识版本、资料或验证规则已变化，请重新验证", 409)
    return validation


def approve_patch(connection: Connection, patch_id: str, principal: str) -> dict:
    state = lock_state(connection)
    patch = get_patch(connection, patch_id)
    if patch["status"] != "READY_FOR_REVIEW":
        raise DomainError("PATCH_STATE", "只有已验证的补丁可审批", 409)
    validation = _require_current_validation(state, patch)
    connection.execute(
        ontology_patch.update()
        .where(ontology_patch.c.id == patch_id)
        .values(
            status="APPROVED",
            approval={"principal": principal, "at": utc_now(), "validation": digest(validation)},
        )
    )
    return get_patch(connection, patch_id)


def reject_patch(connection: Connection, patch_id: str, principal: str) -> dict:
    lock_state(connection)
    patch = get_patch(connection, patch_id)
    if patch["status"] not in {"DRAFT", "READY_FOR_REVIEW", "VALIDATION_FAILED"}:
        raise DomainError("PATCH_STATE", "该状态不能拒绝补丁", 409)
    connection.execute(
        ontology_patch.update()
        .where(ontology_patch.c.id == patch_id)
        .values(
            status="REJECTED",
            approval={"principal": principal, "at": utc_now(), "decision": "REJECTED"},
        )
    )
    return get_patch(connection, patch_id)


def _activate(connection, source, target, action, principal):
    connection.execute(
        knowledge_state.update().where(knowledge_state.c.id == 1).values(active_release=target)
    )
    connection.execute(
        history.insert().values(
            id=uuid4().hex,
            applied_at=utc_now(),
            previous_release=source,
            release_id=target,
            action=action,
            principal=principal,
        )
    )


def publish_patch(connection: Connection, patch_id: str, principal: str) -> dict:
    state = lock_state(connection)
    patch = get_patch(connection, patch_id)
    if patch["status"] != "APPROVED":
        raise DomainError("PATCH_STATE", "需要维护者审批", 409)
    validation = _require_current_validation(state, patch)
    if patch["approval"]["validation"] != digest(validation):
        raise DomainError("VALIDATION_STALE", "审批记录与验证结果不一致", 409)
    identifier = digest([patch["content_hash"], digest(validation)])
    baseline = connection.scalar(
        select(db.release.c.manifest).where(db.release.c.id == patch["base_release"])
    )
    concept = patch["payload"]["operations"][0]
    manifest = {
        **baseline,
        "ontology": f"financial-disclosure-{identifier[:12]}",
        "projection": f"conditional-agreement-{identifier[:12]}",
        "retrieval": f"public-time-agreement-{identifier[:12]}",
        "concepts": {**baseline.get("concepts", {}), CONCEPT: concept},
        "patch_id": patch_id,
        "data_revision": state["data_revision"],
    }
    connection.execute(db.release.insert().values(id=identifier, manifest=manifest))
    document_id = patch["payload"]["example"]["amount_reference"]["document_id"]
    connection.execute(
        projection.insert().values(
            release_id=identifier,
            document_id=document_id,
            value=validation["after"],
        )
    )
    _activate(connection, patch["base_release"], identifier, "PUBLISH", principal)
    connection.execute(
        ontology_patch.update()
        .where(ontology_patch.c.id == patch_id)
        .values(
            status="PUBLISHED",
            published_release=identifier,
        )
    )
    return get_patch(connection, patch_id)


def rollback(connection: Connection, release_id: str, principal: str) -> dict:
    state = lock_state(connection)
    exists = connection.scalar(select(db.release.c.id).where(db.release.c.id == release_id))
    if not exists:
        raise DomainError("RELEASE_NOT_FOUND", "目标知识版本不存在", 404)
    if release_id != state["active_release"]:
        _activate(connection, state["active_release"], release_id, "ROLLBACK", principal)
    return knowledge_view(connection)


def knowledge_view(connection: Connection) -> dict:
    state = current_state(connection)
    releases = [
        dict(row)
        for row in connection.execute(select(db.release).order_by(db.release.c.id)).mappings()
    ]
    patches = [
        dict(row)
        for row in connection.execute(
            select(ontology_patch).order_by(ontology_patch.c.created_at.desc(), ontology_patch.c.id)
        ).mappings()
    ]
    active_projections = [
        dict(row)
        for row in connection.execute(
            select(projection)
            .where(projection.c.release_id == state["active_release"])
            .order_by(projection.c.document_id)
        ).mappings()
    ]
    agreements = set(
        connection.scalars(
            select(db.document.c.id).where(db.document.c.disclosure_type == "AGREEMENT")
        )
    )
    mapped = {item["document_id"] for item in active_projections}
    entries = [
        dict(row)
        for row in connection.execute(
            select(history).order_by(history.c.applied_at.desc())
        ).mappings()
    ]
    return {
        **state,
        "releases": releases,
        "patches": patches,
        "projections": active_projections,
        "pending_documents": sorted(agreements - mapped),
        "history": entries,
    }
