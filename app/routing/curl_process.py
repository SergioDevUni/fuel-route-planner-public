"""Run one curl transfer with bounded output and joined pipe workers."""

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from io import BufferedReader
from typing import cast

import httpx

from app.routing.normalizer import RoutingResponseInvalid


def transfer(
    arguments: list[str],
    configuration: bytes,
    *,
    seconds: float,
    output_limit: int,
    check_deadline: Callable[[], None],
) -> bytes:
    expires = time.monotonic() + seconds
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except OSError:
        raise httpx.ConnectError("curl could not start") from None
    assert process.stdin is not None and process.stdout is not None
    incoming = process.stdin
    outgoing = cast(BufferedReader, process.stdout)
    buffer = bytearray()
    errors: list[Exception] = []
    stopped = threading.Event()

    def feed() -> None:
        try:
            incoming.write(configuration)
            incoming.flush()
        except (BrokenPipeError, OSError):
            pass  # curl's exit status identifies an interrupted transfer.
        finally:
            incoming.close()

    def collect() -> None:
        try:
            while chunk := outgoing.read1(64 * 1024):
                if len(buffer) + len(chunk) > output_limit:
                    errors.append(
                        RoutingResponseInvalid("routing response exceeds size limit")
                    )
                    stopped.set()
                    return
                buffer.extend(chunk)
        except OSError:
            errors.append(httpx.ReadError("curl response pipe failed"))
            stopped.set()

    writer = threading.Thread(target=feed, name="routing-curl-input")
    reader = threading.Thread(target=collect, name="routing-curl-output")
    writer.start()
    reader.start()
    try:
        while process.poll() is None:
            check_deadline()
            if stopped.is_set():
                raise errors[0]
            remaining = expires - time.monotonic()
            if remaining <= 0:
                raise httpx.ReadTimeout("curl transfer timed out")
            stopped.wait(min(0.02, remaining))
        reader.join()
        writer.join()
        check_deadline()
        if errors:
            raise errors[0]
        if process.returncode == 28:
            raise httpx.ReadTimeout("curl transfer timed out")
        if process.returncode:
            raise httpx.ConnectError("curl transfer failed")
        return bytes(buffer)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        writer.join()
        reader.join()
        outgoing.close()
