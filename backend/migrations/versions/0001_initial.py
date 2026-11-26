from alembic import op
from fincourt.schema_v1 import metadata, release

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind(), checkfirst=False)
    # Store an actual version bundle before any document or review enters the database.
    op.bulk_insert(
        release,
        [
            {
                "id": "fincourt-disclosures-v1",
                "manifest": {
                    "ontology": "financial-disclosure-v1",
                    "rules": "decimal-comparison-v1",
                    "projection": "normalized-facts-v1",
                    "retrieval": "public-time-filter-v1",
                    "data_revision": 0,
                    "metrics": ["operating_revenue", "operating_profit", "total_assets"],
                },
            }
        ],
    )


def downgrade():
    metadata.drop_all(op.get_bind(), checkfirst=False)
