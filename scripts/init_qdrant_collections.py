import argparse
import os

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    HnswConfigDiff,
    OptimizersConfigDiff,
    PayloadSchemaType,
    VectorParams,
)

EMBED_DIMS        = 1536
COLLECTION_PREFIX = "opslens_"
QDRANT_URL        = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY", None)


def get_client():
    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)


def create_collection_for_tenant(client, tenant_id):
    collection_name = f"{COLLECTION_PREFIX}{tenant_id}"
    existing = {c.name for c in client.get_collections().collections}
    if collection_name in existing:
        print(f"  [SKIP] '{collection_name}' already exists.")
        return False

    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=EMBED_DIMS, distance=Distance.COSINE),
        hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
        optimizers_config=OptimizersConfigDiff(indexing_threshold=20_000),
    )
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
    print(f"  [OK] Created '{collection_name}' ({EMBED_DIMS}-dim cosine).")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenant-id")
    group.add_argument("--all-tenants", action="store_true")
    args = parser.parse_args()

    client = get_client()
    if args.tenant_id:
        create_collection_for_tenant(client, args.tenant_id)
    else:
        import psycopg2
        db_url = os.getenv("DATABASE_URL", "postgresql://opslens:opslens_dev@localhost:5432/opslens")
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = psycopg2.connect(db_url)
        cur  = conn.cursor()
        cur.execute("SELECT id FROM opslens.tenants")
        ids  = [str(r[0]) for r in cur.fetchall()]
        conn.close()
        print(f"Found {len(ids)} tenant(s)...")
        for tid in ids:
            create_collection_for_tenant(client, tid)
        print("Done.")
