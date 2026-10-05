import copy
import threading
import time

from collections.abc import Callable
from typing import Any, TypeVar

from ontology.concept_catalog import (
    BusinessConceptItem,
    ConceptCatalogService,
)

from ontology.models import (
    ColumnMetadata,
    PhysicalPath,
    SemanticRelationship,
    TableMetadata,
)

from ontology.ontology_service import (
    OntologyService,
)


T = TypeVar("T")


class TTLCache:
    """
    Small thread-safe time-based cache for ontology reads.

    The ontology changes rarely, but every request performs
    many Neo4j lookups against it. Entries expire after
    ttl_seconds so ontology edits are picked up without a
    restart; clear() forces an immediate refresh.

    Results (including None) are deep-copied on the way out
    so callers can never mutate a cached object.
    Exceptions are not cached.
    """

    def __init__(
        self,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:

        if ttl_seconds <= 0:
            raise ValueError(
                "ttl_seconds must be greater than zero."
            )

        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_load(
        self,
        key: Any,
        loader: Callable[[], T],
        should_cache: Callable[[T], bool] | None = None,
    ) -> T:

        now = self._clock()

        with self._lock:
            entry = self._entries.get(key)

        if entry is not None and entry[0] > now:
            return copy.deepcopy(entry[1])

        # Loaded outside the lock: concurrent misses may both
        # query Neo4j, which is harmless for read-only data.
        value = loader()

        if should_cache is None or should_cache(value):

            with self._lock:
                self._entries[key] = (now + self._ttl, value)

            return copy.deepcopy(value)

        return value

    def clear(self) -> None:

        with self._lock:
            self._entries.clear()


class CachedOntologyService(OntologyService):
    """
    OntologyService whose read-only lookups go through a
    shared TTLCache.

    Behaviour is identical to OntologyService apart from
    reuse of recent results.
    """

    def __init__(
        self,
        neo4j_client,
        cache: TTLCache,
    ) -> None:

        super().__init__(neo4j_client)

        self._cache = cache

    def resolve_table(
        self,
        concept: str,
    ) -> TableMetadata | None:

        return self._cache.get_or_load(
            ("resolve_table", concept),
            lambda: super(CachedOntologyService, self)
            .resolve_table(concept),
        )

    def find_physical_path(
        self,
        start_table_key: str,
        end_table_key: str,
        max_hops: int = 8,
    ) -> PhysicalPath | None:

        return self._cache.get_or_load(
            (
                "find_physical_path",
                start_table_key,
                end_table_key,
                max_hops,
            ),
            lambda: super(CachedOntologyService, self)
            .find_physical_path(
                start_table_key=start_table_key,
                end_table_key=end_table_key,
                max_hops=max_hops,
            ),
        )

    def find_physical_paths(
        self,
        start_table_key: str,
        end_table_key: str,
        max_hops: int = 8,
        max_paths: int = 50,
    ) -> list[PhysicalPath]:

        return self._cache.get_or_load(
            (
                "find_physical_paths",
                start_table_key,
                end_table_key,
                max_hops,
                max_paths,
            ),
            lambda: super(CachedOntologyService, self)
            .find_physical_paths(
                start_table_key=start_table_key,
                end_table_key=end_table_key,
                max_hops=max_hops,
                max_paths=max_paths,
            ),
        )

    def get_table_columns(
        self,
        table_key: str,
    ) -> list[ColumnMetadata]:

        return self._cache.get_or_load(
            ("get_table_columns", table_key),
            lambda: super(CachedOntologyService, self)
            .get_table_columns(table_key),
        )

    def find_semantic_relationship(
        self,
        source_concept: str,
        target_concept: str,
    ) -> SemanticRelationship | None:

        return self._cache.get_or_load(
            (
                "find_semantic_relationship",
                source_concept,
                target_concept,
            ),
            lambda: super(CachedOntologyService, self)
            .find_semantic_relationship(
                source_concept=source_concept,
                target_concept=target_concept,
            ),
        )

    def find_semantic_relationship_between_tables(
        self,
        first_table_key: str,
        second_table_key: str,
    ) -> SemanticRelationship | None:

        return self._cache.get_or_load(
            (
                "find_semantic_relationship_between_tables",
                first_table_key,
                second_table_key,
            ),
            lambda: super(CachedOntologyService, self)
            .find_semantic_relationship_between_tables(
                first_table_key=first_table_key,
                second_table_key=second_table_key,
            ),
        )


class CachedConceptCatalogService(ConceptCatalogService):
    """
    ConceptCatalogService whose catalog is shared through a
    TTLCache. QuestionAnalyzer and SemanticQueryNormalizer
    both load the catalog on every request.
    """

    def __init__(
        self,
        neo4j_client,
        cache: TTLCache,
    ) -> None:

        super().__init__(neo4j_client)

        self._cache = cache

    def list_business_concepts(
        self,
    ) -> list[BusinessConceptItem]:

        return self._cache.get_or_load(
            ("list_business_concepts",),
            lambda: super(CachedConceptCatalogService, self)
            .list_business_concepts(),
            # An empty catalog means Neo4j is not ready;
            # do not pin that failure for the whole TTL.
            should_cache=bool,
        )
