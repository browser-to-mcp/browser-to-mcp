"""Run the local owner interface with its existing supervised job worker."""

import json
import sys
from pathlib import Path


def add_interface_parser(subparsers):
    parser = subparsers.add_parser(
        "ui", help="Create, review and run public tools in a local browser interface."
    )
    parser.add_argument("--directory", type=Path, default=Path(".browser-to-mcp-ui"))
    parser.add_argument("--jobs-directory", type=Path, default=Path(".browser-to-mcp-jobs"))
    parser.add_argument("--library-directory", type=Path, default=Path(".browser-to-mcp-library"))
    parser.add_argument(
        "--port", type=int, default=0, help="Loopback port; 0 selects an unused port."
    )


def run_interface(args):
    # The existing supervised worker requires POSIX; keep other CLI modes portable.
    from .http import LocalServer
    from .service import Interface

    service = None
    server = None
    try:
        if not 0 <= args.port <= 65535:
            raise ValueError
        service = Interface(args.directory, args.jobs_directory, args.library_directory)
        server = LocalServer(service, args.port)
        print("Open this private local URL in your browser (do not share it):", file=sys.stderr)
        print(server.url, flush=True)
        print(
            "Press Ctrl+C to stop. Running work is interrupted, not silently retried.",
            file=sys.stderr,
        )
        server.serve_forever(poll_interval=0.2)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(
            json.dumps({"error": "Cannot start UI. Check paths, port and other workers."}),
            file=sys.stderr,
        )
        return 2
    finally:
        if server:
            server.server_close()
        if service:
            service.close()
