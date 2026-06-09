# MemFlux Hermes Plugin
FROM hermes/hermes-agent:latest

# Copy plugin into Hermes plugins directory
COPY memflux/ /app/plugins/memflux/

# Default endpoint
ENV MEMFLUX_BASE_URL=https://memflux.org
# ENV MEMFLUX_API_KEY=***  # Set this at runtime
