"""addon.dev-learning-archaeologist wrapper tests — the full gate battery.

Run:  python3 -m unittest discover -s tests -v   (from the add-on root)

Fixtures are 100% synthetic: every author name/email is invented
(ada@example.com, grace@example.test, repo-pipeline[bot]). No real history,
no real identities.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_ROOT = os.path.dirname(HERE)
UPSTREAM = os.path.expanduser("~/workspaces/dev-learning-archaeologist")
sys.path.insert(0, ADDON_ROOT)
sys.path.insert(0, os.path.join(ADDON_ROOT, "vendor"))

import server  # noqa: E402
import archaeology  # noqa: E402

TEST_PORT = 4898
BASE = f"http://127.0.0.1:{TEST_PORT}"
METHODOLOGY_COMMIT = "fbb375bc938664028be44a0a02d6b783dfe32516"

ADA = ("Ada Example", "ada@example.com")
GRACE = ("Grace Sample", "grace@example.test")
BOT = ("repo-pipeline[bot]", "bot@example.test")


def post(payload=None, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    req = urllib.request.Request(BASE + "/", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status, json.loads(resp.read().decode())


def post_err(payload=None, raw=None):
    try:
        return post(payload, raw)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def _git(repo, *args, date=None, author=None, when=None):
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C"})
    if author:
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = author[0]
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = author[1]
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "-C", repo, *args], check=True, env=env, capture_output=True)


def build_fixture(root):
    """Synthetic history with known answers (dates in UTC, all invented people)."""
    repo = os.path.join(root, "fixture-repo")
    os.makedirs(repo)
    _git(repo, "init", "-b", "main")
    def commit(msg, when, files, author=ADA, body=None):
        for rel, text in files.items():
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        _git(repo, "add", "-A")
        args = ["commit", "-m", msg]
        if body:
            args = ["commit", "-m", msg, "-m", body]
        _git(repo, *args, author=author, when=when)

    commit("feat(app): initial scaffold", "2026-06-01T09:00:00+00:00", {"app/main.py": "print(1)\n", "README.md": "x\n"})
    commit("docs: add setup notes", "2026-06-01T09:30:00+00:00", {"README.md": "x2\n"})
    commit("fix(core): correct config parse", "2026-06-02T10:00:00+00:00", {"core/config.py": "c\n"}, author=GRACE)
    commit("try another approach for parser", "2026-06-02T11:00:00+00:00", {"app/main.py": "print(2)\n"})
    commit("chore: sync pipeline state a", "2026-06-03T12:00:00+00:00", {"sync/a.txt": "1\n"})
    commit("chore: sync pipeline state b", "2026-06-03T12:00:20+00:00", {"sync/b.txt": "2\n"})
    commit("chore: sync pipeline state c", "2026-06-03T12:00:40+00:00", {"sync/c.txt": "3\n"})
    commit("fix(core): still broken parse", "2026-06-04T09:00:00+00:00", {"app/main.py": "print(3)\n"})
    commit("fix(core): fix again parse edge", "2026-06-04T09:10:00+00:00", {"app/main.py": "print(4)\n"})
    commit("fix(core): why does parse fail on empty", "2026-06-04T09:20:00+00:00", {"app/main.py": "print(5)\n"})
    commit("test(core): add parser tests", "2026-06-08T10:00:00+00:00", {"core/test_config.py": "t\n"}, author=GRACE)
    commit("[codex] wire parser module", "2026-06-08T10:30:00+00:00", {"app/wire.py": "w\n"})
    commit("feat(app): expose parser api", "2026-06-08T11:00:00+00:00", {"app/wire.py": "w2\n"},
           body="Co-Authored-By: Claude <claude@anthropic.example>")
    commit("chore: pipeline sync", "2026-06-08T12:00:00+00:00", {"ci/pipeline.yml": "ci\n"}, author=BOT)
    # branch copy of commit 2 (same subject/author, 5 min later) -> Phase 0 dedup drops it
    _git(repo, "checkout", "-b", "copy")
    commit("docs: add setup notes", "2026-06-01T09:35:00+00:00", {"docs/dup.md": "d\n"})
    _git(repo, "checkout", "main")
    return repo


def build_verbs_fixture(root):
    repo = os.path.join(root, "verbs-repo")
    os.makedirs(repo)
    _git(repo, "init", "-b", "main")
    def commit(msg, when, files):
        for rel, text in files.items():
            path = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", msg, author=ADA, when=when)
    commit("explore parser options", "2026-07-01T09:00:00+00:00", {"a.txt": "1\n"})
    commit("fix edge case", "2026-07-01T10:00:00+00:00", {"a.txt": "2\n"})
    commit("[F] repair typo", "2026-07-02T09:00:00+00:00", {"b.txt": "1\n"})
    commit("study the problem", "2026-07-02T10:00:00+00:00", {"c.txt": "1\n"})
    return repo


class Service:
    def __enter__(self):
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", TEST_PORT), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        server._state.update({"busy": False, "last_excavation_id": None})


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dla-test-")
        os.environ["DLA_SCAN_ROOT"] = cls.tmp
        cls.fixture = build_fixture(cls.tmp)
        cls.verbs = build_verbs_fixture(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("DLA_SCAN_ROOT", None)
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestVendorPin(Base):  # vendor hash-pin vs the COMMITTED fbb375b state
    def test_pins_file_internally_consistent(self):
        with open(os.path.join(ADDON_ROOT, "vendor", "VENDOR-PINS.json")) as f:
            pins = json.load(f)
        self.assertEqual(pins["commit"], METHODOLOGY_COMMIT)
        for rel, meta in pins["pins"].items():
            ours = os.path.join(ADDON_ROOT, "vendor", "methodology", rel)
            self.assertTrue(os.path.exists(ours), f"missing vendored file: {rel}")
            self.assertEqual(sha256(ours), meta["sha256"], f"vendor drift vs pin: {rel}")

    def test_vendored_files_identical_to_committed_fbb375b_state(self):
        if not os.path.isdir(UPSTREAM):
            self.skipTest("upstream clone not present on this machine; recorded pins still verified")
        with open(os.path.join(ADDON_ROOT, "vendor", "VENDOR-PINS.json")) as f:
            pins = json.load(f)
        for rel in pins["pins"]:
            theirs = subprocess.run(
                ["git", "-C", UPSTREAM, "show", f"{METHODOLOGY_COMMIT}:{rel}"],
                check=True, capture_output=True).stdout
            with open(os.path.join(ADDON_ROOT, "vendor", "methodology", rel), "rb") as f:
                ours = f.read()
            self.assertEqual(sha256_bytes(ours), sha256_bytes(theirs), f"drift vs committed state: {rel}")

    def test_engine_pins_methodology_commit(self):
        self.assertEqual(archaeology.METHODOLOGY_COMMIT, METHODOLOGY_COMMIT)

    def test_license_is_verbatim_upstream_mit(self):
        with open(os.path.join(ADDON_ROOT, "vendor", "methodology", "LICENSE")) as f:
            text = f.read()
        self.assertIn("Permission is hereby granted", text)
        self.assertIn("Copyright (c) 2026", text)


class TestEngineUnits(Base):
    def test_mask_email(self):
        self.assertEqual(archaeology.mask_email("ada@example.com"), "a***@e***.com")
        self.assertEqual(archaeology.mask_email("a.b.c@mail.example"), "a***@m***.example")
        self.assertEqual(archaeology.mask_email(""), "u***@u***")
        self.assertEqual(archaeology.mask_email("no-at-sign"), "u***@u***")

    def test_frustration_levels(self):
        self.assertEqual(next(lvl for floor, lvl in archaeology.FRUSTRATION_LEVELS if 3 >= floor), 1)
        self.assertEqual(next(lvl for floor, lvl in archaeology.FRUSTRATION_LEVELS if 5 >= floor), 2)
        self.assertEqual(next(lvl for floor, lvl in archaeology.FRUSTRATION_LEVELS if 8 >= floor), 3)
        self.assertEqual(next(lvl for floor, lvl in archaeology.FRUSTRATION_LEVELS if 12 >= floor), 4)

    def test_commit_type_regex(self):
        self.assertTrue(archaeology._COMMIT_TYPE_RE.match("feat(x): y"))
        self.assertTrue(archaeology._COMMIT_TYPE_RE.match("FIX: y"))
        self.assertFalse(archaeology._COMMIT_TYPE_RE.match("fixation of the bug"))  # word boundary
        self.assertFalse(archaeology._COMMIT_TYPE_RE.match("fixed it"))


class TestExcavate(Base):
    def test_ground_truth_matches_independent_rev_list_count(self):
        raw = int(subprocess.run(["git", "-C", self.fixture, "rev-list", "--all", "--count"],
                                 check=True, capture_output=True).stdout)
        out = archaeology.excavate(self.fixture)
        self.assertEqual(out["limits"]["raw_rev_list_count"], raw)
        self.assertEqual(out["ground_truth"]["total_commits_raw"], raw)      # 15
        self.assertEqual(out["ground_truth"]["total_commits_after_dedup"], raw - 1)  # branch copy dropped
        self.assertEqual(out["ground_truth"]["batch_merge_groups"], 1)       # trio within 60s
        self.assertEqual(out["ground_truth"]["logical_commits_after_batch_merge"], raw - 1 - 2)

    def test_identities_and_gap_rule(self):
        out = archaeology.excavate(self.fixture)
        clusters = {c["display_name"]: c for c in out["identities"]["clusters"]}
        self.assertTrue(out["identities"]["multi_author"])  # Ada + Grace
        self.assertTrue(clusters["repo-pipeline[bot]"]["agent_marker"])
        self.assertFalse(clusters["Ada Example"]["agent_marker"])
        self.assertEqual(clusters["Ada Example"]["emails_masked"], ["a***@e***.com"])
        self.assertEqual(out["identities"]["gap_boundary_rule"], "12h (multi-author, unfiltered)")

    def test_temporal_bursts_and_gaps(self):
        out = archaeology.excavate(self.fixture)
        kinds = [e["type"] for e in out["temporal"]["bursts_gaps"]]
        self.assertIn("GAP", kinds)   # 4-day silence 06-04 -> 06-08 (and 25h/21h windows)
        self.assertIn("BURST", kinds) # 10-minute fix run; 10:00-11:00 trio
        self.assertEqual(out["temporal"]["gap_boundary_hours"], 12)
        self.assertGreater(out["temporal"]["burst_to_gap_ratio"], 0)

    def test_hotspot_frustration_level_and_signals(self):
        out = archaeology.excavate(self.fixture)
        by_file = {h["file"]: h for h in out["hotspots"]["files"]}
        main = by_file["app/main.py"]
        self.assertEqual(main["commits"], 5)
        self.assertEqual(main["level"], 2)  # 5+ modifications
        self.assertEqual(main["signals"]["fix_again_still_broken"], 3)
        self.assertEqual(main["signals"]["try_attempt"], 1)
        self.assertNotIn("app/wire.py", by_file)  # only 2 modifications -> below hotspot floor

    def test_commit_types_and_scope(self):
        out = archaeology.excavate(self.fixture)
        dist = out["commit_types"]["distribution"]
        for t in ("feat", "fix", "docs", "test", "chore"):
            self.assertIn(t, dist)
        self.assertGreater(out["commit_types"]["conventional_ratio"], 0.5)
        self.assertIsNone(out["commit_types"]["verb_fallback"])

    def test_verb_fallback_and_bracket_mapping(self):
        out = archaeology.excavate(self.verbs)
        fb = out["commit_types"]["verb_fallback"]
        self.assertIsNotNone(fb)
        self.assertEqual(fb["groups"].get("exploration"), 2)
        self.assertEqual(fb["groups"].get("correction"), 2)

    def test_coauthorship_mer_proxy_and_tools(self):
        out = archaeology.excavate(self.fixture)
        self.assertGreaterEqual(out["coauthorship"]["commits_with_co_authors"], 1)
        self.assertIsNotNone(out["coauthorship"]["mer_proxy"])
        self.assertIn("claude", out["coauthorship"]["ai_tools_seen"])
        self.assertEqual(out["coauthorship"]["mer_proxy_confidence"], "LOW")

    def test_author_filter_scopes_and_reports_pct(self):
        out = archaeology.excavate(self.fixture, author="ada")
        self.assertEqual(out["author_filter"], "ada")
        self.assertEqual(out["contributor_pct_of_repo"], 80.0)  # 12 of 15 raw commits
        self.assertFalse(out["identities"]["multi_author"])
        self.assertEqual(out["identities"]["gap_boundary_rule"], "6h (single analyzed contributor)")

    def test_author_filter_no_match_is_contract_error(self):
        with self.assertRaises(archaeology.ArchaeologyError):
            archaeology.excavate(self.fixture, author="nobody-here")

    def test_control_bytes_in_commit_body_do_not_drop_commits(self):
        repo = os.path.join(self.tmp, "ctrl-repo")
        os.makedirs(repo, exist_ok=True)
        _git(repo, "init", "-b", "main")
        env = dict(os.environ)
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": ADA[0], "GIT_AUTHOR_EMAIL": ADA[1],
                    "GIT_COMMITTER_NAME": ADA[0], "GIT_COMMITTER_EMAIL": ADA[1],
                    "GIT_AUTHOR_DATE": "2026-05-01T09:00:00+00:00",
                    "GIT_COMMITTER_DATE": "2026-05-01T09:00:00+00:00"})
        proc = subprocess.run(["git", "-C", repo, "commit", "--allow-empty", "-F", "-"],
                              input=b"feat: subject with control bytes\x01\x02 in body\n",
                              env=env, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = archaeology.excavate(repo)
        self.assertEqual(out["ground_truth"]["total_commits_raw"], 1)  # body control bytes must not split records

    def test_contributors_table(self):
        out = archaeology.contributors(self.fixture)
        names = {c["display_name"]: c for c in out["contributors"]}
        self.assertEqual(names["Ada Example"]["commits"], 12)  # includes branch copy (raw view)
        self.assertEqual(names["Grace Sample"]["commits"], 2)
        self.assertEqual(names["repo-pipeline[bot]"]["commits"], 1)
        self.assertTrue(names["repo-pipeline[bot]"]["agent_marker"])
        for c in out["contributors"]:
            for email in c["emails_masked"]:
                self.assertIn("***", email)


class TestConfinement(Base):
    def test_traversal_rejected(self):
        for bad in ("../outside", "..", "fixture-repo/../../.."):
            with self.assertRaises(archaeology.ArchaeologyError, msg=bad):
                archaeology.resolve_repo(bad)

    def test_absolute_outside_rejected(self):
        with self.assertRaises(archaeology.ArchaeologyError):
            archaeology.resolve_repo(os.path.expanduser("~"))

    def test_symlink_escape_rejected(self):
        target = tempfile.mkdtemp(prefix="dla-escape-")
        try:
            _git(target, "init", "-b", "main")
            link = os.path.join(self.tmp, "innocent-link")
            os.symlink(target, link)
            with self.assertRaises(archaeology.ArchaeologyError):
                archaeology.resolve_repo("innocent-link")
        finally:
            shutil.rmtree(target, ignore_errors=True)

    def test_non_repo_dir_rejected(self):
        plain = os.path.join(self.tmp, "plain-dir")
        os.makedirs(plain, exist_ok=True)
        with self.assertRaises(archaeology.ArchaeologyError):
            archaeology.resolve_repo("plain-dir")

    def test_fixture_repo_resolves(self):
        resolved = archaeology.resolve_repo("fixture-repo")
        self.assertTrue(resolved.startswith(os.path.realpath(self.tmp) + os.sep))


class TestService(Base):
    def test_status_roundtrip(self):
        with Service():
            code, body = post({"method": "dla.status"})
            self.assertEqual(code, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["version"], archaeology.TOOL_VERSION)
            self.assertEqual(body["methodology_commit"], "fbb375b")
            self.assertFalse(body["busy"])
            self.assertIsNone(body["last_excavation_id"])
            self.assertIn("scan_root", body)

    def test_get_health(self):
        with Service():
            with urllib.request.urlopen(BASE + "/health", timeout=10) as resp:
                body = json.loads(resp.read().decode())
            self.assertTrue(body["ok"])

    def test_full_excavate_lifecycle(self):
        with Service():
            code, body = post({"method": "dla.excavate", "params": {"repo": "fixture-repo"}})
            self.assertEqual(code, 200)
            self.assertEqual(body["schema"], "dla-excavation/1")
            self.assertIn("excavation_id", body)
            self.assertIn("record_path", body)
            self.assertEqual(body["ground_truth"]["total_commits_raw"], 15)
            with open(os.path.join(ADDON_ROOT, body["record_path"])) as f:
                record = json.load(f)
            self.assertEqual(record["ground_truth"]["total_commits_raw"], 15)
            serialized = json.dumps(body) + json.dumps(record)
            self.assertNotIn(os.path.expanduser("~"), serialized)  # home-path redaction on disk AND response
            self.assertNotIn("ada@example.com", serialized)        # raw emails never surface
            self.assertIn("a***@e***.com", serialized)             # masked form does
            os.remove(os.path.join(ADDON_ROOT, body["record_path"]))  # keep var/ clean after the test

    def test_contributors_endpoint(self):
        with Service():
            code, body = post({"method": "dla.contributors", "params": {"repo": "fixture-repo"}})
            self.assertEqual(code, 200)
            self.assertEqual(body["schema"], "dla-contributors/1")
            self.assertEqual(len(body["contributors"]), 3)

    def test_docs_registry_and_read(self):
        with Service():
            code, body = post({"method": "dla.docs"})
            self.assertEqual(code, 200)
            self.assertIn("rules.md", body["docs"])
            self.assertEqual(len(body["docs"]), 12)
            code, doc = post({"method": "dla.doc", "params": {"name": "rules.md"}})
            self.assertEqual(code, 200)
            with open(os.path.join(ADDON_ROOT, "vendor", "methodology", "rules.md")) as f:
                vendored = f.read()
            self.assertEqual(doc["content"], vendored)  # served verbatim

    def test_unknown_doc_is_400(self):
        with Service():
            code, body = post_err({"method": "dla.doc", "params": {"name": "../../etc/passwd"}})
            self.assertEqual(code, 400)
            code, body = post_err({"method": "dla.doc", "params": {"name": "not-a-doc.md"}})
            self.assertEqual(code, 400)

    def test_out_of_boundary_repo_is_400(self):
        with Service():
            for bad in ("../outside", os.path.expanduser("~"), "plain-dir"):
                code, body = post_err({"method": "dla.excavate", "params": {"repo": bad}})
                self.assertEqual(code, 400, bad)
                self.assertIn("error", body)

    def test_single_flight_returns_409(self):
        event = threading.Event()
        original = archaeology.excavate

        def slow(**kw):
            event.wait(timeout=15)
            return original(**kw)

        archaeology.excavate = slow
        try:
            with Service():
                results = {}

                def first():
                    results["a"] = post({"method": "dla.excavate", "params": {"repo": "fixture-repo"}})
                t = threading.Thread(target=first)
                t.start()
                time.sleep(0.5)
                code, body = post_err({"method": "dla.excavate", "params": {"repo": "fixture-repo"}})
                self.assertEqual(code, 409)
                event.set()
                t.join(timeout=20)
                self.assertEqual(results["a"][0], 200)
        finally:
            archaeology.excavate = original

    def test_error_matrix(self):
        with Service():
            self.assertEqual(post_err({"method": "dla.nope"})[0], 404)
            self.assertEqual(post_err({"method": "dla.excavate", "extra": 1})[0], 400)
            self.assertEqual(post_err({"method": "dla.excavate", "params": {"repo": "x", "nope": 1}})[0], 400)
            self.assertEqual(post_err({"method": "dla.excavate"})[0], 400)  # params defaults to {}
            self.assertEqual(post_err({"method": "dla.excavate", "params": {"repo": ""}})[0], 400)
            self.assertEqual(post_err({"method": "dla.excavate", "params": {"repo": "a\x02b"}})[0], 400)  # control chars
            self.assertEqual(post_err({"method": "dla.excavate", "params": {"repo": 7}})[0], 400)
            self.assertEqual(post_err(raw=b"not json")[0], 400)
            self.assertEqual(post_err(raw=b"")[0], 400)
            self.assertEqual(post_err(raw=b"[1,2]")[0], 400)

    def test_oversized_body_413_and_close(self):
        with Service():
            code, body = post_err({"method": "dla.status"}, raw=b'{"x":"' + b"a" * (64 * 1024) + b'"}')
            self.assertEqual(code, 413)

    def test_short_body_is_400_not_hang(self):
        with Service():
            sock = socket.create_connection(("127.0.0.1", TEST_PORT), timeout=10)
            try:
                sock.sendall(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 50\r\n\r\n1234567890")
                sock.shutdown(socket.SHUT_WR)
                data = sock.recv(65536)
                self.assertIn(b"400", data.split(b"\r\n")[0])
            finally:
                sock.close()

    def test_incomplete_body_times_out_408(self):
        original_timeout = server.Handler.timeout
        server.Handler.timeout = 1
        try:
            with Service():
                sock = socket.create_connection(("127.0.0.1", TEST_PORT), timeout=10)
                try:
                    sock.sendall(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 50\r\n\r\n1")
                    data = sock.recv(65536)
                    self.assertIn(b"408", data.split(b"\r\n")[0])
                finally:
                    sock.close()
        finally:
            server.Handler.timeout = original_timeout


if __name__ == "__main__":
    unittest.main()
