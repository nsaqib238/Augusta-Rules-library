#!/bin/bash
# Startup script for Render deployment
# This ensures PORT environment variable is properly used

set -e  # Exit on error

# Get port from environment variable, default to 10000 (Render's default)
PORT=${PORT:-10000}

echo "Starting FastAPI application on port $PORT..."
echo "Current directory: $(pwd)"
echo "Python version: $(python --version)"

# Start uvicorn with explicit port binding
exec uvicorn main:app --host 0.0.0.0 --port $PORT --log-level info

