from alembic import op
from fincourt.schema_v3 import tool_call

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    tool_call.create(op.get_bind())


def downgrade():
    tool_call.drop(op.get_bind())
