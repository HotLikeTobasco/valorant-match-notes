import argparse
import json
from pathlib import Path

from .config import Config


def main():
    parser = argparse.ArgumentParser(description="Local VALORANT post-match analysis")
    parser.add_argument("--config", help="JSON configuration path")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Scan settled files and process pending jobs, then exit",
    )
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.once:
        import time
        from .pipeline import Pipeline

        pipeline = Pipeline(config)
        with pipeline.instance_lock.acquire(timeout=0):
            pipeline.store.recover()
            pipeline.scan()
            if not pipeline.scan_error:
                time.sleep(config.settle_seconds)
                pipeline.scan()
                while pipeline.process_next():
                    pass
        errors = [s for s in pipeline.store.list() if s["state"] == "failed"]
        print(
            json.dumps(
                {
                    "sessions": len(pipeline.store.list()),
                    "failed": len(errors),
                    "watcher_error": pipeline.scan_error,
                }
            )
        )
        raise SystemExit(1 if errors or pipeline.scan_error else 0)
    import uvicorn
    from .app import create_app

    # A single loopback worker owns the durable queue. Do not use multiple uvicorn workers.
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
