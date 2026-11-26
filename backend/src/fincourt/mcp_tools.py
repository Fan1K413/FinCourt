import hmac
import time
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import TypeAdapter
from sqlalchemy import Engine, select

from . import schema_v1 as db
from .changes import disclosure_changes, get_impact
from .claims import get_claim
from .database import Settings
from .documents import DomainError, applicable_facts
from .knowledge import PatchInput, get_patch, validate_patch
from .knowledge import propose_patch as save_patch
from .models import Identifier, utc_now
from .reviews import bound_facts, cited_result, validate_citation
from .schema_v3 import tool_call

_CONTEXT_LIFETIME = 120


def context_token(secret: bytes, run_id: str) -> str:
    payload = f"{run_id}.{int(time.time()) + _CONTEXT_LIFETIME}"
    return f"{payload}.{hmac.digest(secret, payload.encode(), 'sha256').hex()}"


def read_context(secret: bytes, token: str) -> str:
    try:
        payload, supplied = token.rsplit(".", 1)
        identifier, expiry_text = payload.rsplit(".", 1)
        expected = hmac.digest(secret, payload.encode(), "sha256").hex()
        limit = int(expiry_text)
        if (
            len(token) > 240
            or not hmac.compare_digest(expected, supplied)
            or not 0 <= limit - time.time() <= _CONTEXT_LIFETIME
        ):
            raise ValueError
        return TypeAdapter(Identifier).validate_python(identifier)
    except (ValueError, TypeError):
        raise DomainError("RUN_CONTEXT", "审查上下文无效或已过期", 403) from None


def record_tool(connection, run_id, name, arguments, outcome):
    connection.execute(
        tool_call.insert().values(
            id=uuid4().hex,
            run_id=run_id,
            tool=name,
            called_at=utc_now(),
            arguments=arguments,
            outcome=outcome,
        )
    )


def tool_history(connection, run_id) -> list[dict]:
    return [
        dict(row)
        for row in connection.execute(
            select(tool_call)
            .where(tool_call.c.run_id == run_id)
            .order_by(tool_call.c.called_at, tool_call.c.id)
        ).mappings()
    ]


def snapshot_context(connection, secret, context):
    identifier = read_context(secret, context)
    run = (
        connection.execute(select(db.review).where(db.review.c.id == identifier)).mappings().first()
    )
    if not run or run["status"] != "RUNNING" or run["mode"] != "live":
        raise DomainError("RUN_CONTEXT", "审查运行不接受工具调用", 403)
    snapshot = dict(
        connection.execute(select(db.snapshot).where(db.snapshot.c.id == run["snapshot_id"]))
        .mappings()
        .one()
    )
    return run, snapshot


