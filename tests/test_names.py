"""Unit tests for Groq name-cleaning (hunt/names.py).

No real Groq calls: a local stub HTTP server answers with a canned
chat completion, so the request contract (model, reasoning_effort,
max_tokens, response_format, browser User-Agent, Bearer auth) is
verified offline. One env-gated live test runs only when GROQ_API_KEY
is set.
"""

import http.server
import json
import os
import threading
import unittest

from hunt.names import (
    MODEL,
    clean_name,
    clean_to_key,
    parse_clean_response,
)

COMPLETION = {
    "choices": [{
        "message": {"content": json.dumps({
            "distillery": "Royal Brackla",
            "age_years": 18,
            "edition": "palo-cortado-sherry-cask-finish",
            "is_willet_rye": False,
        })},
    }],
}


def _completion(content):
    assert isinstance(content, str), "content must be a string"
    result = {"choices": [{"message": {"content": content}}]}
    assert result["choices"][0]["message"]["content"] == content
    return result


class _StubHandler(http.server.BaseHTTPRequestHandler):
    """Record one request, answer with the canned completion."""

    seen = []
    status = 200
    body = COMPLETION

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler naming
        length = int(self.headers.get("Content-Length", "0"))
        assert length > 0, "POST must carry a body"
        raw = self.rfile.read(length)
        assert raw, "POST body must be non-empty"
        _StubHandler.seen.append({
            "path": self.path,
            "headers": dict(self.headers),
            "body": json.loads(raw.decode("utf-8")),
        })
        payload = json.dumps(_StubHandler.body).encode("utf-8")
        assert payload, "stub response must be non-empty"
        self.send_response(_StubHandler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        assert args, "log call must carry arguments"


def _run_stub(status=200, body=None):
    """Start the stub server. Returns (server, thread, base_url)."""
    assert status in (200, 400, 401, 403, 429, 500), "bad stub status"
    _StubHandler.seen = []
    _StubHandler.status = status
    _StubHandler.body = body if body is not None else COMPLETION
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), _StubHandler)
    assert server.server_address[1] > 0, "stub must bind a port"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%d/openai/v1" % server.server_address[1]
    assert url.startswith("http://127.0.0.1"), "stub URL must be local"
    return server, thread, url


def _stop_stub(server, thread):
    assert server is not None, "server required"
    assert thread is not None, "thread required"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive(), "stub thread must stop"


class ParseResponseTest(unittest.TestCase):
    def test_valid_completion_parses(self):
        cleaned = parse_clean_response(COMPLETION)
        assert cleaned is not None, "valid completion must parse"
        assert cleaned["distillery"] == "Royal Brackla"
        assert cleaned["age_years"] == 18
        assert cleaned["edition"] == "palo-cortado-sherry-cask-finish"
        assert cleaned["is_willet_rye"] is False
        self.assertEqual(cleaned["distillery"], "Royal Brackla")
        self.assertEqual(cleaned["age_years"], 18)

    def test_missing_choices_is_none(self):
        assert parse_clean_response({}) is None
        assert parse_clean_response({"choices": []}) is None
        self.assertIsNone(parse_clean_response({}))

    def test_truncated_content_is_none(self):
        data = _completion('{"distillery": "Royal Brackla", "age_years":')
        assert parse_clean_response(data) is None
        self.assertIsNone(parse_clean_response(data))

    def test_wrong_types_are_none(self):
        data = _completion(json.dumps({
            "distillery": "Royal Brackla", "age_years": "18",
            "edition": "", "is_willet_rye": False}))
        assert parse_clean_response(data) is None
        data2 = _completion(json.dumps({
            "distillery": "", "age_years": 18,
            "edition": "", "is_willet_rye": False}))
        assert parse_clean_response(data2) is None
        self.assertIsNone(parse_clean_response(data))
        self.assertIsNone(parse_clean_response(data2))

    def test_null_age_is_allowed(self):
        data = _completion(json.dumps({
            "distillery": "Chivas Regal Ultis", "age_years": None,
            "edition": "", "is_willet_rye": False}))
        cleaned = parse_clean_response(data)
        assert cleaned is not None, "null age must parse"
        assert cleaned["age_years"] is None
        self.assertIsNone(cleaned["age_years"])


