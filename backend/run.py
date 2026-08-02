#!/usr/bin/env python3
"""启动 llama-sycl-studio WebUI"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from app.database import init_db
from app.config import settings

if __name__ == "__main__":
    init_db()
    print(f"⬢ llama-studio WebUI: http://0.0.0.0:{settings.webui_port}")
    print(f"⬢ Router URL: {settings.router_url}")
    uvicorn.run("main:app", host="0.0.0.0", port=settings.webui_port, reload=False)