def create_mcp(
    settings: Settings,
    secret: bytes,
    engine: Engine,
    port: int = 8766,
    *,
    host: str = "127.0.0.1",
    allowed_hosts: tuple[str, ...] = (),
):
    if len(secret) < 32:
        raise DomainError("MCP_CONFIGURATION_MISSING", "需要至少 32 字节的 MCP 签名密钥", 503)
    if host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
        raise DomainError("MCP_CONFIGURATION_INVALID", "无法识别 MCP 监听地址", 503)
    if host == "0.0.0.0" and (not allowed_hosts or any("*" in value for value in allowed_hosts)):
        raise DomainError("MCP_CONFIGURATION_INVALID", "私网监听需要明确的 MCP Host 列表", 503)
    transport_security = (
        TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
        )
        if allowed_hosts
        else None
    )
    server = FastMCP(
        "FinCourt",
        host=host,
        transport_security=transport_security,
        port=port,
        json_response=True,
        stateless_http=True,
    )

    @server.tool()
    def query_evidence(context: str, metric: Identifier | None = None) -> dict:
        """Get verified disclosure citations from the current FinCourt run snapshot."""
        with engine.begin() as connection:
            run, snapshot = snapshot_context(connection, secret, context)
            docs = {item["id"]: item for item in snapshot["manifest"]["documents"]}
            facts = [
                item
                for item in bound_facts(connection, snapshot)
                if not metric or item["metric"] == metric
            ]
            applicable = applicable_facts(facts, snapshot["manifest"]["corrections"])
            checks = {}
            evidence = [
                {
                    **validate_citation(connection, item, docs, settings.storage_dir, checks),
                    "metric": item["metric"],
                    "value": item["value"],
                    "unit": item["unit"],
                    "applicable": item["id"] in applicable,
                    "report_period": [item["period_start"], item["period_end"]],
                    "published_date": item["published_date"],
                    "audit_status": item["audit_status"],
                }
                for item in facts[:60]
            ]
            record_tool(
                connection,
                run["id"],
                "query_evidence",
                {"metric": metric},
                {
                    "fact_ids": [item["fact_id"] for item in evidence],
                    "truncated": len(facts) > len(evidence),
                },
            )
            return {
                "snapshot_id": snapshot["id"],
                "evidence": evidence,
                "truncated": len(facts) > len(evidence),
            }

    @server.tool()
    def check_claim(context: str) -> dict:
        """Verify the registered claim, using the deterministic rules and bound snapshot."""
        with engine.begin() as connection:
            run, snapshot = snapshot_context(connection, secret, context)
            result = cited_result(
                connection, get_claim(connection, run["claim_id"]), snapshot, settings.storage_dir
            )
            fact_ids = sorted(item["fact_id"] for item in result["evidence"])
            record_tool(
                connection,
                run["id"],
                "check_claim",
                {},
                {
                    "decision": result["decision"],
                    "fact_ids": fact_ids,
                },
            )
            return {"snapshot_id": snapshot["id"], **result, "fact_ids": fact_ids}

    @server.tool()
    def diff_disclosures(context: str, earlier: Identifier, later: Identifier) -> dict:
        """Compare explicitly changed fields of two disclosures inside the run snapshot."""
        with engine.begin() as connection:
            run, snapshot = snapshot_context(connection, secret, context)
            docs = {item["id"] for item in snapshot["manifest"]["documents"]}
            if not {earlier, later}.issubset(docs):
                raise DomainError("EVIDENCE_OUT_OF_SCOPE", "变更文件超出资料快照", 403)
            changes = [
                change
                for change in disclosure_changes(connection, later)
                if change["before"] and change["before"]["document_id"] == earlier
            ]
            record_tool(
                connection,
                run["id"],
                "diff_disclosures",
                {"earlier": earlier, "later": later},
                {
                    "fact_ids": [item["after"]["id"] for item in changes],
                },
            )
            return {"snapshot_id": snapshot["id"], "changes": changes}

    @server.tool()
    def find_impacted_claims(context: str, event_id: Identifier) -> dict:
        """Return change-impact reasons for the claim of the current run."""
        with engine.begin() as connection:
            run, snapshot = snapshot_context(connection, secret, context)
            event = get_impact(connection, event_id)
            if event["document_id"] not in {
                item["id"] for item in snapshot["manifest"]["documents"]
            }:
                raise DomainError("EVIDENCE_OUT_OF_SCOPE", "变更事件超出资料快照", 403)
            items = [
                {"claim_id": item["claim_id"], "reasons": item["reasons"]}
                for item in event["claims"]
                if item["claim_id"] == run["claim_id"]
            ]
            record_tool(
                connection,
                run["id"],
                "find_impacted_claims",
                {"event_id": event_id},
                {
                    "fact_ids": [],
                    "claim_ids": [item["claim_id"] for item in items],
                },
            )
            return {"snapshot_id": snapshot["id"], "claims": items}

    @server.tool()
    def propose_patch(context: str, patch: PatchInput) -> dict:
        """Propose a declarative ontology patch using a document from the current snapshot."""
        with engine.begin() as connection:
            run, snapshot = snapshot_context(connection, secret, context)
            source = patch.example.amount_reference.document_id
            if source not in {item["id"] for item in snapshot["manifest"]["documents"]}:
                raise DomainError("EVIDENCE_OUT_OF_SCOPE", "补丁依据超出资料快照", 403)
            result = save_patch(connection, patch)
            record_tool(
                connection, run["id"], "propose_patch", {"patch_id": result["id"]}, {"fact_ids": []}
            )
            return {"patch_id": result["id"], "status": result["status"]}

    @server.tool()
    def evaluate_patch(context: str, patch_id: Identifier) -> dict:
        """Validate a visible ontology patch without approving or publishing it."""
        with engine.begin() as connection:
            run, snapshot = snapshot_context(connection, secret, context)
            proposed = get_patch(connection, patch_id)
            source = proposed["payload"]["example"]["amount_reference"]["document_id"]
            if source not in {item["id"] for item in snapshot["manifest"]["documents"]}:
                raise DomainError("EVIDENCE_OUT_OF_SCOPE", "补丁依据超出资料快照", 403)
            result = validate_patch(connection, patch_id, settings.storage_dir)
            record_tool(
                connection, run["id"], "evaluate_patch", {"patch_id": patch_id}, {"fact_ids": []}
            )
            return {
                "patch_id": patch_id,
                "status": result["status"],
                "checks": result["validation"]["checks"],
                "new_case_error": result["validation"]["new_case_error"],
            }

    return server
