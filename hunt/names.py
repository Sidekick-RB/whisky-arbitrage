"""Groq + Bedrock name-cleaning for ambiguous listing names.

Cleans a messy retail bottle name into canonical match terms
(distillery, age_years, edition, is_willet_rye). Primary provider
is Amazon Bedrock (boto3 Converse API, model from BEDROCK_MODEL_ID,
default Amazon Nova Lite in the task's region); Groq's chat
completions API is the fallback. The Groq key comes from the
GROQ_API_KEY environment variable only, and Bedrock uses the task
role's credentials; neither is ever written to a file. When both
providers fail, the functions return None and the caller marks the
match unverified. Nothing here fabricates a key.

Bedrock model access: the Model access page was retired in 2026 —
serverless models are auto-enabled on first invoke, so the only
setup is the bedrock:Converse / bedrock:InvokeModel permission on
the task role (inline policy whisky-hunt-bedrock-access). Groq
model: openai/gpt-oss-120b (verified available to this key 9 Nov
2026; llama-3.3-70b-versatile is NOT available — Groq moved it to
Enterprise). gpt-oss-120b is a reasoning model, so every Groq call
sends reasoning_effort low, max_tokens 1024 and response_format
json_object, or the answer truncates mid-JSON. Every Groq request
carries a browser User-Agent header or api.groq.com returns a
Cloudflare 403.
"""

import json
import os
import urllib.error
import urllib.request

MODEL = "openai/gpt-oss-120b"
API_BASE = "https://api.groq.com/openai/v1"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT_S = 30
MAX_NAME_CHARS = 500
MAX_BODY_BYTES = 65536
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID",
                                  "amazon.nova-lite-v1:0")
BEDROCK_REGION = os.environ.get("AWS_REGION", "ap-southeast-1")

