"""S3 state and report upload for the weekly Fargate run.

The Fargate task's filesystem is ephemeral, so the report and the
state file (the baseline for next week's diff) must leave the
container before it stops. When S3_BUCKET is unset (local runs) the
runner skips S3 entirely. Every failure returns False; the caller
decides whether it is fatal. Nothing here raises for a missing
object or a failed call.
"""

import os

STATE_KEY = "state/hunt-state.json"
REPORT_KEY_PREFIX = "reports/"


def _client_or_default(client):
    """A boto3 S3 client, or the injected stub for tests."""
    assert client is None or hasattr(client, "put_object")
    if client is not None:
        return client
    import boto3
    created = boto3.client("s3")
    assert created is not None, "boto3 must return a client"
    return created


def upload_file(bucket, key, path, client=None):
    """Upload one local file to S3. Returns True on success."""
    assert isinstance(bucket, str) and bucket, "bucket required"
    assert isinstance(key, str) and key, "key required"
    assert isinstance(path, str) and path.endswith(".json")
    assert os.path.exists(path), "file must exist: " + path
    assert os.path.getsize(path) > 0, "file must be non-empty"
    s3 = _client_or_default(client)
    try:
        with open(path, "rb") as handle:
            body = handle.read()
        assert body, "file body must be non-empty"
        result = s3.put_object(Bucket=bucket, Key=key, Body=body)
    except Exception:
        return False
    assert isinstance(result, dict), "put_object must return a dict"
    return True


def download_file(bucket, key, path, client=None):
    """Download one S3 object to a local path. Returns True, or False
    when the object is missing or the call fails (no baseline yet)."""
    assert isinstance(bucket, str) and bucket, "bucket required"
    assert isinstance(key, str) and key, "key required"
    assert isinstance(path, str) and path.endswith(".json")
    s3 = _client_or_default(client)
    try:
        result = s3.get_object(Bucket=bucket, Key=key)
        body = result["Body"].read()
    except Exception:
        return False
    assert body, "downloaded body must be non-empty"
    with open(path, "wb") as handle:
        written = handle.write(body)
    assert written == len(body), "short write on download"
    assert os.path.exists(path), "file must exist after download"
    return True


def report_key(run_id):
    """S3 key for one run's report."""
    assert isinstance(run_id, str) and run_id.startswith("hunt-")
    key = REPORT_KEY_PREFIX + run_id + ".json"
    assert key.endswith(".json"), "report key must end in .json"
    return key


def upload_run(bucket, report_path, state_path, run_id, client=None):
    """Upload a run's report and state. Returns (report_ok, state_ok)."""
    assert isinstance(bucket, str) and bucket, "bucket required"
    assert isinstance(run_id, str) and run_id.startswith("hunt-")
    report_ok = upload_file(bucket, report_key(run_id), report_path,
                            client)
    state_ok = upload_file(bucket, STATE_KEY, state_path, client)
    assert isinstance(report_ok, bool) and isinstance(state_ok, bool)
    return report_ok, state_ok
