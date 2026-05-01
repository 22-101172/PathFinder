"""
wrappers/neo4j_client.py
Neo4j client for the gateway.

Copied from PathFinder KG-Engine/src/neo4j_client.py.
Reads connection credentials from environment variables (set by Docker Compose
or the local shell) — no .env file loading; callers must set them externally.

  NEO4J_URI       (default: bolt://localhost:7687)
  NEO4J_USER      (default: neo4j)
  NEO4J_PASSWORD  (required — set via .env or environment; no default to avoid leaking credentials)
"""

import os

from neo4j import GraphDatabase


class Neo4jClient:
    """Thin wrapper around the official Neo4j Python driver."""

    def __init__(self):
        self._uri      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
        self._user     = os.getenv("NEO4J_USER",     "neo4j")
        self._password = os.getenv("NEO4J_PASSWORD", "")
        self._driver   = None

    # ── Connection management ────────────────────────────────

    def connect(self):
        """Open the driver connection (idempotent)."""
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
            )
        return self

    def close(self):
        """Close the driver connection."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    # ── Context manager support ──────────────────────────────

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ── Query execution ──────────────────────────────────────

    def execute_query(self, query: str, params: dict = None) -> list[dict]:
        """
        Run a Cypher query and return results as a list of plain dicts.

        Parameters
        ----------
        query  : Cypher query string
        params : dict of query parameters (optional)

        Returns
        -------
        list of dicts — one per result row, keys are column names.
        """
        if self._driver is None:
            raise RuntimeError("Neo4jClient is not connected. Call connect() first.")

        params = params or {}
        with self._driver.session() as session:
            result = session.run(query, parameters=params)
            return [dict(record) for record in result]
