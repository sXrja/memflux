# MemFlux — Hermes Agent Memory Provider Plugin

Graph-based memory for LLM agents. Connects your Hermes Agent to a MemFlux instance for intelligent knowledge graph storage and retrieval.

## What it does

- **Auto-syncs** conversation turns into a knowledge graph (entities, relations, keywords)
- **Prefetches** relevant graph context before each LLM call
- **Mirrors** built-in Hermes memory writes to the graph
- **Extracts** key facts before context compression discards them

## Tools exposed

| Tool | Description |
|------|-------------|
| `graphcore_search` | Search the knowledge graph for entities/relations |
| `graphcore_remember` | Persist text to the graph (auto-extracted into entities/relations) |
| `graphcore_forget` | Delete a node by ID |
| `graphcore_context` | Generate compressed context block for a query |

## Setup

### 1. Install the plugin

```bash
hermes plugins install sXrja/memflux --enable
```

### 2. Get a MemFlux API key

Sign up at [memflux.org](https://memflux.org), log in, and create an API key (`gc_sk_...`).

### 3. Add your API key

```bash
# Option A: Hermes auth (recommended — stored securely)
hermes auth add memflux --type api-key --api-key gc_sk_your_key_here

# Option B: Environment variable in .env
echo "MEMFLUX_API_KEY=gc_sk_your_key_here" >> ~/.hermes/.env
```

### 4. Activate as memory provider

```bash
hermes config set memory.provider memflux
```

Takes effect on next session (`/reset` or new chat).

## Configuration

| Env Var | Description | Default |
|---------|-------------|---------|
| `MEMFLUX_API_KEY` | MemFlux API key (gc_sk_...) | Required |
| `MEMFLUX_BASE_URL` | MemFlux API endpoint | `https://memflux.org` |

> **Backward compat**: `GRAPHCORE_API_KEY` and `GRAPHCORE_BASE_URL` still work as fallbacks.

## Self-Hosting

MemFlux is a hosted service at [memflux.org](https://memflux.org). Enterprise plan includes local deployment options.

## License

MIT
