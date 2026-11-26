"""Initial migration schema. Freeze this module once revision 0001 ships."""

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

release = Table(
    "knowledge_releases",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("manifest", JSON, nullable=False),
)
document = Table(
    "documents",
    metadata,
    Column("id", String(96), primary_key=True),
    Column("title", String(240), nullable=False),
    Column("subject", String(96), nullable=False, index=True),
    Column("source_kind", String(16), nullable=False),
    Column("source_url", Text),
    Column("source_cluster", String(160)),
    Column("disclosure_type", String(24), nullable=False),
    Column("published_date", String(10), nullable=False, index=True),
    Column("published_at", String(40)),
    Column("ingested_at", String(40), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("media_type", String(24), nullable=False),
    Column("import_hash", String(64), nullable=False),
)
parse = Table(
    "document_parses",
    metadata,
    Column("document_id", ForeignKey("documents.id"), primary_key=True),
    Column("version", String(64), primary_key=True),
    Column("page_count", Integer, nullable=False),
)
page = Table(
    "pages",
    metadata,
    Column("document_id", String(96), primary_key=True),
    Column("parse_version", String(64), primary_key=True),
    Column("number", Integer, primary_key=True),
    Column("text", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    ForeignKeyConstraint(
        ["document_id", "parse_version"], ["document_parses.document_id", "document_parses.version"]
    ),
)
evidence = Table(
    "evidence_spans",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("document_id", String(96), nullable=False),
    Column("parse_version", String(64), nullable=False),
    Column("page", Integer, nullable=False),
    Column("start", Integer, nullable=False),
    Column("end", Integer, nullable=False),
    Column("text", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    ForeignKeyConstraint(
        ["document_id", "parse_version", "page"],
        ["pages.document_id", "pages.parse_version", "pages.number"],
    ),
)
fact = Table(
    "facts",
    metadata,
    Column("id", String(96), primary_key=True),
    Column("document_id", ForeignKey("documents.id"), nullable=False, index=True),
    Column("evidence_id", ForeignKey("evidence_spans.id"), nullable=False),
    Column("metric", String(96), nullable=False, index=True),
    Column("period_start", String(10), nullable=False),
    Column("period_end", String(10), nullable=False),
    Column("scope", String(24), nullable=False),
    Column("currency", String(3)),
    Column("audit_status", String(24), nullable=False),
    Column("value", String(64), nullable=False),
    Column("unit", String(24), nullable=False),
)
correction = Table(
    "corrections",
    metadata,
    Column("replacement_id", ForeignKey("facts.id"), primary_key=True),
    Column("original_id", ForeignKey("facts.id"), primary_key=True, index=True),
)
claim = Table(
    "claims",
    metadata,
    Column("id", String(96), primary_key=True),
    Column("subject", String(96), nullable=False, index=True),
    Column("source_kind", String(16), nullable=False),
    Column("timing", String(16), nullable=False),
    Column("version", Integer, nullable=False),
    Column("definition", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)
snapshot = Table(
    "evidence_snapshots",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("knowledge_release_id", ForeignKey("knowledge_releases.id"), nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)
review = Table(
    "review_runs",
    metadata,
    Column("id", String(96), primary_key=True),
    Column("claim_id", ForeignKey("claims.id"), nullable=False, index=True),
    Column("snapshot_id", ForeignKey("evidence_snapshots.id"), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("purpose", String(24), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("result", JSON),
    Column("error", JSON),
)
