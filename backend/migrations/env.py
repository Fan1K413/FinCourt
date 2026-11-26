from alembic import context
from fincourt.database import settings
from fincourt.schema_v1 import metadata

config = context.config
application_settings = settings()

if context.is_offline_mode():
    context.configure(
        url=application_settings.database_url, target_metadata=metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = config.attributes.get("engine") or application_settings.engine()
    with engine.begin() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
