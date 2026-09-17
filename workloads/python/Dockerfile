FROM python:3.12-slim

# Create non-root user
RUN useradd -m -s /bin/bash runner

# Working directory for workload scripts
WORKDIR /workspace

# Drop to non-root user
USER runner

# Default command: run the mounted script
CMD ["python", "/workspace/script.py"]
