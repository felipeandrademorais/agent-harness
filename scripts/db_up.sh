#!/bin/bash
set -e
echo "Starting PostgreSQL container on port 5455..."
docker compose up -d db
echo "Waiting for database to be ready..."
docker compose exec db pg_isready -U harness -d harness
echo "Database is ready."
