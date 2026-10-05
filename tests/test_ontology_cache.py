import pytest

from fastapi.testclient import TestClient

from api.application import create_app

from ontology.cache import (
    CachedConceptCatalogService,
    TTLCache,
)


class FakeClock:

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_hit_miss_and_expiry():

    clock = FakeClock()
    cache = TTLCache(ttl_seconds=10, clock=clock)
    calls = []

    def loader():
        calls.append(1)
        return {"value": len(calls)}

    assert cache.get_or_load("k", loader) == {"value": 1}
    assert cache.get_or_load("k", loader) == {"value": 1}
    assert len(calls) == 1

    clock.now = 10.5

    assert cache.get_or_load("k", loader) == {"value": 2}
    assert len(calls) == 2


def test_cached_values_cannot_be_mutated_by_callers():

    cache = TTLCache(ttl_seconds=60)

    first = cache.get_or_load("k", lambda: [1, 2, 3])
    first.append(99)

    assert cache.get_or_load("k", lambda: []) == [1, 2, 3]


def test_none_is_cached_and_exceptions_are_not():

    cache = TTLCache(ttl_seconds=60)
    calls = []

    def none_loader():
        calls.append(1)
        return None

    assert cache.get_or_load("none", none_loader) is None
    assert cache.get_or_load("none", none_loader) is None
    assert len(calls) == 1

    def failing():
        raise ConnectionError("neo4j down")

    with pytest.raises(ConnectionError):
        cache.get_or_load("err", failing)

    assert cache.get_or_load("err", lambda: "ok") == "ok"


def test_clear_forces_reload():

    cache = TTLCache(ttl_seconds=60)

    cache.get_or_load("k", lambda: "old")
    cache.clear()

    assert cache.get_or_load("k", lambda: "new") == "new"


def test_empty_catalog_is_not_cached():

    class FakeNeo4j:

        def __init__(self) -> None:
            self.responses = [[], [{"concept": "Invoice"}]]

        def execute_read(self, query, parameters=None):
            return self.responses.pop(0)

    service = CachedConceptCatalogService(
        FakeNeo4j(),
        TTLCache(ttl_seconds=60),
    )

    assert service.list_business_concepts() == []
    assert [
        item.concept
        for item in service.list_business_concepts()
    ] == ["Invoice"]


def test_clear_endpoint():

    cleared = []

    class Agent:
        def ask(self, question):
            raise AssertionError("not called")

    client = TestClient(
        create_app(
            Agent(),
            api_key="key",
            clear_ontology_cache=lambda: cleared.append(1),
        )
    )

    assert client.post("/admin/ontology-cache/clear").status_code == 401

    response = client.post(
        "/admin/ontology-cache/clear",
        headers={"X-API-Key": "key"},
    )

    assert response.status_code == 200
    assert cleared == [1]
