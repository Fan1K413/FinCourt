import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from fincourt import schema_v1 as db
from fincourt.agreement import import_agreement
from fincourt.changes import confirm_event
from fincourt.claims import ClaimInput, create_claim
from fincourt.database import Settings
from fincourt.demo import load_demo_claims, load_disclosures
from fincourt.documents import DomainError
from fincourt.mcp_tools import context_token, create_mcp
from fincourt.models import Cutoff, utc_now
from fincourt.nexent import NexentConfig, request_agent, run_live
from fincourt.reviews import ReviewInput, prepare_snapshot
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import select


def test_nexent_wire_and_mcp_snapshot_binding(workspace, monkeypatch):
    base, engine, _ = workspace
    with engine.begin() as db_context:
        load_disclosures(db_context, base.storage_dir, "SIMULATED", "all")
        load_demo_claims(db_context, base.storage_dir, "SIMULATED")
    settings = Settings(base.database_url, base.storage_dir, "live")
    config = NexentConfig(
        "https://nexent.example.invalid/api",
        "test-key",
        "test-agent",
        "test-protocol",
        b"test-signing-key-only-abcdefghijklmnopqrstuvwxyz",
    )
    monkeypatch.setattr(NexentConfig, "from_environment", lambda: config)
    server = create_mcp(settings, config.mcp_secret, engine)
    check = {"claim_id": "sample-c2", "execution_mode": "live"}

    async def agent(o, run_id, claim_text, signed):
        async with create_connected_server_and_client_session(server) as session:
            assert {item.name for item in (await session.list_tools()).tools} == {
                "query_evidence",
                "check_claim",
                "diff_disclosures",
                "find_impacted_claims",
                "propose_patch",
                "evaluate_patch",
            }
            invalid = await session.call_tool("query_evidence", {"context": signed + "x"})
            assert invalid.isError
            outside = await session.call_tool(
                "diff_disclosures",
                {
                    "context": signed,
                    "earlier": "sample-original",
                    "later": "sample-correction",
                },
            )
            assert outside.isError
            evidence = await session.call_tool(
                "query_evidence",
                {
                    "context": signed,
                    "metric": "operating_revenue",
                },
            )
            assert not evidence.isError, evidence.content
            ready = evidence.structuredContent or json.loads(evidence.content[0].text)
            assert [item["fact_id"] for item in ready["evidence"]] == ["sample-revenue-original"]
            checked = await session.call_tool("check_claim", {"context": signed})
            assert not checked.isError, checked.content
            result = checked.structuredContent or json.loads(checked.content[0].text)

            def wire(request):
                assert str(request.url).endswith("/api/nb/v1/chat/run")
                assert request.headers["Idempotency-Key"] == run_id
                payload = json.loads(request.content)
                assert "conversation_id" not in payload
                assert payload["metadata"]["fincourt_context"] == signed
                response = {"decision": result["decision"], "fact_ids": result["fact_ids"]}
                sse = {"type": "final_answer", "content": json.dumps(response)}
                return httpx.Response(
                    200,
                    text="data: " + json.dumps(sse) + "\n\n",
                    headers={
                        "content-type": "text/event-stream",
                    },
                )

            return await request_agent(o, run_id, claim_text, signed, httpx.MockTransport(wire))

    monkeypatch.setattr("fincourt.nexent.request_agent", agent)
    result = asyncio.run(run_live(engine, settings, ReviewInput.model_validate(check)))
    assert result["status"] == "SUCCEEDED"
    assert [item["tool"] for item in result["tool_calls"]] == ["query_evidence", "check_claim"]
    assert "test-signing-key" not in json.dumps(result)
    assert result["result"]["decision"] == "SUPPORTED"
    with engine.connect() as connection:
        assert (
            connection.scalar(select(db.review.c.mode).where(db.review.c.id == result["id"]))
            == "live"
        )


def test_output_without_mcp_evidence_never_succeeds(workspace, monkeypatch):
    from fincourt.nexent import AgentSubmission

    base, engine, _ = workspace
    with engine.begin() as context:
        load_disclosures(context, base.storage_dir, "SIMULATED", "initial")
        load_demo_claims(context, base.storage_dir, "SIMULATED")
    config = NexentConfig("https://test.invalid", "test", "test", "test", b"test-key")
    monkeypatch.setattr(NexentConfig, "from_environment", lambda: config)

    async def unverified(*_):
        return AgentSubmission(decision="SUPPORTED", fact_ids=["sample-revenue-original"])

    monkeypatch.setattr("fincourt.nexent.request_agent", unverified)
    item = asyncio.run(
        run_live(
            engine,
            Settings(base.database_url, base.storage_dir, "live"),
            ReviewInput(claim_id="sample-c2", execution_mode="live"),
        )
    )
    assert item["status"] == "FAILED"
    assert item["error"]["code"] == "NEXENT_VALIDATION"
    assert item["result"] is None