CLEAN_SYSTEM = (
    "You clean whisky retail listing names into canonical search terms. "
    "Reply with JSON only, no other text: "
    '{"distillery": str, "age_years": int or null, '
    '"edition": str, "is_willet_rye": bool}. '
    "edition is the distinguishing release marker (e.g. '2020-release', "
    "'palo-cortado-sherry-cask-finish') or an empty string. "
    "is_willet_rye is true only for Willet rye whiskey."
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects; the endpoint is fixed and verified."""

    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")


def _post_json(base_url, api_key, payload):
    """POST a JSON payload. Returns (status, parsed dict) or (None, {})."""
    assert isinstance(base_url, str) and base_url.startswith("http")
    assert isinstance(api_key, str) and api_key, "API key required"
    assert isinstance(payload, dict) and payload, "payload required"
    body = json.dumps(payload).encode("utf-8")
    assert body, "payload must serialize"
    url = base_url.rstrip("/") + "/chat/completions"
    assert url.startswith("https://") or url.startswith("http://127.0.0.1")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Accept", "application/json")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Authorization", "Bearer " + api_key)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=TIMEOUT_S) as resp:
            assert resp.status == 200, "Groq did not return 200"
            text = resp.read(MAX_BODY_BYTES).decode("utf-8")
    except Exception:
        return None, {}
    assert text, "Groq response must be non-empty"
    try:
        data = json.loads(text)
    except ValueError:
        return None, {}
    assert isinstance(data, dict), "Groq response must be a JSON object"
    return resp.status, data


def _validate_terms(result):
    """Validate parsed JSON into canonical terms, or None.

    Shared by the Groq and Bedrock paths. A non-object, or any
    missing or mistyped field, yields None — never a guessed key.
    Bedrock answers carry no response_format guarantee, so a valid
    JSON array or bare string must return None, not raise.
    """
    if not isinstance(result, dict):
        return None
    distillery = result.get("distillery")
    age = result.get("age_years")
    edition = result.get("edition")
    is_willet = result.get("is_willet_rye")
    if not isinstance(distillery, str) or not distillery.strip():
        return None
    if age is not None and not isinstance(age, int):
        return None
    if age is not None and age < 0:
        return None
    if not isinstance(edition, str):
        return None
    if not isinstance(is_willet, bool):
        return None
    cleaned = {
        "distillery": distillery.strip(),
        "age_years": age,
        "edition": edition.strip(),
        "is_willet_rye": is_willet,
    }
    assert cleaned["distillery"], "distillery must be non-empty"
    assert isinstance(cleaned["is_willet_rye"], bool)
    return cleaned


def parse_clean_response(data):
    """Validate a Groq chat completion into canonical terms, or None.

    Returns a dict with distillery (str), age_years (int or None),
    edition (str) and is_willet_rye (bool). Any missing or mistyped
    field yields None: the caller marks the match unverified.
    """
    assert isinstance(data, dict), "completion must be a dict"
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    assert isinstance(first, dict), "choice must be a dict"
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        result = json.loads(content)
    except ValueError:
        return None
    return _validate_terms(result)


def _extract_json_object(text):
    """First JSON object in model text, or None.

    Bedrock Converse carries no response_format guarantee, so Nova
    may wrap the answer in ```json fences or prose. Strip fences,
    then take the substring from the first '{' to the last '}'.
    A missing or unbalanced object yields None, never a guess.
    """
    assert isinstance(text, str), "text must be a string"
    stripped = text.strip()
    assert stripped, "text must be non-empty after trim"
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        assert lines, "fenced text must have lines"
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        result = json.loads(stripped[start:end + 1])
    except ValueError:
        return None
    assert isinstance(result, dict), "extracted JSON must be an object"
    return result


def clean_name_bedrock(name, model_id=None, region=None, client=None):
    """Clean one retail name via Bedrock Converse. Returns canonical
    terms, or None.

    client exists only so tests can inject a stub; production builds
    a boto3 bedrock-runtime client with the task role's credentials.
    Any failure (no boto3, no credentials, denied model, bad answer)
    returns None and the caller falls back to Groq.
    """
    assert isinstance(name, str) and name.strip(), "name required"
    model = model_id or BEDROCK_MODEL_ID
    assert isinstance(model, str) and model, "model id required"
    area = region or BEDROCK_REGION
    assert isinstance(area, str) and area, "region required"
    trimmed = name.strip()[:MAX_NAME_CHARS]
    assert trimmed, "name must be non-empty after trim"
    try:
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client(
                "bedrock-runtime", region_name=area,
                config=Config(connect_timeout=10, read_timeout=30))
        response = client.converse(
            modelId=model,
            messages=[{"role": "user",
                       "content": [{"text": trimmed}]}],
            system=[{"text": CLEAN_SYSTEM}],
            inferenceConfig={"temperature": 0, "maxTokens": 1024})
        assert isinstance(response, dict), "Converse must return a dict"
        message = response["output"]["message"]
        parts = message.get("content") or []
        assert isinstance(parts, list), "content must be a list"
        text = "".join(part.get("text", "") for part in parts
                       if isinstance(part, dict))
        assert text.strip(), "Converse answer must be non-empty"
        result = _extract_json_object(text)
    except Exception:
        return None
    return _validate_terms(result)


def clean_name(name, api_key=None, base_url=API_BASE):
    """Clean one retail name. Returns canonical terms, or None.

    api_key defaults to the GROQ_API_KEY environment variable. base_url
    exists only so tests can point at a local stub server; production
    always uses api.groq.com.
    """
    assert isinstance(name, str) and name.strip(), "name required"
    assert isinstance(base_url, str) and base_url.startswith("http")
    key = api_key or os.environ.get("GROQ_API_KEY") or ""
    assert isinstance(key, str), "API key must be a string"
    if not key:
        return None
    trimmed = name.strip()[:MAX_NAME_CHARS]
    assert trimmed, "name must be non-empty after trim"
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 1024,
        "reasoning_effort": "low",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": CLEAN_SYSTEM},
            {"role": "user", "content": trimmed},
        ],
    }
    assert payload["model"] == MODEL, "model must be gpt-oss-120b"
    assert payload["reasoning_effort"] == "low", "reasoning must be low"
    status, data = _post_json(base_url, key, payload)
    assert status is None or isinstance(status, int)
    if status != 200:
        return None
    cleaned = parse_clean_response(data)
    assert cleaned is None or isinstance(cleaned, dict)
    return cleaned


def clean_to_key(name, api_key=None, base_url=API_BASE):
    """Clean one retail name into a canonical match key, or None.

    Bedrock first, Groq fallback. Returns the
    models.canonical_key() string, or None when the name could not
    be cleaned (no key, failed calls, invalid answers).
    """
    assert isinstance(name, str) and name.strip(), "name required"
    assert isinstance(base_url, str) and base_url.startswith("http")
    from .models import canonical_key

    cleaned = clean_name_bedrock(name)
    if cleaned is None:
        cleaned = clean_name(name, api_key=api_key, base_url=base_url)
    if cleaned is None:
        return None
    key = canonical_key(
        cleaned["distillery"], cleaned["age_years"],
        cleaned["edition"], cleaned["is_willet_rye"])
    assert key, "canonical key must be non-empty"
    assert isinstance(key, str), "canonical key must be a string"
    return key
