# MemFlux Hermes Plugin
FROM hermes/hermes-agent:latest

# Copy plugin into Hermes plugins directory
COPY memflux_plugin/ /app/plugins/memflux/

# Set environment variables
ENV GRAPHCORE_BASE_URL=https://memflux.org
# ENV GRAPHCORE_API_KEY=gc_sk_...  # Set this at runtime
