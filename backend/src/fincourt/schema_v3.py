from sqlalchemy import JSON, Column, ForeignKey, MetaData, String, Table

metadata = MetaData()
run_reference = Table("review_runs", metadata, Column("id", String(96), primary_key=True))
tool_call = Table(
    "review_tool_calls",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("run_id", ForeignKey(run_reference.c.id), nullable=False, index=True),
    Column("tool", String(64), nullable=False),
    Column("called_at", String(40), nullable=False),
    Column("arguments", JSON, nullable=False),
    Column("outcome", JSON, nullable=False),
)
