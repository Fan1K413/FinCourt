from sqlalchemy import JSON, Column, ForeignKey, Integer, MetaData, String, Table

metadata = MetaData()
document_reference = Table("documents", metadata, Column("id", String(96), primary_key=True))
claim_reference = Table("claims", metadata, Column("id", String(96), primary_key=True))
run_reference = Table("review_runs", metadata, Column("id", String(96), primary_key=True))
change_event = Table(
    "change_events",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("document_id", ForeignKey(document_reference.c.id), nullable=False, unique=True),
    Column("created_at", String(40), nullable=False),
    Column("changes", JSON, nullable=False),
    Column("fixed_claims_preserved", JSON, nullable=False),
)
impact = Table(
    "change_impacts",
    metadata,
    Column("event_id", ForeignKey(change_event.c.id), primary_key=True),
    Column("claim_id", ForeignKey(claim_reference.c.id), primary_key=True, index=True),
    Column("position", Integer, nullable=False),
    Column("run_id", ForeignKey(run_reference.c.id), unique=True),
    Column("status", String(24), nullable=False),
    Column("reasons", JSON, nullable=False),
)
