import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "zoombots.api:app",
        host=os.environ.get("ZOOMBOTS_HOST", "127.0.0.1"),
        port=int(os.environ.get("ZOOMBOTS_PORT", "8000")),
    )