def test_mcp_change_and_patch_tools(workspace):
    settings, engine, _ = workspace
    secret = b"local-only-test-key-abcdefghijklmnopqrstuvwxyz"
    with engine.begin() as connection:
        load_disclosures(connection, settings.storage_dir, "SIMULATED", "all")
        load_demo_claims(connection, settings.storage_dir, "SIMULATED")
        event = confirm_event(connection, "sample-correction", settings.storage_dir)
        try:
            patch = import_agreement(connection, settings.storage_dir)
        except DomainError as error:
            if error.code == "SOURCE_MISSING":
                pytest.skip("运行 import-agreement 后检查协议的 MCP 工具")
            raise
        specs = [
            ("change-tools", "sample-c5", "DEMO-XINGQIAO", "SIMULATED", "2025-04-13"),
            ("patch-tools", "agreement-claim", "001229", "REAL", "2026-11-22"),
        ]
        for run_id, claim_id, subject, source, day in specs:
            candidate = ClaimInput(
                subject=subject,
                source_kind=source,
                kind="FORECAST",
                text="公开资料包含后续事项披露。",
            )
            if source == "REAL":
                create_claim(connection, candidate.model_copy(update={"id": claim_id}))
            snapshot = prepare_snapshot(connection, candidate, Cutoff(date=day), None)
            connection.execute(
                db.review.insert().values(
                    id=run_id,
                    claim_id=claim_id,
                    snapshot_id=snapshot["id"],
                    mode="live",
                    status="RUNNING",
                    purpose="REVIEW",
                    created_at=utc_now(),
                )
            )
    server = create_mcp(settings, secret, engine)

    async def verify():
        async with create_connected_server_and_client_session(server) as session:

            async def call(name, run_id, **arguments):
                result = await session.call_tool(
                    name,
                    {
                        "context": context_token(secret, run_id),
                        **arguments,
                    },
                )
                assert not result.isError, result.content
                return result.structuredContent or json.loads(result.content[0].text)

            diff = await call(
                "diff_disclosures",
                "change-tools",
                earlier="sample-original",
                later="sample-correction",
            )
            assert [item["after"]["id"] for item in diff["changes"]] == ["sample-revenue-corrected"]
            affected = await call("find_impacted_claims", "change-tools", event_id=event["id"])
            assert [item["claim_id"] for item in affected["claims"]] == ["sample-c5"]
            proposed = await call("propose_patch", "patch-tools", patch=patch["payload"])
            assert proposed["patch_id"] == patch["id"]
            reviewed = await call("evaluate_patch", "patch-tools", patch_id=patch["id"])
            assert reviewed["status"] == "READY_FOR_REVIEW"
            assert all(item["passed"] for item in reviewed["checks"])

    asyncio.run(verify())


def test_nexent_timeout_and_bounded_connection_retry():
    config = NexentConfig("https://test.invalid", "test", "test", "test", b"test-key")
    attempts = []

    def connection_error(request):
        attempts.append(request.headers["Idempotency-Key"])
        raise httpx.ConnectError("test transport")

    with pytest.raises(DomainError) as unavailable:
        asyncio.run(
            request_agent(
                config, "review-id", "claim", "context", httpx.MockTransport(connection_error)
            )
        )
    assert unavailable.value.code == "NEXENT_REQUEST_FAILED"
    assert attempts == ["review-id", "review-id"]

    def read_timeout(_request):
        raise httpx.ReadTimeout("test transport")

    with pytest.raises(DomainError) as timed_out:
        asyncio.run(
            request_agent(
                config, "review-id", "claim", "context", httpx.MockTransport(read_timeout)
            )
        )
    assert timed_out.value.code == "NEXENT_TIMEOUT"


def test_private_mcp_listener_requires_explicit_host(workspace):
    settings, engine, _ = workspace
    secret = b"x" * 32
    with pytest.raises(DomainError) as missing_hosts:
        create_mcp(settings, secret, engine, host="0.0.0.0")
    assert missing_hosts.value.code == "MCP_CONFIGURATION_INVALID"

    server = create_mcp(
        settings, secret, engine, host="0.0.0.0", allowed_hosts=("fincourt-mcp:8766",)
    )
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "deployment-check", "version": "1"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(server.streamable_http_app(), base_url="http://fincourt-mcp:8766") as client:
        assert client.post("/mcp", headers=headers, json=request).status_code == 200
        foreign = client.post(
            "/mcp", headers={**headers, "Host": "unlisted.invalid:8766"}, json=request
        )
        assert foreign.status_code == 421


def test_nexent_rejects_missing_configured_ca(tmp_path):
    config = NexentConfig(
        "https://nexent.example.invalid",
        "test-token",
        "test-agent",
        "v2.8.5",
        b"x" * 32,
        str(tmp_path / "missing.pem"),
    )
    with pytest.raises(DomainError) as invalid:
        asyncio.run(request_agent(config, "review-id", "claim", "context"))
    assert invalid.value.code == "NEXENT_CA_INVALID"
