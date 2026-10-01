#!/usr/bin/env python3
"""addon.dev-learning-archaeologist local-service entry (http-json on 127.0.0.1:4897).

ResonantOS add-on contract: protocol http-json, healthCommand dla.status.

Upstream (KyaniteLabs/dev-learning-archaeologist @ fbb375b) is an ICM
methodology folder for conversational agents, not a CLI. This service exposes
its honest headless surface:

  dla.status        health + boundary report
  dla.contributors  Phase 0 step 1: contributor table for one repo
  dla.excavate      Phase 0 + Phase 1, deterministic, over one repo
  dla.docs          registry of the vendored methodology documents
  dla.doc           read one vendored methodology document (verbatim fbb375b)

BOUNDARY (documented like delegation-bench): the service layer spawns nothing.
The vendored engine (vendor/archaeology.py) runs `git` as a subprocess —
reading history is the TOOL's function. Repos are confined to
DLA_SCAN_ROOT (default var/scan-root under this add-on; never the whole
filesystem — checkyourself precedent). Reads only: nothing here modifies the
analyzed repository.

Hardening: strict per-method parameters (no union allowlists — the sweep
finding), control-character rejection, body <= 64 KiB, 30s socket timeout
with 408+close on incomplete bodies, 413+close on oversized bodies, and
home-path redaction on disk AND in responses. Author emails are masked by
the engine before any output exists.

Exit codes: 0 normal stop; 78 port bind failure.
"""

import json
import os
import re
import socket
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import archaeology  # noqa: E402  (Phase 0/1 engine; owns the git subprocess boundary)

PORT = int(os.environ.get("DLA_PORT", "4897"))  # dev override; manifest port 4897 is the contract
MAX_BODY = 64 * 1024
MAX_STR = 2048
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

ADDON_ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_BASE = os.path.join(ADDON_ROOT, "var")

_state = {"busy": False, "last_excavation_id": None}
_lock = threading.Lock()


def _check_string(name, value):
    if not isinstance(value, str) or not (0 < len(value) <= MAX_STR):
        return f"{name} must be a non-empty string of at most {MAX_STR} characters"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return f"{name} contains control characters"
    return None


def _validate_params(method, params):
    """Strict per-method validation (the sweep finding: no union allowlists)."""
    if not isinstance(params, dict):
        return None, "params must be an object"
    per_method = {
        "dla.status": set(),
        "dla.contributors": {"repo"},
        "dla.excavate": {"repo", "author"},
        "dla.docs": set(),
        "dla.doc": {"name"},
    }
    allowed = per_method.get(method)
    if allowed is None:
        return None, f"unknown method: {method}"
    for key in params:
        if key not in allowed:
            return None, f"unknown field: {key}"

    if method == "dla.excavate":
        repo = params.get("repo")
        err = _check_string("repo", repo)
        if err:
            return None, err
        author = params.get("author")
        if author is not None:
            err = _check_string("author", author)
            if err:
                return None, err
        return {"repo": repo, "author": author}, None

    if method == "dla.contributors":
        repo = params.get("repo")
        err = _check_string("repo", repo)
        if err:
            return None, err
        return {"repo": repo}, None

    if method == "dla.doc":
        name = params.get("name")
        err = _check_string("name", name)
        if err:
            return None, err
        return {"name": name}, None

    return {}, None


def _redact_text(text):
    home = os.path.expanduser("~")
    return text.replace(home, "~") if home and home != "~" else text


def _redact_obj(obj):
    if isinstance(obj, str):
        return _redact_text(obj)
    if isinstance(obj, list):
        return [_redact_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _redact_obj(value) for key, value in obj.items()}
    return obj


def _persist(result):
    """Write the redacted excavation record under var/ and return its addon-relative path."""
    run_id = "excavate-" + uuid.uuid4().hex[:8]
    out_dir = os.path.join(OUT_BASE, run_id)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "excavation.json")
    with open(path, "w") as f:
        json.dump(_redact_obj(result), f, indent=1)
    return run_id, os.path.relpath(path, ADDON_ROOT)


