"""
Railway entry point.
Exposes FastAPI app for uvicorn.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the FastAPI app for Railway's uvicorn
from src.main import app

# For local running
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
