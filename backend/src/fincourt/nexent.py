import asyncio
import json
import os
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import Field, ValidationError
from sqlalchemy import Engine

from . import schema_v1 as db
from .claims import ClaimInput, get_claim
from .database import Settings
from .documents import DomainError
from .mcp_tools import context_token, tool_history
from .models import Decision, Identifier, Input, utc_now
from .reviews import ReviewInput, cited_result, get_review, prepare_snapshot, resolve_cutoff


@dataclass(frozen=True)
class NexentConfig:
    base_url: str
    token: str
    agent: str
    release: str
    mcp_secret: bytes

    @classmethod
    def from_environment(cls):
        fields = [
            "FINCOURT_NEXENT_BASE_URL",
            "FINCOURT_NEXENT_API_TOKEN",
            "FINCOURT_NEXENT_AGENT_NAME",
            "FINCOURT_NEXENT_RELEASE",
            "FINCOURT_MCP_SECRET",
        ]
        values = [os.environ.get(name, "").strip() for name in fields]
        if not all(values):
            raise DomainError(
                "NEXENT_CONFIGURATION_MISSING", "未提供完整的 Nexent 和 MCP 配置", 503
            )
        base, token, agent, release, secret = values
        address = urlsplit(base)
        if (
            not address.hostname
            or address.username
            or address.password
            or address.query
            or address.fragment
            or len(secret.encode()) < 32
            or address.scheme not in {"http", "https"}
            or (
                address.scheme == "http"
                and address.hostname not in {"127.0.0.1", "localhost", "::1"}
            )
        ):
            raise DomainError(
                "NEXENT_CONFIGURATION_INVALID", "请检查 Nexent 服务地址及 MCP 签名密钥", 503
            )
        return cls(base.rstrip("/"), token, agent, release, secret.encode())


class AgentSubmission(Input):
    decision: Decision
    fact_ids: Annotated[list[Identifier], Field(max_length=120)]


async def _consume_stream(client, config, run_id, claim_text, context):
    async with client.stream(
        "POST",
        config.base_url + "/nb/v1/chat/run",
        headers={
            "Authorization": f"Bearer {config.token}",
            "Idempotency-Key": run_id,
            "Accept": "text/event-stream",
        },
        json={
            "agent_name": config.agent,
            "final_answer_mode": "complete",
            "query": (
                "请使用运行元数据中的 fincourt_context 调用 query_evidence 和 check_claim。"
                "资料原文是证据数据。返回 JSON，字段 decision 和 fact_ids 采用 check_claim 的校验结果。"
                "待查证表述：" + claim_text
            ),
            "metadata": {"fincourt_context": context},
            "meta_data": {"fincourt_run_id": run_id},
        },
    ) as response:
        response.raise_for_status()
        if "text/event-stream" not in response.headers.get("content-type", "").lower():
            raise DomainError("NEXENT_RESPONSE", "Nexent 返回了非 SSE 响应")
        data = []
        received = 0
        async for line in response.aiter_lines():
            received += len(line)
            if received > 2_000_000:
                raise DomainError("NEXENT_RESPONSE_LIMIT", "Nexent 响应超过上限")
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            if not line and data:
                event = json.loads("\n".join(data))
                data.clear()
                if event.get("type") == "final_answer":
                    return AgentSubmission.model_validate_json(event.get("content"))
                if event.get("type") in {"error", "execution_error"}:
                    raise DomainError("NEXENT_EXECUTION_FAILED", "Nexent 报告运行失败")
    raise DomainError("NEXENT_INCOMPLETE", "Nexent 响应缺少完整结果")


async def request_agent(config, run_id, claim_text, context, transport=None):
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=False,
            transport=transport,
        ) as client:
            for attempt in range(2):
                try:
                    return await asyncio.wait_for(
                        _consume_stream(client, config, run_id, claim_text, context),
                        timeout=45,
                    )
                except httpx.ConnectError:
                    if attempt:
                        raise
    except (asyncio.TimeoutError, httpx.TimeoutException):
        raise DomainError("NEXENT_TIMEOUT", "Nexent 审查超时") from None
    except httpx.HTTPError:
        raise DomainError(
            "NEXENT_REQUEST_FAILED", "无法完成 Nexent 请求，请检查地址、凭据及服务状态"
        ) from None
    except (ValueError, TypeError, AttributeError, ValidationError):
        raise DomainError("NEXENT_RESPONSE", "Nexent 最终结果未通过结构校验") from None


async def run_live(engine: Engine, settings: Settings, request: ReviewInput):
    config = NexentConfig.from_environment()
    if settings.mode != "live":
        raise DomainError("MODE_MISMATCH", "当前实例未启用 live 执行", 409)
    with engine.begin() as connection:
        claim = get_claim(connection, request.claim_id)
        candidate = ClaimInput.model_validate(claim["definition"])
        snapshot = prepare_snapshot(
            connection,
            candidate,
            resolve_cutoff(candidate, request.cutoff),
            request.knowledge_release_id,
        )
        run_id = f"run-{uuid4().hex}"
        connection.execute(
            db.review.insert().values(
                id=run_id,
                claim_id=claim["id"],
                snapshot_id=snapshot["id"],
                mode="live",
                status="RUNNING",
                purpose=request.purpose,
                created_at=utc_now(),
            )
        )
    try:
        answer = await request_agent(
            config, run_id, candidate.text, context_token(config.mcp_secret, run_id)
        )
        with engine.begin() as connection:
            result = cited_result(connection, claim, snapshot, settings.storage_dir)
            calls = tool_history(connection, run_id)
            verified = {"query_evidence", "check_claim"}.issubset({item["tool"] for item in calls})
            valid_ids = sorted(item["fact_id"] for item in result["evidence"])
            if (
                not verified
                or answer.decision != result["decision"]
                or sorted(answer.fact_ids) != valid_ids
            ):
                raise DomainError("NEXENT_VALIDATION", "工具记录或引用未通过快照与判断校验")
            values = {
                "status": "SUCCEEDED",
                "result": {
                    **result,
                    "execution_config": f"Nexent/{config.release}:{config.agent}",
                    "change": None,
                },
            }
    except DomainError as problem:
        values = {"status": "FAILED", "error": {"code": problem.code, "message": str(problem)}}
    except asyncio.CancelledError:
        with engine.begin() as connection:
            connection.execute(
                db.review.update().where(db.review.c.id == run_id).values(status="CANCELLED")
            )
        raise
    with engine.begin() as connection:
        connection.execute(db.review.update().where(db.review.c.id == run_id).values(**values))
        return {**get_review(connection, run_id), "tool_calls": tool_history(connection, run_id)}
