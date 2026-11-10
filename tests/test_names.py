"""Unit tests for Groq + Bedrock name-cleaning (hunt/names.py).

No real Groq calls: a local stub HTTP server answers with a canned
chat completion, so the request contract (model, reasoning_effort,
max_tokens, response_format, browser User-Agent, Bearer auth) is
verified offline. No real Bedrock calls either: a stub client
answers with a canned Converse response. One env-gated live test
runs only when GROQ_API_KEY is set.
"""

import contextlib
import http.server
import io
import json
import os
import threading
import unittest

import hunt.names as names
from hunt.names import (
    MODEL,
    _extract_json_object,
    _validate_terms,
    clean_name,
    clean_name_bedrock,
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


class _StubBedrockClient:
    """Fake boto3 bedrock-runtime client with a canned Converse
    answer. Records the request so the contract is verifiable."""

    def __init__(self, text=None, error=None):
        assert text is None or isinstance(text, str)
        self.text = text
        self.error = error
        self.calls = []

    def converse(self, **kwargs):
        assert kwargs, "converse must be called with arguments"
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"output": {"message": {"role": "assistant",
                                       "content": [{"text": self.text}]}}}


BEDROCK_TERMS = json.dumps({
    "distillery": "Royal Brackla",
    "age_years": 18,
    "edition": "palo-cortado-sherry-cask-finish",
    "is_willet_rye": False,
})


class BedrockTest(unittest.TestCase):
    def test_stub_converse_returns_terms(self):
        client = _StubBedrockClient(text=BEDROCK_TERMS)
        cleaned = clean_name_bedrock(
            "Royal Brackla 18 Palo Cortado", client=client)
        assert cleaned is not None
        assert cleaned["distillery"] == "Royal Brackla"
        assert cleaned["age_years"] == 18
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["modelId"] == names.BEDROCK_MODEL_ID
        assert call["inferenceConfig"]["temperature"] == 0
        assert call["messages"][0]["role"] == "user"
        assert "Royal Brackla 18" in call["messages"][0]["content"][0]["text"]
        self.assertEqual(cleaned["distillery"], "Royal Brackla")

    def test_error_returns_none(self):
        client = _StubBedrockClient(error=RuntimeError("denied"))
        assert clean_name_bedrock("Royal Brackla 18",
                                  client=client) is None
        self.assertIsNone(clean_name_bedrock(
            "Royal Brackla 18", client=client))

    def test_fenced_json_returns_terms(self):
        """10 Nov 2026: the slice-7 run's 376 Bedrock calls all
        failed silently and the persisted name cache came back
        empty. Bedrock carries no response_format guarantee, so a
        fenced answer must parse, not die in json.loads."""
        fenced = "```json\n" + BEDROCK_TERMS + "\n```"
        client = _StubBedrockClient(text=fenced)
        cleaned = clean_name_bedrock(
            "Royal Brackla 18 Palo Cortado", client=client)
        assert cleaned is not None
        assert cleaned["distillery"] == "Royal Brackla"
        assert cleaned["age_years"] == 18
        self.assertEqual(cleaned["distillery"], "Royal Brackla")

    def test_prose_wrapped_json_returns_terms(self):
        text = ("Here are the canonical terms:\n" + BEDROCK_TERMS
                + "\nHope that helps.")
        client = _StubBedrockClient(text=text)
        cleaned = clean_name_bedrock(
            "Royal Brackla 18 Palo Cortado", client=client)
        assert cleaned is not None
        assert cleaned["age_years"] == 18

    def test_extract_json_object_edges(self):
        assert _extract_json_object(BEDROCK_TERMS) is not None
        assert _extract_json_object("no braces here") is None
        assert _extract_json_object('{"a": 1') is None
        assert _extract_json_object('["Royal Brackla", 18]') is None
        self.assertIsNone(_extract_json_object("plain text"))

    def test_non_object_json_returns_none(self):
        # Bedrock carries no response_format guarantee: a valid JSON
        # array or bare string must return None, never raise.
        client = _StubBedrockClient(text='["Royal Brackla", 18]')
        assert clean_name_bedrock("Royal Brackla 18",
                                  client=client) is None
        assert _validate_terms(["Royal Brackla", 18]) is None
        assert _validate_terms("Royal Brackla") is None
        self.assertIsNone(_validate_terms([1, 2]))

    def test_clean_to_key_prefers_bedrock(self):
        # Bedrock succeeds, so Groq must never be called: the Groq
        # base URL points at a dead port and would fail if used.
        saved = names.clean_name_bedrock
        client = _StubBedrockClient(text=BEDROCK_TERMS)
        names.clean_name_bedrock = (
            lambda name, **kw: clean_name_bedrock(name, client=client))
        try:
            key = clean_to_key("Royal Brackla 18 Palo Cortado",
                               api_key="unused",
                               base_url="http://127.0.0.1:1")
        finally:
            names.clean_name_bedrock = saved
        assert key == ("royal-brackla-18yr-"
                       "palo-cortado-sherry-cask-finish")
        self.assertEqual(
            key, "royal-brackla-18yr-palo-cortado-sherry-cask-finish")

    def test_clean_to_key_falls_back_to_groq(self):
        saved = names.clean_name_bedrock
        names.clean_name_bedrock = lambda name, **kw: None
        server, thread, url = _run_stub()
        try:
            key = clean_to_key("Royal Brackla 18 Palo Cortado",
                               api_key="test-key", base_url=url)
        finally:
            names.clean_name_bedrock = saved
            _stop_stub(server, thread)
        assert key == ("royal-brackla-18yr-"
                       "palo-cortado-sherry-cask-finish")
        self.assertEqual(
            key, "royal-brackla-18yr-palo-cortado-sherry-cask-finish")


