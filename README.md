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

### 1. Get a MemFlux API key

Sign up at [memflux.org](https://memflux.org) and create an API key (`gc_sk_...`).

### 2. Install the plugin

```bash
# Copy to your Hermes plugins directory
cp -r memflux_plugin/ ~/.hermes/plugins/memflux/

# Or with the Hermes CLI
hermes plugins install memflux
```

### 3. Configure

Add to your `~/.hermes/.env`:

```env
GRAPHCORE_API_KEY=gc_sk_your_key_here
GRAPHCORE_BASE_URL=https://memflux.org
```

Or set in `config.yaml`:

```yaml
memory:
  provider: graphcore

graphcore:
  api_key: gc_sk_your_key_here
  base_url: https://memflux.org
```

### 4. Activate

```bash
hermes config set memory.provider graphcore
```

Takes effect on next session (`/reset` or new chat).

## Configuration

| Env Var | Description | Default |
|---------|-------------|---------|
| `GRAPHCORE_API_KEY` | MemFlux API key (gc_sk_...) | Required |
| `GRAPHCORE_BASE_URL` | MemFlux API endpoint | `https://memflux.org` |

## Self-Hosting

MemFlux is a hosted service at [memflux.org](https://memflux.org). Enterprise plan includes local deployment options.

## License

MIT
