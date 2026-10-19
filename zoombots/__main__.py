import os

import uvicorn

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    host = os.environ.get("ZOOMBOTS_HOST", "127.0.0.1")
    if host not in LOOPBACK and not os.environ.get("ZOOMBOTS_TOKEN"):
        raise SystemExit("ZOOMBOTS_HOST expose l'API hors de cette machine : définir ZOOMBOTS_TOKEN (jeton d'accès)")
    uvicorn.run("zoombots.api:app", host=host, port=int(os.environ.get("ZOOMBOTS_PORT", "8000")))


if __name__ == "__main__":
    main()
