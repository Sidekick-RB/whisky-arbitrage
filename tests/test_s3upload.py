"""Unit tests for the S3 upload/download helpers.

A stub S3 client stands in for boto3: it records put_object calls,
returns canned bodies for get_object, and can be told to fail. No
network, no bucket, no credentials.
"""

import io
import json
import os
import tempfile
import unittest

from hunt.s3upload import (
    NAME_CACHE_KEY,
    STATE_KEY,
    download_file,
    report_key,
    upload_cache,
    upload_file,
    upload_run,
)

BUCKET = "whisky-hunt-state-012389890270"


class _StubS3:
    """Minimal S3 stand-in: records puts, serves one canned object."""

    def __init__(self, fail_put=False, missing=False):
        assert isinstance(fail_put, bool) and isinstance(missing, bool)
        self.fail_put = fail_put
        self.missing = missing
        self.puts = []
        self.gets = []

    def put_object(self, Bucket, Key, Body):
        assert Bucket and Key and Body, "put needs bucket, key, body"
        if self.fail_put:
            raise RuntimeError("simulated put failure")
        self.puts.append((Bucket, Key, Body))
        return {"ETag": '"stub-etag"'}

    def get_object(self, Bucket, Key):
        assert Bucket and Key, "get needs bucket and key"
        if self.missing:
            raise RuntimeError("NoSuchKey")
        self.gets.append((Bucket, Key))
        return {"Body": io.BytesIO(b'{"baseline": true}')}


def _temp_json(payload):
    """A temp .json file holding the payload. Caller deletes it."""
    assert isinstance(payload, dict), "payload must be a dict"
    handle = tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, mode="w", encoding="utf-8")
    json.dump(payload, handle)
    handle.close()
    assert os.path.getsize(handle.name) > 0, "temp file must be non-empty"
    return handle.name


class KeysTest(unittest.TestCase):
    def test_report_key(self):
        assert report_key("hunt-20261109T010000Z") == (
            "reports/hunt-20261109T010000Z.json")
        self.assertEqual(
            report_key("hunt-20261109T010000Z"),
            "reports/hunt-20261109T010000Z.json")

    def test_state_key(self):
        assert STATE_KEY == "state/hunt-state.json"
        self.assertEqual(STATE_KEY, "state/hunt-state.json")

    def test_name_cache_key(self):
        assert NAME_CACHE_KEY == "state/name-cache.json"
        self.assertEqual(NAME_CACHE_KEY, "state/name-cache.json")


class UploadTest(unittest.TestCase):
    def test_upload_records_call(self):
        path = _temp_json({"run": "ok"})
        try:
            stub = _StubS3()
            assert upload_file(BUCKET, "reports/x.json", path, stub)
            assert len(stub.puts) == 1
            bucket, key, body = stub.puts[0]
            assert bucket == BUCKET
            assert key == "reports/x.json"
            assert json.loads(body) == {"run": "ok"}
            self.assertEqual(len(stub.puts), 1)
        finally:
            os.unlink(path)

    def test_upload_failure_returns_false(self):
        path = _temp_json({"run": "ok"})
        try:
            stub = _StubS3(fail_put=True)
            assert upload_file(BUCKET, "reports/x.json", path, stub) is False
            self.assertFalse(
                upload_file(BUCKET, "reports/x.json", path, stub))
        finally:
            os.unlink(path)

    def test_upload_missing_file_asserts(self):
        stub = _StubS3()
        with self.assertRaises(AssertionError):
            upload_file(BUCKET, "reports/x.json",
                        "/tmp/does-not-exist-xyz.json", stub)

    def test_upload_cache_records_call(self):
        path = _temp_json({"Royal Brackla 18": "royal-brackla-18yr"})
        try:
            stub = _StubS3()
            assert upload_cache(BUCKET, path, stub)
            assert len(stub.puts) == 1
            bucket, key, body = stub.puts[0]
            assert bucket == BUCKET
            assert key == NAME_CACHE_KEY
            assert json.loads(body) == {
                "Royal Brackla 18": "royal-brackla-18yr"}
            self.assertEqual(len(stub.puts), 1)
        finally:
            os.unlink(path)


class DownloadTest(unittest.TestCase):
    def test_download_writes_file(self):
        handle = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False)
        handle.close()
        try:
            stub = _StubS3()
            assert download_file(BUCKET, STATE_KEY, handle.name, stub)
            with open(handle.name, "rb") as read_handle:
                assert json.loads(read_handle.read()) == {
                    "baseline": True}
            assert stub.gets == [(BUCKET, STATE_KEY)]
            self.assertEqual(stub.gets, [(BUCKET, STATE_KEY)])
        finally:
            os.unlink(handle.name)

    def test_download_missing_returns_false(self):
        handle = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False)
        handle.close()
        try:
            stub = _StubS3(missing=True)
            assert download_file(
                BUCKET, STATE_KEY, handle.name, stub) is False
            self.assertFalse(
                download_file(BUCKET, STATE_KEY, handle.name, stub))
        finally:
            os.unlink(handle.name)


class UploadRunTest(unittest.TestCase):
    def test_upload_run_uploads_both(self):
        report_path = _temp_json({"report": 1})
        state_path = _temp_json({"state": 1})
        try:
            stub = _StubS3()
            report_ok, state_ok = upload_run(
                BUCKET, report_path, state_path,
                "hunt-20261109T010000Z", stub)
            assert report_ok is True and state_ok is True
            assert len(stub.puts) == 2
            keys = [key for _, key, _ in stub.puts]
            assert keys == ["reports/hunt-20261109T010000Z.json",
                            STATE_KEY]
            self.assertEqual(len(stub.puts), 2)
        finally:
            os.unlink(report_path)
            os.unlink(state_path)


if __name__ == "__main__":
    unittest.main()
