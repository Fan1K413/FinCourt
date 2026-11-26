import hashlib
import json
import os
import re
import tempfile
from datetime import timezone
from decimal import Decimal
from pathlib import Path
from threading import Lock

import pypdfium2
from sqlalchemy import and_, select
from sqlalchemy.engine import Connection

from . import schema_v1 as db
from .models import Cutoff, DocumentInput, utc_now
from .schema_v5 import knowledge_state

PDF_PARSE = f"pdfium-{pypdfium2.PYPDFIUM_INFO}"
TEXT_PARSE = "fixture-text-v1"
MAX_FILE = 12 * 1024 * 1024
MAX_PAGES = 300
MAX_TEXT = 2_000_000
FACT_SCOPE = ("metric", "period_start", "period_end", "scope", "currency")
PDF_LOCK = Lock()
NUMBER = re.compile(r"(?<![0-9.,])-?[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?(?![0-9.,])")


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


def sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def parse_pages(data: bytes, media_type: str) -> tuple[str, list[str]]:
    if not data or len(data) > MAX_FILE:
        raise DomainError("FILE_SIZE", "文件大小必须在 1 字节到 12 MiB 之间")
    try:
        if media_type == "application/pdf" and data.startswith(b"%PDF-"):
            with PDF_LOCK, pypdfium2.PdfDocument(data) as pdf:
                if not 1 <= len(pdf) <= MAX_PAGES:
                    raise DomainError("PAGE_LIMIT", "PDF 页数必须在 1 到 300 之间")
                pages = []
                for number in range(len(pdf)):
                    pdf_page = pdf[number]
                    text_page = pdf_page.get_textpage()
                    try:
                        pages.append(text_page.get_text_bounded().replace("\r\n", "\n"))
                    finally:
                        text_page.close()
                        pdf_page.close()
            version = PDF_PARSE
        elif media_type == "text/plain":
            pages = data.decode("utf-8").replace("\r\n", "\n").split("\f")
            version = TEXT_PARSE
        else:
            raise DomainError("FILE_TYPE", "请上传带文本层的 PDF")
    except (UnicodeError, pypdfium2.PdfiumError) as error:
        raise DomainError("PARSE_FAILED", "无法解析文件") from error
    if len(pages) > MAX_PAGES or sum(map(len, pages)) > MAX_TEXT:
        raise DomainError("TEXT_LIMIT", "文本页数或长度超出限制")
    if not any(text.strip() for text in pages):
        raise DomainError("NO_TEXT", "未提取到文本，当前版本需要文本层")
    if any("\0" in text for text in pages):
        raise DomainError("PARSE_FAILED", "文本包含无效字符")
    return version, pages