class CleanNameStubTest(unittest.TestCase):
    def test_stub_call_returns_terms(self):
        server, thread, url = _run_stub()
        try:
            cleaned = clean_name("Royal Brackla 18 Palo Cortado",
                                 api_key="test-key", base_url=url)
            assert cleaned is not None, "stub call must return terms"
            assert cleaned["distillery"] == "Royal Brackla"
            self.assertEqual(cleaned["age_years"], 18)
        finally:
            _stop_stub(server, thread)

    def test_stub_request_contract(self):
        server, thread, url = _run_stub()
        try:
            cleaned = clean_name("Royal Brackla 18",
                                 api_key="test-key", base_url=url)
            assert cleaned is not None, "stub call must succeed"
            assert len(_StubHandler.seen) == 1, "one request expected"
            seen = _StubHandler.seen[0]
            assert seen["path"] == "/openai/v1/chat/completions"
            assert seen["headers"]["Authorization"] == "Bearer test-key"
            assert "Mozilla/5.0" in seen["headers"]["User-Agent"]
            assert "python-urllib" not in seen["headers"]["User-Agent"]
            body = seen["body"]
            assert body["model"] == MODEL, "model must be gpt-oss-120b"
            assert body["model"] != "llama-3.3-70b-versatile"
            assert body["reasoning_effort"] == "low"
            assert body["max_tokens"] == 1024
            assert body["response_format"] == {"type": "json_object"}
            assert body["temperature"] == 0
            self.assertEqual(body["model"], "openai/gpt-oss-120b")
        finally:
            _stop_stub(server, thread)

    def test_http_error_returns_none(self):
        server, thread, url = _run_stub(status=500, body={"error": "boom"})
        try:
            assert clean_name("Royal Brackla 18",
                              api_key="test-key", base_url=url) is None
            self.assertIsNone(clean_name(
                "Royal Brackla 18", api_key="test-key", base_url=url))
        finally:
            _stop_stub(server, thread)

    def test_bad_answer_returns_none(self):
        body = _completion('{"distillery": "Royal Brackla", "age_years":')
        server, thread, url = _run_stub(status=200, body=body)
        try:
            assert clean_name("Royal Brackla 18",
                              api_key="test-key", base_url=url) is None
            self.assertIsNone(clean_name(
                "Royal Brackla 18", api_key="test-key", base_url=url))
        finally:
            _stop_stub(server, thread)

    def test_clean_to_key_via_stub(self):
        server, thread, url = _run_stub()
        try:
            key = clean_to_key("Royal Brackla 18 Palo Cortado",
                               api_key="test-key", base_url=url)
            assert key == ("royal-brackla-18yr-"
                           "palo-cortado-sherry-cask-finish")
            self.assertEqual(
                key, "royal-brackla-18yr-palo-cortado-sherry-cask-finish")
        finally:
            _stop_stub(server, thread)

    def test_no_key_returns_none(self):
        if "GROQ_API_KEY" in os.environ:
            self.skipTest("GROQ_API_KEY is set in this environment")
        assert clean_name("Royal Brackla 18") is None
        assert clean_to_key("Royal Brackla 18") is None
        self.assertIsNone(clean_name("Royal Brackla 18"))


@unittest.skipUnless("GROQ_API_KEY" in os.environ,
                     "live test: set GROQ_API_KEY")
class CleanNameLiveTest(unittest.TestCase):
    def test_live_clean_known_bottle(self):
        cleaned = clean_name(
            "Royal Brackla 18 Year Old Palo Cortado Sherry Cask Finish")
        assert cleaned is not None, "live clean must return terms"
        assert cleaned["distillery"] == "Royal Brackla"
        assert cleaned["age_years"] == 18
        self.assertEqual(cleaned["distillery"], "Royal Brackla")


if __name__ == "__main__":
    unittest.main()
