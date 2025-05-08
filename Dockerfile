# Stage 1: Base image and dependencies
FROM python:3.9-slim-bookworm AS base

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
# Default port, can be overridden by platform or --env PORT during docker run
ENV PORT 8000
# Set appropriate locale settings to avoid Unicode errors with some CLI tools or logs
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8

# Install system dependencies required for curl (by core downloader) and potentially by proxy cores
# ca-certificates is important for HTTPS calls
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    # Add other system libraries if your proxy cores have specific needs
    # e.g., libcap2-bin for setcap if needed, though cores are usually self-contained
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*


# Create a non-root user and group
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home --home-dir /home/appuser --shell /sbin/nologin appuser


# Create application directory structure and necessary subdirectories for runtime data
# These directories will be owned by appuser
WORKDIR /app

# Create subdirectories for backend persistent/runtime data before setting user,
# so we can chown them appropriately.
# These paths should align with what your backend/app/core/config.py expects
# if it uses absolute paths or paths relative to /app/backend.
# Assuming CORES_DIR and TEMP_DIR in config.py are relative paths like "downloaded_cores"
# and the backend's runtime WORKDIR will be /app/backend.
RUN mkdir -p /app/backend/downloaded_cores && \
    mkdir -p /app/backend/temp_configs && \
    chown -R appuser:appgroup /app/backend/downloaded_cores && \
    chown -R appuser:appgroup /app/backend/temp_configs

# Copy backend requirements first to leverage Docker layer caching
COPY backend/requirements.txt /app/backend/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy backend application code
# Ensure .dockerignore is set up to exclude .venv, __pycache__, etc.
COPY backend/app /app/backend/app

# Copy frontend static files (assuming FastAPI serves them)
COPY frontend /app/frontend
# Ensure the frontend directory and its contents are readable by appuser
RUN chown -R appuser:appgroup /app/frontend

# Switch to the non-root user
USER appuser



ENV PYTHONPATH="/app:${PYTHONPATH}"


# Set the working directory for running the backend application
WORKDIR /app/backend

# Expose the port the app runs on (defined by ENV PORT)
EXPOSE ${PORT}

# Define the command to run the application
# Uvicorn will run from /app/backend, so "app.main:app" refers to /app/backend/app/main.py
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