class FirstFailureLoggingTest(unittest.TestCase):
    def setUp(self):
        names._reset_logged_failures()

    def _captured(self, func):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = func()
        return result, buf.getvalue()

    def test_bedrock_error_logs_once(self):
        """Slice 11: 376 Bedrock calls, 0 succeeded, and the log
        said nothing about why. The first failure must name the
        exception; later ones stay silent."""
        client = _StubBedrockClient(error=RuntimeError("denied"))
        _, out1 = self._captured(lambda: clean_name_bedrock(
            "Royal Brackla 18", client=client))
        assert "Bedrock clean failed" in out1
        assert "denied" in out1
        _, out2 = self._captured(lambda: clean_name_bedrock(
            "Royal Brackla 18", client=client))
        assert out1.count("Bedrock clean failed") == 1
        assert "Bedrock clean failed" not in out2
        self.assertIn("denied", out1)

    def test_bedrock_non_json_answer_logs(self):
        client = _StubBedrockClient(text="not json at all")
        result, out = self._captured(lambda: clean_name_bedrock(
            "Royal Brackla 18", client=client))
        assert result is None
        assert "answer was not JSON" in out
        self.assertIn("answer was not JSON", out)

    def test_bedrock_validation_failure_logs(self):
        text = json.dumps({"distillery": 123, "age_years": 18,
                           "edition": "", "is_willet_rye": False})
        client = _StubBedrockClient(text=text)
        result, out = self._captured(lambda: clean_name_bedrock(
            "Royal Brackla 18", client=client))
        assert result is None
        assert "failed validation" in out
        self.assertIn("failed validation", out)

    def test_groq_http_error_logs_status(self):
        """A 429 (rate limit) and a 401 (bad key) need different
        fixes, so the first non-200 must print its status."""
        server, thread, url = _run_stub(status=429,
                                        body={"error": "slow down"})
        try:
            result, out = self._captured(lambda: clean_name(
                "Royal Brackla 18", api_key="test-key", base_url=url))
        finally:
            _stop_stub(server, thread)
        assert result is None
        assert "Groq clean failed" in out
        assert "429" in out
        self.assertIn("429", out)


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
