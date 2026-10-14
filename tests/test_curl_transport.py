import gzip
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx
import pytest

from app.planning.budget import PlanningDeadlineExceeded, RequestBudget
from app.routing.client import OpenRouteServiceAdapter, RoutingQuotaExceeded
from app.routing.curl_transport import CurlTransport, quote
from app.routing.normalizer import RoutingResponseInvalid


@pytest.fixture
def endpoint():
    observations = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            observations.append((self.path, dict(self.headers), json.loads(body)))
            if self.path == "/stall":
                time.sleep(1)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/destination")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/quota":
                status, payload = 403, b'{"error":"Quota exceeded"}'
            elif self.path == "/oversize":
                status, payload = 200, b"x" * 200000
            else:
                status, payload = 200, b'{"message":"hello"}'
            if self.path == "/gzip":
                payload = gzip.compress(payload)
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if self.path == "/gzip":
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", observations
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.integration
def test_real_curl_preserves_body_and_keeps_credentials_off_arguments(endpoint):
    url, observations = endpoint
    original = subprocess.Popen
    children = []

    def spawn(arguments, **kwargs):
        assert "private-key" not in " ".join(arguments)
        assert "--disable" == arguments[1]
        assert "--insecure" not in arguments
        child = original(arguments, **kwargs)
        children.append(child)
        return child

    adapter = OpenRouteServiceAdapter("private-key")
    budget = RequestBudget()
    try:
        with patch("app.routing.curl_process.subprocess.Popen", side_effect=spawn):
            assert adapter._post(
                url + "/json", {"label": 'héllo "quoted" \\ path'}, budget
            ) == {"message": "hello"}
    finally:
        adapter.close()
    assert budget.attempts == 1
    assert observations[0][1]["Authorization"] == "private-key"
    assert observations[0][1]["Accept-Encoding"] == "identity"
    assert observations[0][2] == {"label": 'héllo "quoted" \\ path'}
    assert all(child.poll() == 0 for child in children)


@pytest.mark.integration
def test_quota_response_stays_one_attempt_without_hidden_retry(endpoint):
    url, observations = endpoint
    adapter = OpenRouteServiceAdapter("private-key")
    budget = RequestBudget()
    try:
        with pytest.raises(RoutingQuotaExceeded):
            adapter._post(url + "/quota", {}, budget)
    finally:
        adapter.close()
    assert budget.attempts == len(observations) == 1


@pytest.mark.integration
def test_redirect_does_not_forward_credentials(endpoint):
    url, observations = endpoint
    with httpx.Client(transport=CurlTransport(max_response_bytes=1024)) as client:
        response = client.post(
            url + "/redirect", headers={"Authorization": "private-key"}, json={}
        )
    assert response.status_code == 302
    assert [row[0] for row in observations] == ["/redirect"]


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.integration
def test_deadline_and_cancellation_reap_child_and_join_pipe_workers(endpoint, cancel):
    url, _ = endpoint
    original = subprocess.Popen
    children = []
    timers = []
    budget = RequestBudget(deadline_seconds=15 if cancel else 0.15)

    def spawn(arguments, **kwargs):
        child = original(arguments, **kwargs)
        children.append(child)
        if cancel:
            timer = threading.Timer(0.1, budget.stop_work)
            timers.append(timer)
            timer.start()
        return child

    adapter = OpenRouteServiceAdapter("private-key")
    began = time.monotonic()
    try:
        with patch("app.routing.curl_process.subprocess.Popen", side_effect=spawn):
            with pytest.raises(PlanningDeadlineExceeded):
                adapter._post(url + "/stall", {}, budget)
    finally:
        adapter.close()
        for timer in timers:
            timer.cancel()
            timer.join()
    assert time.monotonic() - began < 0.6
    assert children and all(child.poll() is not None for child in children)
    assert not any(t.name.startswith("routing-curl-") for t in threading.enumerate())


@pytest.mark.integration
def test_oversized_output_aborts_before_buffer_growth(endpoint):
    url, _ = endpoint
    with httpx.Client(transport=CurlTransport(max_response_bytes=1024)) as client:
        with pytest.raises(RoutingResponseInvalid, match="size limit"):
            client.post(url + "/oversize", json={})
    assert not any(t.name.startswith("routing-curl-") for t in threading.enumerate())


@pytest.mark.integration
def test_provider_must_honor_identity_encoding(endpoint):
    url, _ = endpoint
    with httpx.Client(transport=CurlTransport(max_response_bytes=1024)) as client:
        with pytest.raises(RoutingResponseInvalid, match="identity encoding"):
            client.post(url + "/gzip", json={})


@pytest.mark.integration
def test_concurrent_transfers_keep_their_own_body(endpoint):
    url, observations = endpoint
    adapter = OpenRouteServiceAdapter("private-key")
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(
                pool.map(
                    lambda i: adapter._post(url + "/json", {"i": i}, RequestBudget()),
                    range(6),
                )
            )
    finally:
        adapter.close()
    assert results == [{"message": "hello"}] * 6
    assert {row[2]["i"] for row in observations} == set(range(6))


@pytest.mark.parametrize("value", ["line\nbreak", "line\rbreak", "null\0byte"])
def test_config_rejects_control_character_injection(value):
    with pytest.raises(httpx.LocalProtocolError):
        quote(value)
