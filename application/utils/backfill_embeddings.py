"""One-off maintenance script: computes and stores semantic embeddings for any product
that doesn't have one yet - needed for the catalog bulk-loaded directly into Postgres
(bypassing the API's upsert endpoint, which computes embeddings automatically for new
writes). Safe to re-run: only touches rows where embedding IS NULL.

Usage: python backfill_embeddings.py <product_type> [client_id]
"""
import sys

from db import SessionLocal, ProductModel, DEMO_CLIENT_ID
from modelData import compute_embedding


def backfill(product_type: str, client_id: int = DEMO_CLIENT_ID) -> int:
    with SessionLocal() as session:
        rows = (
            session.query(ProductModel)
            .filter_by(client_id=client_id, product_type=product_type)
            .filter(ProductModel.embedding.is_(None))
            .all()
        )
        print(f"{len(rows)} products missing an embedding for client_id={client_id}, product_type={product_type}")

        for i, row in enumerate(rows, start=1):
            text = " ".join(filter(None, [row.title, row.description, row.genre_1]))
            row.embedding = compute_embedding(text)
            if i % 50 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)} embedded")

        session.commit()
        return len(rows)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python backfill_embeddings.py <product_type> [client_id]")
        sys.exit(1)
    product_type_arg = sys.argv[1]
    client_id_arg = int(sys.argv[2]) if len(sys.argv) > 2 else DEMO_CLIENT_ID
    backfill(product_type_arg, client_id_arg)
