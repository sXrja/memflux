"""MemFlux memory plugin — Hermes MemoryProvider interface.

Self-hosted graph-based memory for LLMs. Uses the MemFlux API
(memflux.org) to store and retrieve facts, entities,
and relationships as a knowledge graph.

Features:
- sync_turn: automatically extracts entities/relations from each turn
- prefetch: retrieves relevant graph context before each LLM call
- Explicit tools: graphcore_search, graphcore_remember, graphcore_forget
- Mirrors built-in memory writes to the graph
- API-key auth (shared or per-user key)

Config (env vars or hermes config.yaml under graphcore:):
  MEMFLUX_API_KEY   — API key (required)
  MEMFLUX_BASE_URL  — API endpoint (default: https://memflux.org)
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://memflux.org"
_ASYNC_SHUTDOWN = object()


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "graphcore_search",
    "description": (
        "Search the MemFlux knowledge graph for entities and relations "
        "matching a query. Returns ranked nodes with their connections."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in the knowledge graph.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 10, max: 50).",
            },
        },
        "required": ["query"],
    },
}

REMEMBER_SCHEMA = {
    "name": "graphcore_remember",
    "description": (
        "Persist a fact, relationship, or piece of knowledge to the "
        "MemFlux knowledge graph. Text is auto-extracted into "
        "entities and relations."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The text to ingest into the graph.",
            },
            "source": {
                "type": "string",
                "description": "Optional source identifier (e.g. 'conversation', 'manual').",
            },
        },
        "required": ["content"],
    },
}

FORGET_SCHEMA = {
    "name": "graphcore_forget",
    "description": "Delete a node from the MemFlux knowledge graph by ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "node_id": {
                "type": "string",
                "description": "The node ID to delete.",
            },
        },
        "required": ["node_id"],
    },
}

CONTEXT_SCHEMA = {
    "name": "graphcore_context",
    "description": (
        "Generate a compressed context block from the MemFlux knowledge graph "
        "relevant to a query. Useful for injecting graph context into "
        "prompts or understanding what the graph knows about a topic."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The topic or question to generate context for.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Max context tokens (default: 500).",
            },
        },
        "required": ["query"],
    },
}


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _api_call(method: str, path: str, base_url: str, api_key: str,
              json_data: dict = None, timeout: float = 5.0) -> Optional[dict]:
    """Make a synchronous HTTP request to the GraphCore API."""
    import urllib.request
    import urllib.error

    url = f"{base_url}{path}"
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    body = json.dumps(json_data).encode() if json_data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        logger.debug("GraphCore API %s %s -> %d: %s", method, path, e.code, body_text)
        return None
    except Exception as e:
        logger.debug("GraphCore API %s %s error: %s", method, path, e)
        return None


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------

class GraphCoreMemoryProvider(MemoryProvider):
    """MemFlux-backed memory provider for Hermes Agent."""

    def __init__(self):
        self._base_url: str = ""
        self._api_key: str = ""
        self._session_id: str = ""
        self._user_id: str = ""
        self._platform: str = ""
        self._available: bool = False
        self._write_queue: queue.Queue = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None
        self._prefetch_cache: Dict[str, str] = {}
        self._turn_counter: int = 0

    # -- Identity ------------------------------------------------------------

    @property
    def name(self) -> str:
        return "memflux"

    # -- Availability --------------------------------------------------------

    def is_available(self) -> bool:
        """Check if GraphCore API is reachable and has an API key."""
        api_key = os.getenv("MEMFLUX_API_KEY", os.getenv("GRAPHCORE_API_KEY", ""))
        base_url = os.getenv("MEMFLUX_BASE_URL", os.getenv("GRAPHCORE_BASE_URL", _DEFAULT_BASE_URL))
        if not api_key:
            return False
        result = _api_call("GET", "/health", base_url, api_key, timeout=2.0)
        return result is not None and result.get("status") == "ok"

    # -- Core lifecycle ------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        self._base_url = os.getenv("MEMFLUX_BASE_URL", os.getenv("GRAPHCORE_BASE_URL", _DEFAULT_BASE_URL))
        self._api_key = os.getenv("MEMFLUX_API_KEY", os.getenv("GRAPHCORE_API_KEY", ""))
        self._session_id = session_id
        self._user_id = kwargs.get("user_id", "hermes")
        self._platform = kwargs.get("platform", "cli")
        self._turn_counter = 0

        if not self._api_key:
            logger.warning("MemFlux: no MEMFLUX_API_KEY set — provider inactive")
            return

        # Start background writer thread
        self._writer_thread = threading.Thread(
            target=self._write_loop, daemon=True, name="graphcore-writer"
        )
        self._writer_thread.start()

        logger.info("GraphCore memory provider initialized (session=%s)", session_id)

    def system_prompt_block(self) -> str:
        return (
            "MemFlux graph memory is active. Use graphcore_search to query "
            "the knowledge graph, graphcore_remember to store new facts, "
            "graphcore_context for compressed graph context, and "
            "graphcore_forget to remove nodes. Facts from your conversations "
            "are automatically extracted into the graph."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Retrieve relevant graph context for the upcoming turn."""
        cached = self._prefetch_cache.get(query, "")
        if cached:
            return cached

        if not self._api_key:
            return ""

        result = _api_call(
            "POST", "/v1/context/generate", self._base_url, self._api_key,
            json_data={
                "query": query,
                "max_tokens": 500,
                "max_nodes": 20,
            },
            timeout=5.0,
        )

        if result and result.get("context"):
            ctx = result["context"]
            nodes_used = result.get("nodes_used", 0)
            if nodes_used > 0:
                self._prefetch_cache[query] = ctx
                return ctx

        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Pre-cache context for next turn."""
        self._prefetch_cache.pop(query, None)  # invalidate stale cache

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Queue turn content for background graph ingestion."""
        if not self._api_key:
            return

        self._turn_counter += 1

        # Ingest the combined turn as a graph node
        combined = f"User: {user_content}\nAssistant: {assistant_content}"
        self._write_queue.put({
            "type": "add",
            "text": combined,
            "source": f"hermes:{self._platform}:{self._session_id}:turn{self._turn_counter}",
        })

    # -- Tools ---------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, REMEMBER_SCHEMA, FORGET_SCHEMA, CONTEXT_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "graphcore_search":
            return self._tool_search(args.get("query", ""), args.get("limit", 10))
        elif tool_name == "graphcore_remember":
            return self._tool_remember(args.get("content", ""), args.get("source", "manual"))
        elif tool_name == "graphcore_forget":
            return self._tool_forget(args.get("node_id", ""))
        elif tool_name == "graphcore_context":
            return self._tool_context(args.get("query", ""), args.get("max_tokens", 500))
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _tool_search(self, query: str, limit: int = 10) -> str:
        result = _api_call(
            "POST", "/v1/graph/query", self._base_url, self._api_key,
            json_data={"query": query, "limit": limit},
        )
        if result is None:
            return json.dumps({"error": "GraphCore API unavailable"})
        return json.dumps(result)

    def _tool_remember(self, content: str, source: str = "manual") -> str:
        result = _api_call(
            "POST", "/v1/graph/add", self._base_url, self._api_key,
            json_data={"text": content, "source": source},
        )
        if result is None:
            return json.dumps({"error": "GraphCore API unavailable"})
        return json.dumps(result)

    def _tool_forget(self, node_id: str) -> str:
        result = _api_call(
            "DELETE", f"/v1/graph/nodes/{node_id}", self._base_url, self._api_key,
        )
        if result is None:
            return json.dumps({"error": "GraphCore API unavailable"})
        return json.dumps(result)

    def _tool_context(self, query: str, max_tokens: int = 500) -> str:
        result = _api_call(
            "POST", "/v1/context/generate", self._base_url, self._api_key,
            json_data={"query": query, "max_tokens": max_tokens, "max_nodes": 20},
        )
        if result is None:
            return json.dumps({"error": "GraphCore API unavailable"})
        return json.dumps(result)

    # -- Built-in memory mirroring -------------------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror built-in memory writes to GraphCore."""
        if not self._api_key or action != "add":
            return

        self._write_queue.put({
            "type": "add",
            "text": content,
            "source": f"hermes:memory:{target}",
        })

    # -- Session lifecycle --------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Flush write queue on session end."""
        self._write_queue.put(_ASYNC_SHUTDOWN)
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=5.0)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        if reset:
            self._turn_counter = 0
            self._prefetch_cache.clear()
        self._session_id = new_session_id

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Extract key facts before compression discards them."""
        if not self._api_key:
            return ""

        # Ingest the about-to-be-compressed messages to the graph
        combined_parts = []
        for msg in messages[-10:]:  # last 10 messages
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                combined_parts.append(f"{role}: {content[:200]}")

        if combined_parts:
            self._write_queue.put({
                "type": "add",
                "text": "\n".join(combined_parts),
                "source": f"hermes:precompress:{self._session_id}",
            })

        return ""

    def shutdown(self) -> None:
        self._write_queue.put(_ASYNC_SHUTDOWN)
        if self._writer_thread and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=5.0)

    # -- Config schema ------------------------------------------------------

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "api_key",
                "description": "MemFlux API key (gc_sk_...)",
                "secret": True,
                "required": True,
                "env_var": "MEMFLUX_API_KEY",
            },
            {
                "key": "base_url",
                "description": "MemFlux API base URL",
                "required": False,
                "default": _DEFAULT_BASE_URL,
                "env_var": "MEMFLUX_BASE_URL",
            },
        ]

    # -- Background writer ---------------------------------------------------

    def _write_loop(self) -> None:
        """Background thread: drain write queue, batch-send to API."""
        batch = []
        last_flush = time.monotonic()

        while True:
            try:
                item = self._write_queue.get(timeout=2.0)
            except queue.Empty:
                # Timeout — flush any pending batch
                if batch:
                    self._flush_batch(batch)
                    batch = []
                last_flush = time.monotonic()
                continue

            if item is _ASYNC_SHUTDOWN:
                if batch:
                    self._flush_batch(batch)
                return

            batch.append(item)
            if len(batch) >= 5 or (time.monotonic() - last_flush) > 5.0:
                self._flush_batch(batch)
                batch = []
                last_flush = time.monotonic()

    def _flush_batch(self, batch: list) -> None:
        """Send a batch of items to the GraphCore API."""
        for item in batch:
            try:
                _api_call(
                    "POST", "/v1/graph/add", self._base_url, self._api_key,
                    json_data={
                        "text": item["text"],
                        "source": item.get("source", "hermes"),
                    },
                    timeout=5.0,
                )
            except Exception as e:
                logger.debug("GraphCore write error: %s", e)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx):
    """Register the GraphCore memory provider."""
    ctx.register_memory_provider(GraphCoreMemoryProvider())