def _history_call(kind, args):
    """Run one history-reading engine call under the single-flight gate."""
    with _lock:
        if _state["busy"]:
            return {"error": "a history read is already in progress", "last_excavation_id": _state["last_excavation_id"]}, 409
        _state["busy"] = True
    try:
        result = getattr(archaeology, kind)(**args)  # ArchaeologyError = caller's problem (400)
        if kind == "excavate":
            run_id, record_path = _persist(result)
            result = dict(result)
            result["excavation_id"] = run_id
            result["record_path"] = record_path
            with _lock:
                _state["last_excavation_id"] = run_id
        return _redact_obj(result), 200
    except archaeology.ArchaeologyError as exc:
        return _redact_obj({"error": str(exc)}), 400
    except Exception as exc:  # honest failure, never a server crash
        return _redact_obj({"error": kind + " failed: " + str(exc)[:300]}), 500
    finally:
        with _lock:
            _state["busy"] = False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30  # a lying Content-Length must not pin a thread forever

    def _reply(self, code, payload, close=False):
        if close:
            self.close_connection = True  # never leave undrained bodies on a keep-alive connection
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if close:
            self.send_header("Connection", "close")  # advertise the close (413/408/bad-frame paths)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._reply(200, self._status())
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/":
            self._reply(404, {"error": "not found"}, close=True)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, {"error": "bad content-length"}, close=True)
            return
        if length <= 0 or length > MAX_BODY:
            self._reply(413 if length > MAX_BODY else 400, {"error": "body must be 1..65536 bytes"}, close=True)
            return
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except (TimeoutError, socket.timeout, OSError):
            self._reply(408, {"error": "request body incomplete (timeout)"}, close=True)
            return
        except (ValueError, UnicodeDecodeError):
            self._reply(400, {"error": "body must be valid JSON"}, close=True)
            return
        if not isinstance(req, dict):
            self._reply(400, {"error": "body must be a JSON object"}, close=True)
            return
        method = req.get("method")
        params = req.get("params", {})
        for key in req:
            if key not in ("method", "params"):
                self._reply(400, {"error": f"unknown field: {key}"}, close=True)
                return
        if method == "dla.status":
            self._reply(200, self._status())
        elif method in ("dla.contributors", "dla.excavate"):
            args, err = _validate_params(method, params)
            if err:
                self._reply(400, {"error": err})
                return
            payload, code = _history_call(method.removeprefix("dla."), args)
            self._reply(code, payload)
        elif method == "dla.docs":
            args, err = _validate_params(method, params)
            if err:
                self._reply(400, {"error": err})
                return
            self._reply(200, _redact_obj(self._docs()))
        elif method == "dla.doc":
            args, err = _validate_params(method, params)
            if err:
                self._reply(400, {"error": err})
                return
            try:
                content = archaeology.read_doc(args["name"])
            except archaeology.ArchaeologyError as exc:
                self._reply(400, {"error": str(exc)})
                return
            self._reply(200, _redact_obj({
                "tool": archaeology.TOOL_NAME,
                "methodology_commit": archaeology.METHODOLOGY_SHORT,
                "name": args["name"],
                "content": content,
            }))
        else:
            self._reply(404, {"error": f"unknown method: {method}"})

    def _docs(self):
        return {
            "tool": archaeology.TOOL_NAME,
            "methodology_commit": archaeology.METHODOLOGY_SHORT,
            "docs": archaeology.doc_names(),
            "note": "byte-identical vendored methodology documents (rules.md Phase 2-5 guide the agent-side analysis; dla.excavate supplies the Phase 0-1 evidence)",
        }

    def _status(self):
        with _lock:
            return _redact_obj({
                "ok": True,
                "tool": archaeology.TOOL_NAME,
                "version": archaeology.TOOL_VERSION,
                "methodology_commit": archaeology.METHODOLOGY_SHORT,
                "busy": _state["busy"],
                "last_excavation_id": _state["last_excavation_id"],
                "scan_root": str(archaeology.mcp_scan_root()),
            })

    def log_message(self, fmt, *args):  # keep service logs quiet and content-free
        sys.stderr.write("dla-service: " + (fmt % args) + "\n")


def main():
    try:  # first-run UX: the default scan root may not exist yet
        os.makedirs(archaeology.mcp_scan_root(), exist_ok=True)
    except OSError:
        pass  # honest error surfaces later if the boundary is unusable
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        sys.stderr.write(f"dla-service: cannot bind 127.0.0.1:{PORT} ({exc}); manifest entrypoint expects this port\n")
        return 78
    sys.stderr.write(f"dla-service: listening on http://127.0.0.1:{PORT}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
