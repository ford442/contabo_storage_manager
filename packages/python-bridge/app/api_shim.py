"""Shim to integrate full storage_manager app with python-bridge main."""

# Import all from the full app
import sys
import os

# The full app is designed to run standalone, so we'll re-export its router
# by extracting the routes from it

# First, let's just use the simplified version for now since the full app 
# has too many GCS dependencies that may not be configured

from fastapi import APIRouter
api_router = APIRouter(prefix="/api", tags=["api"])

# Import the simplified handlers
from .api_simple import *
