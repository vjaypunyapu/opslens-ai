# Re-export the async session factory from the shared API db module so worker
# tasks can use `from ..db import AsyncSession` without duplicating config.
from apps.api.db.session import AsyncSessionFactory as AsyncSession  # noqa: F401