def store_bytes(directory: Path, file_hash: str, content: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / file_hash
    if target.is_file():
        if sha256(target.read_bytes()) != file_hash:
            raise DomainError("STORAGE_INTEGRITY", "原文件哈希校验失败", 500)
        return
    fd, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        if os.environ.get("FINCOURT_EVIDENCE_GROUP_READ") == "1":
            os.chmod(temporary, 0o640)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def annotate_page(document_id: str, version: str, number: int, text: str, quote: str) -> dict:
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        raise DomainError("INVALID_CITATION", f"第 {number} 页未找到唯一匹配的引文")
    return {
        "id": digest([document_id, version, number, start, quote]),
        "document_id": document_id,
        "parse_version": version,
        "page": number,
        "start": start,
        "end": start + len(quote),
        "text": quote,
        "sha256": sha256(quote),
    }


def _old_fact(connection: Connection, fact_id: str):
    return (
        connection.execute(
            select(
                db.fact,
                db.document.c.subject,
                db.document.c.source_kind,
                db.document.c.published_date,
                db.document.c.published_at,
            )
            .join(db.document, db.fact.c.document_id == db.document.c.id)
            .where(db.fact.c.id == fact_id)
        )
        .mappings()
        .first()
    )


def import_document(
    connection: Connection, directory: Path, metadata, data: bytes, media_type: str
) -> dict:
    item = DocumentInput.model_validate(metadata)
    if not data or len(data) > MAX_FILE:
        raise DomainError("FILE_SIZE", "文件大小必须在 1 字节到 12 MiB 之间")
    if item.source_kind == "REAL" and media_type != "application/pdf":
        raise DomainError("FILE_TYPE", "真实公告须上传原始 PDF")
    file_hash = sha256(data)
    payload = item.model_dump(mode="json", exclude={"facts"})
    payload["published_at"] = (
        item.published_at.astimezone(timezone.utc).isoformat() if item.published_at else None
    )
    signature = digest(
        [payload, file_hash, media_type, [f.model_dump(mode="json") for f in item.facts]]
    )
    current = (
        connection.execute(select(db.document).where(db.document.c.id == item.id))
        .mappings()
        .first()
    )
    if current:
        if current["import_hash"] != signature:
            raise DomainError("DOCUMENT_CONFLICT", "资料 ID 已绑定另一版本，请使用新 ID", 409)
        store_bytes(directory, file_hash, data)
        return get_document(connection, item.id)

    version, pages = parse_pages(data, media_type)
    connection.execute(
        knowledge_state.update()
        .where(knowledge_state.c.id == 1)
        .values(
            data_revision=knowledge_state.c.data_revision + 1,
        )
    )
    connection.execute(
        db.document.insert().values(
            **payload,
            ingested_at=utc_now(),
            sha256=file_hash,
            media_type=media_type,
            import_hash=signature,
        )
    )
    connection.execute(
        db.parse.insert().values(document_id=item.id, version=version, page_count=len(pages))
    )
    connection.execute(
        db.page.insert(),
        [
            dict(
                document_id=item.id,
                parse_version=version,
                number=number,
                text=text,
                sha256=sha256(text),
            )
            for number, text in enumerate(pages, 1)
        ],
    )

    spans = set()
    for candidate in item.facts:
        if candidate.page > len(pages):
            raise DomainError("INVALID_CITATION", "引用页码超出 PDF 页数")
        span = annotate_page(
            item.id, version, candidate.page, pages[candidate.page - 1], candidate.quote
        )
        cited_numbers = {Decimal(text.replace(",", "")) for text in NUMBER.findall(span["text"])}
        if Decimal(candidate.value) not in cited_numbers:
            raise DomainError("CITATION_VALUE_MISMATCH", "引用中未找到候选事实的原始数值")
        if span["id"] not in spans:
            connection.execute(db.evidence.insert().values(**span))
            spans.add(span["id"])
        values = candidate.model_dump(mode="json", exclude={"page", "quote", "period", "replaces"})
        values.update(
            document_id=item.id,
            evidence_id=span["id"],
            period_start=candidate.period.start.isoformat(),
            period_end=candidate.period.end.isoformat(),
        )
        connection.execute(db.fact.insert().values(**values))
        for original_id in sorted(set(candidate.replaces)):
            original = _old_fact(connection, original_id)
            if not original or original["document_id"] == item.id:
                raise DomainError("INVALID_CORRECTION", "更正必须引用既有资料中的事实")
            if (
                original["subject"] != item.subject
                or original["source_kind"] != item.source_kind
                or any(original[key] != values[key] for key in FACT_SCOPE)
                or candidate.scope == "UNKNOWN"
                or original["published_date"] > payload["published_date"]
                or (
                    original["published_date"] == payload["published_date"]
                    and original["published_at"]
                    and payload["published_at"]
                    and original["published_at"] >= payload["published_at"]
                )
            ):
                raise DomainError("INVALID_CORRECTION", "更正的主体、口径或时间不匹配")
            connection.execute(
                db.correction.insert().values(replacement_id=candidate.id, original_id=original_id)
            )

    store_bytes(directory, file_hash, data)
    return get_document(connection, item.id)


def _document_rows(connection: Connection, document_id: str | None = None) -> list[dict]:
    query = (
        select(db.document, db.parse.c.version, db.parse.c.page_count)
        .join(db.parse, db.parse.c.document_id == db.document.c.id)
        .order_by(db.document.c.published_date.desc(), db.document.c.id, db.parse.c.version)
    )
    if document_id:
        query = query.where(db.document.c.id == document_id)
    by_id = {}
    for row in connection.execute(query).mappings():
        payload = by_id.setdefault(
            row["id"],
            {
                **{key: row[key] for key in db.document.c.keys() if key != "import_hash"},
                "time_precision": "TIMESTAMP" if row["published_at"] else "DATE_ONLY",
                "parses": [],
            },
        )
        payload["parses"].append({"version": row["version"], "page_count": row["page_count"]})
    return list(by_id.values())


def list_documents(connection: Connection) -> list[dict]:
    return _document_rows(connection)


def get_document(connection: Connection, document_id: str) -> dict:
    rows = _document_rows(connection, document_id)
    if not rows:
        raise DomainError("DOCUMENT_NOT_FOUND", "资料不存在", 404)
    return rows[0]


def get_page(connection: Connection, document_id: str, number: int, version: str | None) -> dict:
    version = version or get_document(connection, document_id)["parses"][-1]["version"]
    row = (
        connection.execute(
            select(db.page).where(
                db.page.c.document_id == document_id,
                db.page.c.number == number,
                db.page.c.parse_version == version,
            )
        )
        .mappings()
        .first()
    )
    if not row:
        raise DomainError("PAGE_NOT_FOUND", "该解析版本中没有此页", 404)
    return dict(row)


def fact_rows(connection: Connection, document_ids: list[str]) -> list[dict]:
    query = (
        select(
            db.fact,
            db.document.c.title,
            db.document.c.subject,
            db.document.c.source_cluster,
            db.document.c.disclosure_type,
            db.document.c.published_date,
            db.document.c.published_at,
            db.document.c.source_kind,
            db.document.c.media_type,
        )
        .join(db.document)
        .where(db.fact.c.document_id.in_(document_ids))
        .order_by(db.document.c.published_date, db.document.c.published_at, db.fact.c.id)
    )
    return [dict(row) for row in connection.execute(query).mappings()]


def evidence_for(connection: Connection, item: dict) -> dict:
    span = (
        connection.execute(
            select(db.evidence).where(
                and_(
                    db.evidence.c.id == item["evidence_id"],
                    db.evidence.c.document_id == item["document_id"],
                )
            )
        )
        .mappings()
        .first()
    )
    if not span:
        raise DomainError("INVALID_CITATION", "证据定位不存在")
    page = get_page(connection, span["document_id"], span["page"], span["parse_version"])
    if (
        sha256(page["text"]) != page["sha256"]
        or page["text"][span["start"] : span["end"]] != span["text"]
        or sha256(span["text"]) != span["sha256"]
    ):
        raise DomainError("INVALID_CITATION", "证据与解析快照不一致")
    return {**span, "title": item["title"], "media_type": item["media_type"]}


def visible_evidence(
    connection: Connection,
    cutoff: Cutoff,
    subject: str,
    source_kind: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    eligible = [
        item
        for item in list_documents(connection)
        if item["subject"] == subject
        and item["source_kind"] == source_kind
        and cutoff.includes(item["published_date"], item["published_at"])
    ]
    facts = fact_rows(connection, [item["id"] for item in eligible])
    ids = [item["id"] for item in facts]
    edges = [
        dict(row)
        for row in connection.execute(
            select(db.correction)
            .where(db.correction.c.replacement_id.in_(ids), db.correction.c.original_id.in_(ids))
            .order_by(db.correction.c.original_id, db.correction.c.replacement_id)
        ).mappings()
    ]
    return eligible, facts, edges


def applicable_facts(facts, edges, as_disclosed_document=None) -> dict:
    replaced = {edge["original_id"] for edge in edges}
    replacements = {edge["replacement_id"] for edge in edges}
    return {
        item["id"]: item
        for item in facts
        if item["disclosure_type"] != "COMMENTARY"
        and (
            item["document_id"] == as_disclosed_document
            or (
                item["id"] not in replaced
                and (item["disclosure_type"] != "CORRECTION" or item["id"] in replacements)
            )
        )
    }
