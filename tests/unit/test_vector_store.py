"""Unit tests for QdrantVectorStore using in-memory mode."""

import pytest

from app.core.exceptions import VectorStoreError
from app.integrations.vector_store.base import VectorPoint
from app.integrations.vector_store.qdrant_store import QdrantVectorStore


@pytest.fixture
def memory_vector_store():
    """Create isolated in-memory QdrantVectorStore with 4-dimension vectors."""
    store = QdrantVectorStore(
        mode="memory",
        collection_name="test_knowledge",
        vector_size=4,
    )
    return store


@pytest.mark.asyncio
async def test_qdrant_collection_creation_and_health(memory_vector_store) -> None:
    """Verify collection is created automatically and health_check passes."""
    assert await memory_vector_store.health_check() is False
    await memory_vector_store.initialize()
    assert await memory_vector_store.health_check() is True
    await memory_vector_store.close()


@pytest.mark.asyncio
async def test_qdrant_upsert_and_semantic_retrieval(memory_vector_store) -> None:
    """Verify vector upsert and semantic cosine search with score ranking."""
    points = [
        VectorPoint(
            id="chk-1",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"title": "Leave Policy", "text": "Annual leave rules", "access_level": "employee"},
        ),
        VectorPoint(
            id="chk-2",
            vector=[0.0, 1.0, 0.0, 0.0],
            payload={"title": "IT Security", "text": "Password standards", "access_level": "employee"},
        ),
        VectorPoint(
            id="chk-3",
            vector=[0.707, 0.707, 0.0, 0.0],
            payload={"title": "Combined Guidelines", "text": "General notice", "access_level": "employee"},
        ),
    ]

    await memory_vector_store.upsert(points)

    # Query closest to [1.0, 0.0, 0.0, 0.0]
    results = await memory_vector_store.search(
        query_vector=[1.0, 0.0, 0.0, 0.0],
        top_k=2,
    )

    assert len(results) == 2
    # Top match is chk-1 with perfect similarity ~1.0
    assert results[0].payload["title"] == "Leave Policy"
    assert results[0].score >= 0.99
    # Second match is chk-3 (~0.707)
    assert results[1].payload["title"] == "Combined Guidelines"
    assert results[1].score > 0.7

    await memory_vector_store.close()


@pytest.mark.asyncio
async def test_qdrant_access_level_and_metadata_filtering(memory_vector_store) -> None:
    """Verify search filter enforces role access levels, category, and department isolation."""
    points = [
        VectorPoint(
            id="doc-pub",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"title": "Public Holidays", "access_level": "public", "category": "holiday"},
        ),
        VectorPoint(
            id="doc-emp",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"title": "Leave Policy", "access_level": "employee", "category": "leave"},
        ),
        VectorPoint(
            id="doc-hr",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"title": "Internal HR Guidelines", "access_level": "hr", "category": "hr_internal"},
        ),
        VectorPoint(
            id="doc-admin",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"title": "Executive Compensation", "access_level": "admin", "category": "finance"},
        ),
        VectorPoint(
            id="doc-eng",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={
                "title": "Engineering Runbook",
                "access_level": "employee",
                "department": "Engineering",
                "category": "engineering",
            },
        ),
    ]
    await memory_vector_store.upsert(points)

    # 1. Standard employee access filter: public + employee
    res_emp = await memory_vector_store.search(
        query_vector=[1.0, 0.0, 0.0, 0.0],
        top_k=10,
        allowed_access_levels=["public", "employee"],
    )
    emp_levels = {r.payload["access_level"] for r in res_emp}
    assert "hr" not in emp_levels
    assert "admin" not in emp_levels
    assert "public" in emp_levels
    assert "employee" in emp_levels

    # 2. HR access filter: public + employee + hr
    res_hr = await memory_vector_store.search(
        query_vector=[1.0, 0.0, 0.0, 0.0],
        top_k=10,
        allowed_access_levels=["public", "employee", "hr"],
    )
    hr_levels = {r.payload["access_level"] for r in res_hr}
    assert "hr" in hr_levels
    assert "admin" not in hr_levels

    # 3. Department filter: Engineering department user sees generic + Engineering
    res_dept = await memory_vector_store.search(
        query_vector=[1.0, 0.0, 0.0, 0.0],
        top_k=10,
        allowed_access_levels=["public", "employee"],
        department="Engineering",
    )
    dept_titles = {r.payload["title"] for r in res_dept}
    assert "Engineering Runbook" in dept_titles
    assert "Leave Policy" in dept_titles

    await memory_vector_store.close()


@pytest.mark.asyncio
async def test_qdrant_delete_by_document_id(memory_vector_store) -> None:
    """Verify points belonging to a specific document ID are deleted cleanly."""
    points = [
        VectorPoint(
            id="doc-1-chk-1",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"document_id": "doc-alpha", "title": "Doc Alpha Chunk 1"},
        ),
        VectorPoint(
            id="doc-1-chk-2",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"document_id": "doc-alpha", "title": "Doc Alpha Chunk 2"},
        ),
        VectorPoint(
            id="doc-2-chk-1",
            vector=[1.0, 0.0, 0.0, 0.0],
            payload={"document_id": "doc-beta", "title": "Doc Beta Chunk 1"},
        ),
    ]
    await memory_vector_store.upsert(points)

    # Delete doc-alpha
    await memory_vector_store.delete_by_document_id("doc-alpha")

    # Search all
    results = await memory_vector_store.search(
        query_vector=[1.0, 0.0, 0.0, 0.0],
        top_k=10,
    )

    doc_ids = {r.payload["document_id"] for r in results}
    assert "doc-alpha" not in doc_ids
    assert "doc-beta" in doc_ids

    await memory_vector_store.close()


@pytest.mark.asyncio
async def test_qdrant_invalid_vector_dimension_rejection(memory_vector_store) -> None:
    """Verify QdrantVectorStore rejects vectors with wrong dimensionality."""
    # Store initialized with size=4, pass size=2
    bad_point = VectorPoint(id="chk-bad", vector=[1.0, 0.0], payload={})

    with pytest.raises(VectorStoreError) as exc_info:
        await memory_vector_store.upsert([bad_point])
    assert "Vector dimension mismatch" in str(exc_info.value)

    with pytest.raises(VectorStoreError):
        await memory_vector_store.search(query_vector=[1.0, 0.0])

    await memory_vector_store.close()


@pytest.mark.asyncio
async def test_qdrant_empty_collection_search(memory_vector_store) -> None:
    """Verify searching an empty collection returns empty list without error."""
    await memory_vector_store.initialize()
    results = await memory_vector_store.search(query_vector=[1.0, 0.0, 0.0, 0.0])
    assert results == []
    await memory_vector_store.close()
