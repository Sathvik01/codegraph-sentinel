"""Sole access point to FalkorDB: one gateway per (host, port) per process.

The plan's singleton-per-path guard adapts to a networked server: a single
gateway object owns the connection, so tests and the kill-switch harness
cannot open two conflicting handles on the same endpoint inside one process.
"""

from __future__ import annotations

from typing import Any

from falkordb import FalkorDB
from redis.exceptions import RedisError, ResponseError

from codegraph_sentinel import config


class GraphUnavailableError(Exception):
    """Any connect/ping/transport failure. The kill-switch path keys on this."""


class GraphQueryError(Exception):
    """Cypher semantics errors — the server is up, the query is wrong."""


class GraphGateway:
    """Singleton-per-endpoint wrapper around the falkordb client."""

    _instances: dict[tuple[str, int], "GraphGateway"] = {}

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._closed = False
        try:
            self._db = FalkorDB(host=host, port=port)
        except Exception as exc:  # unreachable server at construction
            raise GraphUnavailableError(f"falkordb connect failed: {exc}") from exc

    # --- lifecycle -----------------------------------------------------

    @classmethod
    def open(cls, host: str | None = None, port: int | None = None) -> "GraphGateway":
        """Open the gateway for an endpoint. Second open on the same
        endpoint within a process raises RuntimeError (the singleton guard)."""
        host = host if host is not None else config.FALKOR_HOST
        port = port if port is not None else config.FALKOR_PORT
        key = (host, port)
        if key in cls._instances:
            raise RuntimeError(
                f"GraphGateway already open for {host}:{port} in this process"
            )
        gw = cls(host, port)
        cls._instances[key] = gw
        return gw

    def close(self) -> None:
        """Release the handle and free the endpoint. Does NOT touch server data."""
        key = (self.host, self.port)
        if GraphGateway._instances.get(key) is self:
            del GraphGateway._instances[key]
        self._closed = True
        try:
            del self._db
        except AttributeError:
            pass

    # --- handles -------------------------------------------------------

    def graph(self, name: str | None = None):
        """The select_graph handle (used by ingest.py and tests)."""
        if self._closed:
            raise GraphUnavailableError("gateway is closed")
        return self._db.select_graph(name or config.GRAPH_NAME)

    def query(
        self, cypher: str, params: dict[str, Any] | None = None, read_only: bool = False
    ):
        """Execute pinned Cypher. Never leaks a bare transport error:
        ResponseError -> GraphQueryError, everything else -> GraphUnavailableError."""
        if self._closed:
            raise GraphUnavailableError("gateway is closed")
        g = self.graph()
        try:
            if read_only:
                return g.ro_query(cypher, params=params or {})
            return g.query(cypher, params=params or {})
        except ResponseError as exc:
            raise GraphQueryError(str(exc)) from exc
        except (RedisError, OSError, ConnectionError) as exc:
            raise GraphUnavailableError(str(exc)) from exc
        except Exception as exc:
            raise GraphUnavailableError(str(exc)) from exc

    # --- liveness ------------------------------------------------------

    def ping(self) -> bool:
        """True on success; raises GraphUnavailableError on every failure."""
        try:
            self.query("RETURN 1", read_only=True)
        except GraphUnavailableError:
            raise
        except GraphQueryError as exc:  # pragma: no cover - server-side oddity
            raise GraphUnavailableError(str(exc)) from exc
        return True