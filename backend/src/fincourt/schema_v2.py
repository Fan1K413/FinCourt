from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table

# Kept on separate metadata so migration 0001 remains immutable.
metadata = MetaData()
claim_reference = Table("claims", metadata, Column("id", String(96), primary_key=True))
depends_on = Table(
    "claim_dependencies",
    metadata,
    Column("claim_id", ForeignKey(claim_reference.c.id), primary_key=True),
    Column("dependency_id", ForeignKey(claim_reference.c.id), primary_key=True, index=True),
)
proof = Table(
    "claim_proofs",
    metadata,
    Column("claim_id", ForeignKey(claim_reference.c.id), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("rule_id", String(64), nullable=False),
)
