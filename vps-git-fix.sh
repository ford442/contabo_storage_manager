#!/bin/bash
# Run this on the VPS to fix git conflicts

echo "Fixing git conflicts on VPS..."
cd /root/contabo_storage_manager

echo "Stashing local changes..."
git stash

echo "Pulling latest..."
git pull origin main

echo "Applying stashed changes (if any)..."
git stash pop || echo "No stashed changes to apply"

echo "Done!"
