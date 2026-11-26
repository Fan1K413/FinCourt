from alembic import op
from fincourt.schema_v4 import change_event, impact

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    change_event.create(op.get_bind())
    impact.create(op.get_bind())


def downgrade():
    impact.drop(op.get_bind())
    change_event.drop(op.get_bind())
