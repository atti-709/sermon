"""FastAPI app factory and the `sermon web` server entry point."""

import socket
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DIST = REPO_ROOT / "web" / "dist"

DEFAULT_PORT = 8756


def create_app() -> FastAPI:
    from .api import media_router, router

    app = FastAPI(title="sermon web", docs_url=None, redoc_url=None)
    app.include_router(router, prefix="/api")
    app.include_router(media_router, prefix="/media")

    public_dir = REPO_ROOT / "captions" / "public"
    out_dir = REPO_ROOT / "captions" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media/app", StaticFiles(directory=public_dir), name="media-app")
    app.mount("/media/out", StaticFiles(directory=out_dir), name="media-out")

    if WEB_DIST.joinpath("index.html").is_file():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="ui")
    return app


app = create_app()


def _find_free_port(start: int) -> int:
    for port in range(start, start + 11):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # match uvicorn's own bind flags, or a just-restarted server drifts
            # to the next port while the browser still points at the old one
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"error: no free port in {start}-{start + 10}")


def run_server(port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    import uvicorn

    chosen = _find_free_port(port)
    url = f"http://127.0.0.1:{chosen}"
    if not WEB_DIST.joinpath("index.html").is_file():
        print("web UI not built — API only. Build it with: cd web && npm install && npm run build")
    print(f"sermon web: {url}")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run("sermon.web.app:app", host="127.0.0.1", port=chosen, log_level="warning")
