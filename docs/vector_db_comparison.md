# Vector DB Comparison

This project tested FAISS and Chroma for similar action-item search.

## Assumption

The target operating pattern is:

- about 200 users
- each user creates or updates action items 2 to 3 times per day
- search latency should be very fast
- operational records stay in PostgreSQL
- vector search can use a separate vector index

The benchmark expands the current action-item set into synthetic records:

- 600 records: about 1 day at 200 users x 3 updates
- 6,000 records: about 10 days at the same rate

## Command

```powershell
.\.venv\Scripts\python.exe experiments\compare_vector_dbs.py --records 600 --pg-dsn postgresql://postgres:postgres@localhost:5432/mobidays_app
.\.venv\Scripts\python.exe experiments\compare_vector_dbs.py --records 6000 --pg-dsn postgresql://postgres:postgres@localhost:5432/mobidays_app --output data/interim/vector_db_comparison_6000.json
```

## Results

| Records | Engine | Build | Avg query | P95 query | Max query |
|---:|---|---:|---:|---:|---:|
| 600 | FAISS | 0.357 ms | 0.060 ms | 0.115 ms | 0.115 ms |
| 600 | Chroma | 898.114 ms | 6.653 ms | 22.810 ms | 22.810 ms |
| 6,000 | FAISS | 3.267 ms | 0.621 ms | 0.718 ms | 0.718 ms |
| 6,000 | Chroma | 5,236.160 ms | 2.499 ms | 6.310 ms | 6.310 ms |

## Interpretation

FAISS is the better fit when the priority is fast search over a local/vector-only
index. It is also simpler to rebuild a small-to-medium index a few times per day.

Chroma is more convenient when the vector store itself should manage documents,
metadata, collections, and filtering. In this project, PostgreSQL already handles
the operational metadata, so Chroma's extra management layer is less important.

## Recommendation

Use:

```text
PostgreSQL = operational DB
FAISS = vector similarity index
```

Runtime flow:

```text
query text -> embedding -> FAISS top-k action_item_id -> PostgreSQL metadata/evidence lookup
```

Chroma remains a good fallback if the project later needs standalone vector
collections with richer metadata filtering and document management inside the
vector database itself.
