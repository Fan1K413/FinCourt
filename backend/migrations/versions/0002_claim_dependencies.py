from alembic import op
from fincourt.schema_v2 import depends_on as dependencies_table
from fincourt.schema_v2 import proof

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    dependencies_table.create(op.get_bind())
    proof.create(op.get_bind())


def downgrade():
    proof.drop(op.get_bind())
    dependencies_table.drop(op.get_bind())
