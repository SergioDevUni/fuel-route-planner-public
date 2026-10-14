"""HTTPX transport matching the hosted provider's working curl HTTP/1.1 path."""

import shutil
from collections.abc import Callable
from typing import cast

import httpx

from app.routing.curl_process import transfer
from app.routing.normalizer import RoutingResponseInvalid

HEADER_LIMIT = 64 * 1024


def quote(value: str) -> str:
    if any(char in value for char in ("\r", "\n", "\0")):
        raise httpx.LocalProtocolError(
            "curl configuration contains a control character"
        )
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def response_parts(
    raw: bytes, body_limit: int
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    position = 0
    while True:
        end = raw.find(b"\r\n\r\n", position)
        if end < 0 or end + 4 > HEADER_LIMIT:
            raise RoutingResponseInvalid("invalid curl response headers")
        lines = raw[position:end].split(b"\r\n")
        status_parts = lines[0].split(b" ", 2)
        if len(status_parts) < 2 or status_parts[0] != b"HTTP/1.1":
            raise RoutingResponseInvalid("invalid curl response status")
        try:
            status = int(status_parts[1])
        except ValueError:
            raise RoutingResponseInvalid("invalid curl response status") from None
        position = end + 4
        if 100 <= status < 200:
            continue
        if not 200 <= status <= 599:
            raise RoutingResponseInvalid("invalid curl response status")
        headers = []
        for line in lines[1:]:
            key, separator, value = line.partition(b":")
            if not separator:
                raise RoutingResponseInvalid("invalid curl response header")
            if (
                key.lower() == b"content-encoding"
                and value.strip().lower() != b"identity"
            ):
                raise RoutingResponseInvalid(
                    "provider ignored the identity encoding request"
                )
            # curl has already decoded HTTP chunk framing.
            if key.lower() not in (b"content-length", b"transfer-encoding"):
                headers.append((key, value.strip()))
        body = raw[position:]
        if len(body) > body_limit:
            raise RoutingResponseInvalid("routing response exceeds size limit")
        return status, headers, body


class CurlTransport(httpx.BaseTransport):
    def __init__(self, *, max_response_bytes: int) -> None:
        self.executable = shutil.which("curl")
        if self.executable is None:
            raise RuntimeError(
                "curl is required for hosted routing; install curl first"
            )
        self.max_response_bytes = max_response_bytes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        check = cast(
            Callable[[], None],
            request.extensions.get("routing_deadline_check", lambda: None),
        )
        check()
        timeout = request.extensions["timeout"]
        seconds = float(timeout["read"])
        connect = float(timeout["connect"])
        configuration = [
            "url = " + quote(str(request.url)),
            "request = " + quote(request.method),
            'user-agent = "fuel-route-planner/0.1.0"',
            'header = "Accept-Encoding: identity"',
        ]
        for raw_key, raw_value in request.headers.raw:
            key, value = raw_key.decode("ascii"), raw_value.decode("ascii")
            if key.lower() not in (
                "host",
                "connection",
                "content-length",
                "accept-encoding",
                "user-agent",
            ):
                configuration.append("header = " + quote(key + ": " + value))
        configuration.append("data = " + quote(request.read().decode("utf-8")))
        assert self.executable is not None
        raw = transfer(
            [
                self.executable,
                "--disable",
                "--http1.1",
                "--silent",
                "--show-error",
                "--suppress-connect-headers",
                "--dump-header",
                "-",
                "--output",
                "-",
                "--retry",
                "0",
                "--max-time",
                f"{seconds:.6f}",
                "--connect-timeout",
                f"{connect:.6f}",
                "--config",
                "-",
            ],
            ("\n".join(configuration) + "\n").encode("utf-8"),
            seconds=seconds,
            output_limit=self.max_response_bytes + HEADER_LIMIT,
            check_deadline=check,
        )
        status, headers, body = response_parts(raw, self.max_response_bytes)
        return httpx.Response(status, headers=headers, content=body, request=request)
