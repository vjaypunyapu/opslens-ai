"""
OpsLens AI – Qdrant Collection Initialisation
Run once per tenant during onboarding, or as an idempotent startup check.

Usage:
    python scripts/init_qdrant_collections.py --tenant-id <uuid>
    python scripts/init_qdrant_collections.py --all-tenants
"""
import argparse
import asyncio

import sqlalchemy as sa
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    OptimizersConfigDiff,
    PayloadSchemaType,
    VectorParams,
)

from apps.api.config import settings
from apps.api.db.session import AsyncSession
from apps.api.models.tenant import Tenant

EMBED_DIMS = 1536
COLLECTION_PREFIX = "opslens_"


def create_collection_for_tenant(client: QdrantClient, tenant_id: str) -> bool:
    """Create (or verify) a Qdrant collection for a tenant. Returns True if created."""
    collection_name = f"{COLLECTION_PREFIX}{tenant_id}"

    existing = {c.name for c in client.get_collections().collections}
    if collection_name in existing:
        print(f"  [SKIP] Collection '{collection_name}' already exists.")
        return False

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(
            size=EMBED_DIMS,
            distance=Distance.COSINE,
            on_disk=False,      # set True for large tenants (>500k vectors)
        ),
        hnsw_config=HnswConfigDiff(
            m=16,               # number of edges per node in HNSW graph
            ef_construct=100,   # construction time / accuracy tradeoff
            full_scan_threshold=10_000,
        ),
        optimizers_config=OptimizersConfigDiff(
            indexing_threshold=20_000,  # start indexing after 20k vectors
        ),
    )

    # Create payload indices for fast filtered search
    for field, schema_type in [
        ("source_type", PayloadSchemaType.KEYWORD),
        ("tenant_id",   PayloadSchemaType.KEYWORD),
        ("document_id", PayloadSchemaType.KEYWORD),
        ("author",      PayloadSchemaType.KEYWORD),
        ("created_at",  PayloadSchemaType.DATETIME),
    ]:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field,
            field_schema=schema_type,
        )

    print(f"  [OK]   Created collection '{collection_name}' with {EMBED_DIMS}-dim vectors.")
    return True


async def init_all_tenants():
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)

    async with AsyncSession() as db:
        result = await db.execute(sa.select(Tenant.id))
        tenant_ids = [str(row[0]) for row in result.fetchall()]

    print(f"Initialising Qdrant collections for {len(tenant_ids)} tenant(s)...")
    for tid in tenant_ids:
        create_collection_for_tenant(client, tid)
    print("Done.")


async def init_single_tenant(tenant_id: str):
    client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
    create_collection_for_tenant(client, tenant_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialise Qdrant collections for OpsLens tenants")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenant-id", help="Single tenant UUID")
    group.add_argument("--all-tenants", action="store_true")
    args = parser.parse_args()

    if args.all_tenants:
        asyncio.run(init_all_tenants())
    else:
        asyncio.run(init_single_tenant(args.tenant_id))
