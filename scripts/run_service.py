from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "bid_knowledge.service.app:app",
        host=os.getenv("BID_SERVICE_HOST", "0.0.0.0"),
        port=int(os.getenv("BID_SERVICE_PORT", "8000")),
        workers=1,
    )


if __name__ == "__main__":
    main()
