#!/usr/bin/env python3

import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = ROOT / "pkos" / "skills" / "getnote" / "scripts" / "getnote.sh"
ACTIVE_TEMPDIRS = set()


def cleanup_active(signum, _frame):
    for temp_dir in list(ACTIVE_TEMPDIRS):
        temp_dir.cleanup()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


class GetNoteShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for stale in Path(tempfile.gettempdir()).glob("getnote-shell-test-*"):
            shutil.rmtree(stale, ignore_errors=True)
        signal.signal(signal.SIGINT, cleanup_active)
        signal.signal(signal.SIGTERM, cleanup_active)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="getnote-shell-test-")
        ACTIVE_TEMPDIRS.add(self.temp_dir)
        self.addCleanup(self._cleanup_temp_dir)
        self.work = Path(self.temp_dir.name)
        self.log_path = self.work / "curl-log.jsonl"
        self.fake_curl = self.work / "fake_curl.py"
        self.fake_curl.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import sys

                args = sys.argv[1:]
                url = next((arg for arg in args if arg.startswith("http")), "")
                entry = {
                    "argv": args,
                    "url": url,
                    "headers": [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-H"],
                    "body": next((args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-d"), ""),
                    "data_urlencode": [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "--data-urlencode"],
                    "forms": [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-F"],
                }
                with open(os.environ["GETNOTE_FAKE_CURL_LOG"], "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry) + "\\n")

                mode = os.environ.get("GETNOTE_FAKE_CURL_MODE", "")
                if mode == "connection_failure":
                    print("fake connection failure", file=sys.stderr)
                    sys.exit(7)
                if mode == "timeout":
                    print("fake timeout", file=sys.stderr)
                    sys.exit(28)

                if "oss-upload.test" in url:
                    print('{"oss":"ok"}')
                    sys.exit(0)

                if mode == "non_2xx":
                    print('{"success":false,"error":{"code":"server","reason":"failed"}}')
                    print("500")
                    sys.exit(0)
                if mode == "invalid_json":
                    print("not-json")
                    print("200")
                    sys.exit(0)
                if mode == "success_false":
                    print('{"success":false,"error":{"code":"bad","reason":"denied"},"request_id":"req-bad"}')
                    print("200")
                    sys.exit(0)

                if "/resource/image/upload_token" in url:
                    print(json.dumps({
                        "success": True,
                        "data": {
                            "tokens": [{
                                "host": "https://oss-upload.test",
                                "key": "images/test.png",
                                "OSSAccessKeyId": "oss-key",
                                "policy": "oss-policy",
                                "signature": "oss-signature",
                                "callback": "oss-callback",
                                "access_url": "https://cdn.test/images/test.png"
                            }]
                        }
                    }))
                    print("200")
                    sys.exit(0)

                print('{"success":true,"data":{"ok":true}}')
                print("200")
                """
            ),
            encoding="utf-8",
        )
        self.fake_curl.chmod(self.fake_curl.stat().st_mode | stat.S_IXUSR)

    def _cleanup_temp_dir(self):
        ACTIVE_TEMPDIRS.discard(self.temp_dir)
        self.temp_dir.cleanup()

    def run_getnote(self, *args, mode=""):
        env = os.environ.copy()
        env.update(
            {
                "GETNOTE_API_KEY": "gk_live_test_secret",
                "GETNOTE_CLIENT_ID": "cli_test",
                "GETNOTE_CURL_BIN": str(self.fake_curl),
                "GETNOTE_FAKE_CURL_LOG": str(self.log_path),
                "GETNOTE_TIMEOUT_SECONDS": "3",
            }
        )
        if mode:
            env["GETNOTE_FAKE_CURL_MODE"] = mode
        return subprocess.run(
            ["bash", str(SCRIPT_PATH), *args],
            cwd=str(ROOT),
            env=env,
            text=True,
            capture_output=True,
        )

    def calls(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def test_auth_header_has_no_bearer(self):
        proc = self.run_getnote("list_topics", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        joined = " ".join(self.calls()[0]["headers"])
        self.assertIn("Authorization: gk_live_test_secret", joined)
        self.assertNotIn("Bearer", joined)

    def test_query_params_are_separate_for_topic_notes(self):
        proc = self.run_getnote("list_topic_notes", "abc", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        params = self.calls()[0]["data_urlencode"]
        self.assertEqual(params, ["topic_id=abc", "page=1"])
        self.assertIn("topic_id=abc&page=1", "&".join(params))

    def test_save_link_body_contains_link_note_type_and_url(self):
        proc = self.run_getnote("save_link", "https://example.com/post", "Post title", "web,pkos", "topic-1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(self.calls()[0]["body"])
        self.assertEqual(body["note_type"], "link")
        self.assertEqual(body["link_url"], "https://example.com/post")
        self.assertEqual(body["topic_id"], "topic-1")

    def test_share_note_uses_official_sharing_endpoint(self):
        proc = self.run_getnote("share_note", "note-1")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.calls()[0]["url"].endswith("/resource/note/sharing"))

    def test_follow_topic_live_body_uses_link_not_live_id(self):
        proc = self.run_getnote("follow_topic_live", "topic-1", "https://dedao.cn/live/abc")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(self.calls()[0]["body"])
        self.assertEqual(body["link"], "https://dedao.cn/live/abc")
        self.assertNotIn("live_id", body)

        invalid = self.run_getnote("follow_topic_live", "topic-1", "live-abc")
        self.assertNotEqual(invalid.returncode, 0)

    def test_get_upload_token_uses_get_endpoint_and_separate_params(self):
        proc = self.run_getnote("get_upload_token", "image/png", "2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        call = self.calls()[0]
        self.assertTrue(call["url"].endswith("/resource/image/upload_token"))
        self.assertIn("-X", call["argv"])
        self.assertEqual(call["data_urlencode"], ["mime_type=image/png", "count=2"])

    def test_upload_image_rejects_invalid_inputs_before_curl(self):
        missing = self.run_getnote("upload_image", str(self.work / "missing.png"), "image/png")
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(self.calls(), [])

        directory = self.run_getnote("upload_image", str(self.work), "image/png")
        self.assertNotEqual(directory.returncode, 0)
        self.assertEqual(self.calls(), [])

        image = self.work / "image.txt"
        image.write_text("not an image", encoding="utf-8")
        bad_mime = self.run_getnote("upload_image", str(image), "text/plain")
        self.assertNotEqual(bad_mime.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_upload_image_uses_token_fields_and_oss_curl_order(self):
        image = self.work / "image.png"
        image.write_bytes(b"png")
        proc = self.run_getnote("upload_image", str(image), "image/png")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0]["url"].endswith("/resource/image/upload_token"))
        self.assertEqual(calls[1]["url"], "https://oss-upload.test")
        self.assertEqual(
            calls[1]["forms"],
            [
                "key=images/test.png",
                "OSSAccessKeyId=oss-key",
                "policy=oss-policy",
                "signature=oss-signature",
                "callback=oss-callback",
                "Content-Type=image/png",
                f"file=@{image};type=image/png",
            ],
        )
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["image_url"], "https://cdn.test/images/test.png")

    def test_api_failure_modes_are_nonzero_without_secret_leak(self):
        cases = {
            "connection_failure": "curl failed",
            "timeout": "timeout",
            "non_2xx": "HTTP 500",
            "invalid_json": "malformed JSON",
            "success_false": "success:false",
        }
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                self.log_path.unlink(missing_ok=True)
                proc = self.run_getnote("list_topics", "1", mode=mode)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(expected, proc.stderr)
                self.assertNotIn("gk_live_test_secret", proc.stderr)


if __name__ == "__main__":
    unittest.main()
