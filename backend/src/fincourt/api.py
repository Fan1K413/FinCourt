import hmac
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Header, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import schema_v1 as db
from .changes import ChangeInput, confirm_event, event_list, get_impact
from .claims import ClaimInput, create_claim, get_claim, list_claims
from .database import ROOT, Settings, settings
from .demo import load_demo_claims, load_disclosures
from .documents import (
    MAX_FILE,
    DomainError,
    applicable_facts,
    get_document,
    get_page,
    import_document,
    list_documents,
    sha256,
    visible_evidence,
)
from .knowledge import (
    PatchInput,
    approve_patch,
    get_patch,
    knowledge_view,
    propose_patch,
    publish_patch,
    reject_patch,
    rollback,
    validate_patch,
)
from .mcp_tools import tool_history
from .models import Cutoff, DocumentInput, Identifier, SourceKind
from .nexent import run_live
from .reviews import ReviewInput, get_review, review_history, run_review


class EvidenceQuery(Cutoff):
    subject: Identifier
    source_kind: SourceKind


def create_app(configuration: Settings | None = None) -> FastAPI:
    config = configuration or settings()
    engine = config.engine()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        engine.dispose()

    app = FastAPI(title="FinCourt", lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = config

    @app.exception_handler(DomainError)
    async def handle_domain_error(_request, error):
        return JSONResponse(
            status_code=error.status,
            content={
                "detail": {"code": error.code, "message": str(error)},
            },
        )

    @app.exception_handler(IntegrityError)
    async def handle_conflict(_request, _error):
        return JSONResponse(
            status_code=409,
            content={
                "detail": {"code": "VERSION_CONFLICT", "message": "ID 已存在或资料版本冲突"},
            },
        )

    @app.get("/health")
    def health():
        with engine.connect() as connection:
            available = connection.scalar(select(db.release.c.id).limit(1))
        return {
            "status": "ok" if available else "unseeded",
            "mode": config.mode,
            "database": engine.dialect.name,
        }

    @app.post("/api/documents", status_code=201)
    def upload(metadata: str = Form(), file: UploadFile = File()):
        try:
            item = DocumentInput.model_validate_json(metadata)
        except ValidationError as error:
            raise DomainError("INVALID_METADATA", error.errors()[0]["msg"]) from error
        data = file.file.read(MAX_FILE + 1)
        with engine.begin() as connection:
            result = import_document(connection, config.storage_dir, item, data, "application/pdf")
            confirm_event(connection, item.id, config.storage_dir, mode=config.mode)
            return result

    @app.get("/api/documents")
    def documents():
        with engine.connect() as connection:
            return list_documents(connection)

    @app.get("/api/evidence")
    def evidence(query: EvidenceQuery = Query()):
        with engine.connect() as connection:
            docs, facts, edges = visible_evidence(
                connection, query, query.subject, query.source_kind
            )
        applicable = applicable_facts(facts, edges)
        return {
            "documents": docs,
            "facts": [{**item, "applicable": item["id"] in applicable} for item in facts],
            "corrections": edges,
        }

    @app.get("/api/documents/{document_id}")
    def document(document_id: Identifier):
        with engine.connect() as connection:
            return get_document(connection, document_id)

    @app.get("/api/documents/{document_id}/pages/{page}")
    def document_page(document_id: Identifier, page: int, version: str | None = None):
        with engine.connect() as connection:
            return get_page(connection, document_id, page, version)

    @app.get("/api/documents/{document_id}/file")
    def document_file(document_id: Identifier):
        with engine.connect() as connection:
            item = get_document(connection, document_id)
        target = config.storage_dir / item["sha256"]
        if not target.is_file() or sha256(target.read_bytes()) != item["sha256"]:
            raise DomainError("STORAGE_INTEGRITY", "原文件缺失或哈希校验失败", 500)
        return FileResponse(
            target,
            media_type=item["media_type"],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": "inline",
            },
        )

    @app.get("/api/claims")
    def claims():
        with engine.connect() as connection:
            return list_claims(connection)

    @app.post("/api/claims", status_code=201)
    def new_claim(candidate: ClaimInput):
        with engine.begin() as connection:
            return create_claim(connection, candidate)

    @app.get("/api/claims/{claim_id}")
    def claim(claim_id: Identifier):
        with engine.connect() as connection:
            return {
                **get_claim(connection, claim_id),
                "reviews": review_history(connection, claim_id),
            }

    def fixture_review(request: ReviewInput):
        if config.mode != "fixture":
            raise DomainError("MODE_MISMATCH", "当前实例未启用 fixture 执行", 409)
        with engine.begin() as connection:
            return run_review(connection, request, config.storage_dir)

    @app.post("/api/reviews", status_code=201)
    async def review(request: ReviewInput):
        if request.execution_mode == "live":
            return await run_live(engine, config, request)
        return await run_in_threadpool(fixture_review, request)

    @app.get("/api/reviews/{run_id}")
    def saved_review(run_id: Identifier):
        with engine.connect() as connection:
            result = get_review(connection, run_id)
            return {**result, "tool_calls": tool_history(connection, run_id)}

    @app.post("/api/change-events")
    def new_change(payload: ChangeInput):
        with engine.begin() as connection:
            return confirm_event(
                connection, payload.document_id, config.storage_dir, mode=config.mode
            )

    @app.get("/api/change-events")
    def changes():
        with engine.connect() as connection:
            return event_list(connection)

    @app.get("/api/change-events/{event_id}/impact")
    def change_impact(event_id: Identifier):
        with engine.connect() as connection:
            return get_impact(connection, event_id)

    @app.post("/api/demo/correction")
    def demo_correction():
        if config.mode != "fixture":
            raise DomainError("MODE_MISMATCH", "模拟更正需要 fixture 模式", 409)
        with engine.begin() as connection:
            load_disclosures(connection, config.storage_dir, "SIMULATED", "initial")
            load_demo_claims(connection, config.storage_dir, "SIMULATED")
            correction = load_disclosures(
                connection, config.storage_dir, "SIMULATED", "correction"
            )[0]
            load_demo_claims(connection, config.storage_dir, "SIMULATED")
            return confirm_event(connection, correction["id"], config.storage_dir)

    def maintainer(authorization: str | None) -> str:
        token = os.getenv("FINCOURT_ADMIN_TOKEN")
        if not token:
            raise DomainError("ADMIN_CONFIGURATION_MISSING", "未配置维护者凭据", 503)
        if not authorization or not hmac.compare_digest(
            authorization.encode(), f"Bearer {token}".encode()
        ):
            raise DomainError("ADMIN_REQUIRED", "需要维护者凭据", 403)
        return "local-maintainer"

    @app.get("/api/knowledge-releases")
    def knowledge():
        with engine.connect() as connection:
            return knowledge_view(connection)

    @app.post("/api/ontology-patches", status_code=201)
    def new_patch(candidate: PatchInput):
        with engine.begin() as connection:
            return propose_patch(connection, candidate)

    @app.get("/api/ontology-patches/{patch_id}")
    def patch_details(patch_id: Identifier):
        with engine.connect() as connection:
            return get_patch(connection, patch_id)

    @app.post("/api/ontology-patches/{patch_id}/validate")
    def validate(patch_id: Identifier):
        with engine.begin() as connection:
            return validate_patch(connection, patch_id, config.storage_dir)

    @app.post("/api/ontology-patches/{patch_id}/approve")
    def approve(patch_id: Identifier, authorization: str | None = Header(default=None)):
        principal = maintainer(authorization)
        with engine.begin() as connection:
            return approve_patch(connection, patch_id, principal)

    @app.post("/api/ontology-patches/{patch_id}/reject")
    def reject(patch_id: Identifier, authorization: str | None = Header(default=None)):
        principal = maintainer(authorization)
        with engine.begin() as connection:
            return reject_patch(connection, patch_id, principal)

    @app.post("/api/ontology-patches/{patch_id}/publish")
    def publish(patch_id: Identifier, authorization: str | None = Header(default=None)):
        principal = maintainer(authorization)
        with engine.begin() as connection:
            return publish_patch(connection, patch_id, principal)

    @app.post("/api/knowledge-releases/{release_id}/rollback")
    def restore_release(release_id: Identifier, authorization: str | None = Header(default=None)):
        principal = maintainer(authorization)
        with engine.begin() as connection:
            return rollback(connection, release_id, principal)

    static_path = ROOT / "frontend" / "dist"
    if (static_path / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static_path, html=True), name="workbench")
    return app


app = create_app()
