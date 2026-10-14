"""Run the project API with Waitress and cooperative planner shutdown."""

import argparse
import logging
import os
import signal
import threading


def main(*, local: bool = False) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="127.0.0.1:8000")
    parser.add_argument("--threads", type=int, default=32)
    parser.add_argument("--trusted-proxy")
    args = parser.parse_args()
    if not 8 <= args.threads <= 64:
        parser.error("threads must be 8-64; leave room for busy/health responses")
    if args.trusted_proxy == "*":
        parser.error("trusted proxy must be an exact address, not a wildcard")
    module = "config.settings" if local else "config.production"
    if os.environ.get("DJANGO_SETTINGS_MODULE", module) != module:
        parser.error(f"serve_api requires DJANGO_SETTINGS_MODULE={module}")
    os.environ["DJANGO_SETTINGS_MODULE"] = module

    from waitress import wasyncore
    from waitress.server import create_server

    from app.api.deadline import planning_activity, shutdown_planning
    from config.wsgi import application

    logger = logging.getLogger("app.api.operations")
    options = {
        "listen": args.listen,
        "threads": args.threads,
        "connection_limit": 100,
        "backlog": 32,
        "max_request_header_size": 16384,
        # Application limit is 16 KiB; ingress hard cap is 64 KiB.
        # The headroom lets ordinary oversized JSON receive the API envelope.
        "max_request_body_size": 65536,
        "channel_timeout": 30,
        "clear_untrusted_proxy_headers": True,
        "expose_tracebacks": False,
    }
    if args.trusted_proxy:
        options.update(
            trusted_proxy=args.trusted_proxy,
            trusted_proxy_count=1,
            trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto"},
        )
    channels = {}
    server = create_server(application, map=channels, **options)
    stopping = threading.Event()

    def stop(signum, frame):
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)
    io_thread = threading.Thread(target=server.run, name="api-http", daemon=True)
    io_thread.start()
    logger.info("api_server_started", extra=planning_activity())
    try:
        while io_thread.is_alive() and not stopping.wait(0.25):
            pass
    finally:
        drained = shutdown_planning(timeout=20)
        logger.info(
            "api_shutdown_drained" if drained else "api_shutdown_workers_remaining",
            extra=planning_activity(),
        )
        server.task_dispatcher.shutdown(timeout=5)
        wasyncore.close_all(map=channels)
        io_thread.join(timeout=2)
    if not drained:
        # Python cannot safely kill an in-process thread. Never claim a drain.
        raise SystemExit(
            "Planner drain exceeded 20 seconds; supervisor intervention needed"
        )


if __name__ == "__main__":
    main()
