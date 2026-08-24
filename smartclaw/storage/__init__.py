"""Storage module for persistent data"""

from smartclaw.storage.storage import Storage

# Vector storage functions (for memory system)
from smartclaw.storage.vector import (
    ensure_vector_tables,
    vector_search,
    fts_search,
    insert_chunks,
    get_embedding_from_cache,
    put_embedding_to_cache,
    cosine_similarity,
    bm25_rank_to_score,
    build_fts_query,
)

__all__ = [
    "Storage",
    # Vector storage
    "ensure_vector_tables",
    "vector_search",
    "fts_search",
    "insert_chunks",
    "get_embedding_from_cache",
    "put_embedding_to_cache",
    "cosine_similarity",
    "bm25_rank_to_score",
    "build_fts_query",
]
