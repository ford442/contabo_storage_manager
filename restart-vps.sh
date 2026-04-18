#!/bin/bash
# Run this on the VPS to update and restart the Python bridge

set -e

echo "Updating contabo_storage_manager..."
cd /root/contabo_storage_manager

echo "Pulling latest code..."
git pull origin main

echo "Rebuilding and restarting python-bridge container..."
docker compose --profile python up -d --build python-bridge

echo "Checking health..."
sleep 3
curl -s http://localhost:8000/health

echo ""
echo "Deploy complete at $(date)"
