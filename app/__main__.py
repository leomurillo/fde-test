"""python -m app [--host 127.0.0.1] [--port 8000]"""

from __future__ import annotations

import argparse
import logging


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app", description="Run the FDE practice exam locally.")
    ap.add_argument("--host", default="127.0.0.1", help="interface to bind (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(levelname)s %(name)s: %(message)s")

    import uvicorn

    from .main import create_app

    app = create_app()
    print(f"FDE Test running at http://{args.host}:{args.port}  (data: {app.state.ctx.data_dir})")
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
