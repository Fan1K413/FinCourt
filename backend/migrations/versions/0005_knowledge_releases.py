from alembic import op
from fincourt.schema_v1 import document
from fincourt.schema_v5 import history, knowledge_state, ontology_patch, projection
from sqlalchemy import func, select

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    for table in (knowledge_state, ontology_patch, projection, history):
        table.create(connection)
    existing = connection.scalar(select(func.count()).select_from(document))
    connection.execute(
        knowledge_state.insert().values(
            id=1,
            active_release="fincourt-disclosures-v1",
            data_revision=existing,
        )
    )


def downgrade():
    for table in (history, projection, ontology_patch, knowledge_state):
        table.drop(op.get_bind())
