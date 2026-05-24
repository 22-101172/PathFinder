"""
neo4j_client.py
Manages the connection to Neo4j using credentials from .env.
"""

import logging
import os
import time

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Thin wrapper around the official Neo4j Python driver."""

    def __init__(self):
        self._uri      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
        self._user     = os.getenv("NEO4J_USER",     "neo4j")
        self._password = os.getenv("NEO4J_PASSWORD")
        if not self._password:
            raise EnvironmentError(
                "NEO4J_PASSWORD is not set. Copy .env.example to .env and fill in your Neo4j password."
            )
        self._database = os.getenv("NEO4J_DATABASE",  "neo4j")
        self._driver   = None

    def connect(self):
        """Open the driver connection with retry logic (idempotent)."""
        if self._driver is not None:
            return self

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "Neo4jClient: connecting to %s (attempt %d/%d)",
                self._uri, attempt, max_attempts
            )
            try:
                driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
                driver.verify_connectivity()
                self._driver = driver
                logger.info("Neo4jClient: connected to %s successfully.", self._uri)
                return self
            except Exception as exc:
                logger.warning(
                    "Neo4jClient: connection attempt %d/%d failed: %s",
                    attempt, max_attempts, exc
                )
                if attempt < max_attempts:
                    time.sleep(2)

        logger.error("Neo4jClient: all 3 connection attempts to %s failed.", self._uri)
        raise RuntimeError(
            f"Cannot connect to Neo4j at {self._uri} after 3 attempts. "
            "Make sure Neo4j is running."
        )

    def close(self):
        if self._driver is not None:
            self._driver.close()
            self._driver = None
        logger.info("Neo4jClient: connection closed")

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def execute_query(self, query: str, params: dict = None) -> list:
        if self._driver is None:
            raise RuntimeError("Neo4jClient is not connected. Call connect() first.")
        params = params or {}
        with self._driver.session(database=self._database) as session:
            result = session.run(query, parameters=params)
            return [dict(record) for record in result]
