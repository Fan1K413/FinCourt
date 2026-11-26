from sqlalchemy import JSON, Column, ForeignKey, Integer, MetaData, String, Table

metadata = MetaData()
release_ref = Table("knowledge_releases", metadata, Column("id", String(64), primary_key=True))
document_ref = Table("documents", metadata, Column("id", String(96), primary_key=True))
knowledge_state = Table(
    "knowledge_state",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("active_release", ForeignKey(release_ref.c.id), nullable=False),
    Column("data_revision", Integer, nullable=False),
)
ontology_patch = Table(
    "ontology_patches",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("base_release", ForeignKey(release_ref.c.id), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("status", String(24), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("validation", JSON),
    Column("approval", JSON),
    Column("published_release", ForeignKey(release_ref.c.id)),
)
projection = Table(
    "agreement_projections",
    metadata,
    Column("release_id", ForeignKey(release_ref.c.id), primary_key=True),
    Column("document_id", ForeignKey(document_ref.c.id), primary_key=True),
    Column("value", JSON, nullable=False),
)
history = Table(
    "knowledge_publication_history",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("applied_at", String(40), nullable=False),
    Column("previous_release", ForeignKey(release_ref.c.id), nullable=False),
    Column("release_id", ForeignKey(release_ref.c.id), nullable=False),
    Column("action", String(16), nullable=False),
    Column("principal", String(96), nullable=False),
)
