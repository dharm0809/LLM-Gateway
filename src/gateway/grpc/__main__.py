"""Entrypoint for ``python -m gateway.grpc``.

Starts the gRPC governance sidecar as a standalone process, independent
of the ASGI app.  Useful for the hybrid architecture where the Go proxy
handles HTTP and the Python sidecar handles governance intelligence.
"""

import asyncio

from gateway.grpc.server import run_standalone

if __name__ == "__main__":
    asyncio.run(run_standalone())
