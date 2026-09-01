"""Deterministic excavation engine for the Dev Learning Archaeologist methodology.

Upstream (github.com/KyaniteLabs/dev-learning-archaeologist @ fbb375b) is an
Interpretable Context Methodology (ICM) specialist: a folder of markdown rules
for a conversational agent, NOT a runnable CLI. The byte-identical methodology
documents live next to this file under methodology/ (hash-pinned in
VENDOR-PINS.json).

This module is this add-on's own deterministic implementation of the parts of
that methodology which are mechanically computable — Phase 0 (ground truth)
and Phase 1 (excavate) from rules.md, using the formulas and taxonomies from
reference/signal-heuristics.md. It is honest about the boundary: Phase 2-5
(era stratification, the 7 analysis vectors, the HTML report) are agent
judgment work guided by the served methodology documents; this engine claims
none of them.

BOUNDARY (documented, like delegation-bench): this module runs `git` as a
subprocess — reading history is the TOOL's function. The service layer
(server.py) spawns nothing itself. Subprocess arguments are constructed only
from the scan-root-confined repo path; no request data is ever passed to a
shell. Reads only: no git command here mutates the target repository.

Privacy: author emails are masked in every output (a***@e***.com form); raw
emails never leave this module's internal state.
"""

import hashlib
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

TOOL_NAME = "dev-learning-archaeologist"
TOOL_VERSION = "0.1.0"
SCHEMA = "dla-excavation/1"
METHODOLOGY_COMMIT = "fbb375bc938664028be44a0a02d6b783dfe32516"
METHODOLOGY_SHORT = "fbb375b"

DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "methodology")

MAX_COMMITS = 50000          # internal walk cap; reported in limits, not a request knob
MAX_MESSAGE_CHARS = 2000     # subject/body truncation per commit
GAP_HOURS_SINGLE = 6         # signal-heuristics.md: gap = 6+ hours (12+ multi-author)
GAP_HOURS_MULTI = 12
BURST_WINDOW_HOURS = 4       # burst = 2+ commits within 4 hours
BATCH_MERGE_WINDOW = timedelta(seconds=60)   # 3+ commits/60s = batch merge
BATCH_MERGE_MIN = 3
DEDUP_WINDOW = timedelta(minutes=5)          # branch dedup tolerance (Phase 0)
HOTSPOT_MAX_FILES = 50

FRUSTRATION_LEVELS = ((12, 4), (8, 3), (5, 2), (3, 1))  # mods -> level (first match wins)

_COMMIT_TYPE_RE = re.compile(
    r"^(feat|fix|docs|refactor|test|chore|style|perf|build|ci|revert)(\(|:|!)", re.I
)
_SCOPE_RE = re.compile(r"^[a-z]+(?:!.?)?\(([^)]+)\):", re.I)
_BRACKET_RE = re.compile(r"^\[([^\]]{1,24})\]")
_CO_AUTHOR_RE = re.compile(r"^co-authored-by:\s*(.*?)\s*<([^>]*)>\s*$", re.I | re.M)
_FIX_AGAIN_RE = re.compile(r"fix again|still broken|still fails|still fails|doesn't work|does not work|why\b|wtf", re.I)
_TRY_RE = re.compile(r"\btry\b|\battempt\b|\banother attempt\b", re.I)
_START_OVER_RE = re.compile(r"start over|rewrite|from scratch|rip out|tear out", re.I)
_ALL_CAPS_RE = re.compile(r"^[^a-z]*[A-Z]{4,}[^a-z]*$")

_VERB_GROUPS = {
    "exploration": ("explore", "study", "try", "experiment"),
    "refinement": ("tighten", "narrow", "calibrate", "adjust"),
    "convergence": ("lock", "finalize", "set", "apply"),
    "correction": ("repair", "fix", "reset", "revert"),
    "creation": ("build", "add", "create", "implement"),
    "infrastructure": ("make", "wire", "route", "guard", "harden", "stabilize"),
    "preservation": ("keep", "preserve", "restore", "allow"),
    "visibility": ("expose", "surface", "show", "extract", "close"),
    "verification": ("prove", "validate", "audit", "check"),
}
_BRACKET_MAP = {
    "a": "creation", "add": "creation", "f": "correction", "fix": "correction",
    "i": "infrastructure", "integrate": "infrastructure", "r": "refinement",
    "refactor": "refinement", "t": "verification", "test": "verification",
    "d": "visibility", "docs": "visibility", "c": "convergence", "chore": "convergence",
}
_AGENT_MARKER_RE = re.compile(
    r"bot|pipeline|codex|copilot|claude|devin|dependabot|github-actions|renovate"
    r"|semantic-release|cursor",
    re.I,
)
_AI_TOOL_RE = re.compile(r"claude|cursor|copilot|codex|devin|gemini|windsurf", re.I)
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class ArchaeologyError(Exception):
    """Caller-facing contract error (bad path, not a repo, git failure)."""



def _git_env():
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    return env


def _run_git(repo, args):
    cmd = ["git", "-C", repo] + args
    try:
        proc = subprocess.run(  # the TOOL's git boundary: read-only, repo confined
            cmd, capture_output=True, timeout=120, env=_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ArchaeologyError("git timed out after 120s") from exc
    except OSError as exc:
        raise ArchaeologyError("git is not available: " + str(exc)[:200]) from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ArchaeologyError("git failed: " + (detail[0][:200] if detail else "unknown error"))
    return proc.stdout.decode("utf-8", "replace")


def mcp_scan_root():
    """The confinement root. env-pinned DLA_SCAN_ROOT, default var/scan-root."""
    default = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "scan-root")
    return os.environ.get("DLA_SCAN_ROOT") or default


def resolve_repo(repo_arg):
    """Confine the requested repo under the scan root (checkyourself precedent).

    Rejects: empty/non-string, absolute escapes, `..` climbs, symlink escapes
    (realpath), missing dirs, non-repositories.
    """
    root = os.path.realpath(mcp_scan_root())
    if not isinstance(repo_arg, str) or not repo_arg.strip():
        raise ArchaeologyError("repo is required (path under DLA_SCAN_ROOT)")
    candidate = repo_arg if os.path.isabs(repo_arg) else os.path.join(root, repo_arg)
    resolved = os.path.realpath(candidate)
    if resolved != root and not resolved.startswith(root + os.sep):
        raise ArchaeologyError("repo must sit under the scan root (" + mask_home(root) + ")")
    if not os.path.isdir(resolved):
        raise ArchaeologyError("repo is not a directory under the scan root")
    dotgit = os.path.join(resolved, ".git")
    if not os.path.exists(dotgit):
        raise ArchaeologyError("repo has no .git (not a git work tree)")
    inside = _run_git(resolved, ["rev-parse", "--is-inside-work-tree"]).strip()
    if inside != "true":
        raise ArchaeologyError("repo is not a git work tree")
    return resolved



# ---------------------------------------------------------------- redaction

def mask_email(email):
    email = (email or "").strip()
    if not email or "@" not in email:
        return "u***@u***"
    local, _, domain = email.rpartition("@")
    labels = domain.split(".")
    masked_domain = ".".join(
        ((label[:1] + "***") if i == 0 else label) for i, label in enumerate(labels)
    )
    return (local[:1] or "u") + "***@" + masked_domain


def mask_home(text):
    home = os.path.expanduser("~")
    return text.replace(home, "~") if home and home != "~" else text



# ---------------------------------------------------------------- docs

def doc_names():
    names = []
    for base, _dirs, files in os.walk(DOCS_DIR):
        for name in files:
            rel = os.path.relpath(os.path.join(base, name), DOCS_DIR)
            names.append(rel.replace(os.sep, "/"))
    return sorted(names)


def read_doc(name):
    if not isinstance(name, str) or name not in doc_names():
        raise ArchaeologyError("unknown doc; call dla.docs for the registry")
    try:
        with open(os.path.join(DOCS_DIR, *name.split("/")), encoding="utf-8") as f:
            return f.read()
    except OSError as exc:  # TOCTOU/permission edge never escapes the contract
        raise ArchaeologyError("doc unreadable: " + str(exc)[:200]) from exc



# ---------------------------------------------------------------- parsing

def _parse_log(repo):
    """One pass over `git log --all`: identity + timestamp + message per commit."""
    fmt = "%H%x01%aI%x01%cI%x01%an%x01%ae%x01%cn%x01%ce%x01%B%x02"
    raw = _run_git(repo, ["log", "--all", "--date=iso-strict", "--max-count=" + str(MAX_COMMITS), "--format=" + fmt])
    commits = []
    records = raw.split("\x02")
    merged = []
    for record in records:
        record = record.lstrip("\n")
        if not record.strip():
            continue
        if merged and len(merged[-1].split("\x01")) < 8:
            # the record separator byte appeared inside a commit body: rejoin
            merged[-1] = merged[-1] + "\x02" + record
        else:
            merged.append(record)
    for record in merged:
        fields = record.split("\x01")
        if len(fields) < 8:
            continue
        chash, aiso, ciso, an, ae, cn, ce = fields[:7]
        body = "\x01".join(fields[7:])  # a \x01 inside a body belongs to the body
        body = body[:MAX_MESSAGE_CHARS]
        lines = body.splitlines()
        subject = lines[0].strip()[:MAX_MESSAGE_CHARS] if lines else ""
        try:
            at = datetime.fromisoformat(aiso)
        except ValueError:
            continue
        try:
            ct = datetime.fromisoformat(ciso)
        except ValueError:
            ct = at
        commits.append({
            "hash": chash, "author_time": at, "committer_time": ct,
            "author_name": an.strip(), "author_email": ae.strip().lower(),
            "committer_name": cn.strip(), "committer_email": ce.strip().lower(),
            "subject": subject, "body": body,
        })
    return commits


def _parse_files(repo):
    """Second pass: subject + touched files per commit (for hotspots)."""
    fmt = "%H%x01%s"
    raw = _run_git(repo, ["log", "--all", "--date=iso-strict", "--max-count=" + str(MAX_COMMITS),
                          "--format=" + fmt, "--name-only"])
    out = {}
    chash = subject = None
    files = []
    def flush():
        if chash:
            out[chash] = (subject, [f for f in files if f][:HOTSPOT_MAX_FILES * 4])
    for line in raw.splitlines():
        if "\x01" in line and len(line.split("\x01")[0]) == 40:
            flush()
            chash, _, subject = line.partition("\x01")
            subject = subject.strip()[:MAX_MESSAGE_CHARS]
            files = []
        elif line.strip() and chash:
            files.append(line.strip())
    flush()
    return out


def _parse_log_count(repo):
    out = _run_git(repo, ["rev-list", "--all", "--count"])
    return int(out.strip())



# ------------------------------------------------------- identity clustering

class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _build_clusters(commits):
    """Phase 0 identity consolidation: same email OR same normalized name = one identity."""
    uf = _Union()
    for c in commits:
        uf.find(("e", c["author_email"]))
        uf.union(("e", c["author_email"]), ("n", c["author_name"].casefold().strip()))
    for c in commits:
        for _, co_email in _CO_AUTHOR_RE.findall(c["body"]):
            co_email = co_email.strip().lower()
            if co_email:
                uf.find(("e", co_email))
    emails = {c["author_email"] for c in commits}
    pairs = {}
    for c in commits:
        pairs[(c["author_name"].strip(), c["author_email"])] = pairs.get((c["author_name"].strip(), c["author_email"]), 0) + 1
    clusters = {}
    for email in emails:
        root = uf.find(("e", email))
        clusters.setdefault(root, []).append(email)
    out = []
    for root, member_emails in clusters.items():
        names = set()
        count = 0
        for c in commits:
            if uf.find(("e", c["author_email"])) == root:
                names.add(c["author_name"].strip())
                count += 1
        display = sorted(names, key=lambda n: (-pairs.get((n, next(iter(member_emails))), 0), n))
        out.append({
            "display_name": display[0] if display else "unknown",
            "names": sorted(names),
            "emails_masked": sorted(mask_email(e) for e in member_emails),
            "commits": count,
            "agent_marker": any(_AGENT_MARKER_RE.search(n) or _AGENT_MARKER_RE.search(e)
                                for n in names for e in member_emails),
        })
    out.sort(key=lambda c: (-c["commits"], c["display_name"]))
    total = sum(c["commits"] for c in out) or 1
    for c in out:
        c["pct"] = round(100.0 * c["commits"] / total, 1)
    return out, uf


def _co_author_tools(commits):
    tools = set()
    for c in commits:
        for name, _email in _CO_AUTHOR_RE.findall(c["body"]):
            m = _AI_TOOL_RE.search(name)
            if m:
                tools.add(m.group(0).lower())
    return sorted(tools)



# ------------------------------------------------------- Phase 0 helpers

def _dedup_branch_copies(commits):
    """Phase 0: deduplicate by message + author + timestamp within 5-min tolerance."""
    kept, dropped = [], 0
    groups = {}
    for c in sorted(commits, key=lambda c: c["author_time"]):
        key = (c["subject"].strip(), c["author_email"])
        bucket = groups.setdefault(key, [])
        for prior in reversed(bucket):
            if abs(c["author_time"] - prior["author_time"]) <= DEDUP_WINDOW:
                dropped += 1
                break
        else:
            bucket.append(c)
            kept.append(c)
    return kept, dropped


def _batch_merges(commits):
    """signal-heuristics: 3+ commits, same author, within 60s = one logical commit."""
    by_author = {}
    for c in sorted(commits, key=lambda c: c["author_time"]):
        by_author.setdefault(c["author_email"], []).append(c)
    merged, groups = [], 0
    for _email, seq in by_author.items():
        run = []
        for c in seq:
            if run and (c["author_time"] - run[-1]["author_time"]) <= BATCH_MERGE_WINDOW:
                run.append(c)
            else:
                if len(run) >= BATCH_MERGE_MIN:
                    groups += 1
                    merged.append(run[0])
                elif run:
                    merged.extend(run)
                run = [c]
        if len(run) >= BATCH_MERGE_MIN:
            groups += 1
            merged.append(run[0])
        else:
            merged.extend(run)
    merged.sort(key=lambda c: c["author_time"])
    return merged, groups



# ------------------------------------------------------- Phase 1 helpers

def _classify_types(logical):
    conventional, counts = 0, {}
    scopes = {}
    for c in logical:
        m = _COMMIT_TYPE_RE.match(c["subject"])
        if m:
            conventional += 1
            t = m.group(1).lower()
            counts[t] = counts.get(t, 0) + 1
            sm = _SCOPE_RE.match(c["subject"])
            if sm:
                scope = sm.group(1).strip().lower()
                if scope and scope not in scopes:
                    scopes[scope] = c["author_time"].date().isoformat()
        else:
            counts["non-conventional"] = counts.get("non-conventional", 0) + 1
    total = len(logical) or 1
    ratio = conventional / total
    fallback = None
    if ratio < 0.5:
        fallback = _verb_fallback(logical)
    return {
        "distribution": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "conventional_ratio": round(ratio, 3),
        "scope_analysis": {
            "used": ratio > 0.8 and bool(scopes),
            "scopes_first_seen": dict(sorted(scopes.items(), key=lambda kv: kv[1])),
        },
        "verb_fallback": fallback,
    }


def _verb_fallback(logical):
    groups = {}
    for c in logical:
        subject = c["subject"]
        b = _BRACKET_RE.match(subject)
        if b:
            token = b.group(1).strip().lower()
            if _AGENT_MARKER_RE.search(token):
                g = "agent-marker"
            else:
                g = _BRACKET_MAP.get(token, "other")
        else:
            word = re.split(r"[\s:(_,]+", subject.strip(), maxsplit=1)[0].lower()
            g = "other"
            for group, verbs in _VERB_GROUPS.items():
                if word in verbs:
                    g = group
                    break
        groups[g] = groups.get(g, 0) + 1
    return {"trigger": ">50% of commits lack conventional prefixes", "groups": dict(sorted(groups.items(), key=lambda kv: -kv[1]))}


def _temporal(logical, gap_hours):
    hourly = [{"hour": h, "commits": 0} for h in range(24)]
    daily = [{"day": d, "commits": 0} for d in _DAY_NAMES]
    for c in logical:
        hourly[c["author_time"].hour]["commits"] += 1
        daily[c["author_time"].weekday()]["commits"] += 1
    total = len(logical) or 1
    for h in hourly:
        h["pct"] = round(100.0 * h["commits"] / total, 1)
    avg = total / 7.0
    for d in daily:
        d["pct"] = round(100.0 * d["commits"] / total, 1)
        d["vs_avg"] = round(d["commits"] - avg, 1) if avg else 0.0
    bursts, gaps = [], []
    run = [logical[0]] if logical else []
    for prev, cur in zip(logical, logical[1:]):
        delta = cur["author_time"] - prev["author_time"]
        if delta >= timedelta(hours=gap_hours):
            gaps.append({"type": "GAP", "start": prev["author_time"].isoformat(),
                         "end": cur["author_time"].isoformat(),
                         "days": round(delta.total_seconds() / 86400, 2), "commits": 0})
            if len(run) >= 2:
                bursts.append(run)
            run = [cur]
        else:
            if delta <= timedelta(hours=BURST_WINDOW_HOURS):
                run.append(cur)
            else:
                if len(run) >= 2:
                    bursts.append(run)
                run = [cur]
    if len(run) >= 2:
        bursts.append(run)
    burst_rows = [{
        "type": "BURST", "start": r[0]["author_time"].isoformat(), "end": r[-1]["author_time"].isoformat(),
        "days": round(max((r[-1]["author_time"] - r[0]["author_time"]).total_seconds() / 86400, 1 / 86400), 4),
        "commits": len(r),
        "velocity_commits_per_day": round(len(r) / max((r[-1]["author_time"] - r[0]["author_time"]).total_seconds() / 86400, 1 / 86400), 1),
    } for r in bursts]
    events = sorted(gaps + burst_rows, key=lambda e: e["start"])
    return {
        "gap_boundary_hours": gap_hours,
        "hourly": hourly,
        "daily": daily,
        "peak_hour": max(hourly, key=lambda h: h["commits"])["hour"] if logical else None,
        "peak_day": max(daily, key=lambda d: d["commits"])["day"] if logical else None,
        "bursts_gaps": events,
        "burst_to_gap_ratio": (round(sum(b["days"] for b in burst_rows) / sum(g["days"] for g in gaps), 2)
                                 if burst_rows and gaps else None),
    }


def _hotspots(logical, files_by_hash):
    per_file = {}
    seen = set()
    for c in logical:
        pair = files_by_hash.get(c["hash"])
        if not pair:
            continue
        _subject, files = pair
        for f in files:
            if (c["hash"], f) in seen:
                continue
            seen.add((c["hash"], f))
            slot = per_file.setdefault(f, {"commits": 0, "first": c["author_time"], "last": c["author_time"],
                                           "try_markers": 0, "fix_again_markers": 0,
                                           "start_over_markers": 0, "all_caps_subjects": 0})
            slot["commits"] += 1
            slot["first"] = min(slot["first"], c["author_time"])
            slot["last"] = max(slot["last"], c["author_time"])
            if _TRY_RE.search(c["subject"]):
                slot["try_markers"] += 1
            if _FIX_AGAIN_RE.search(c["subject"]):
                slot["fix_again_markers"] += 1
            if _START_OVER_RE.search(c["subject"]):
                slot["start_over_markers"] += 1
            if _ALL_CAPS_RE.match(c["subject"]):
                slot["all_caps_subjects"] += 1
    rows = []
    for f, slot in per_file.items():
        if slot["commits"] < 3:  # level 1 starts at 3+ modifications
            continue
        level = next((lvl for floor, lvl in FRUSTRATION_LEVELS if slot["commits"] >= floor), 1)
        rows.append({
            "file": f, "commits": slot["commits"], "level": level,
            "first_seen": slot["first"].isoformat(), "last_seen": slot["last"].isoformat(),
            "signals": {"try_attempt": slot["try_markers"], "fix_again_still_broken": slot["fix_again_markers"],
                        "start_over_rewrite": slot["start_over_markers"], "all_caps_subject": slot["all_caps_subjects"]},
        })
    rows.sort(key=lambda r: (-r["commits"], r["file"]))
    return rows[:HOTSPOT_MAX_FILES]


def _ground_truth(commits_raw, deduped, logical, groups, active_days, span_days, first, last):
    return {
        "total_commits_raw": len(commits_raw),
        "total_commits_after_dedup": len(deduped),
        "logical_commits_after_batch_merge": len(logical),
        "batch_merge_groups": groups,
        "active_days": active_days,
        "span_days": span_days,
        "active_day_ratio": round(active_days / span_days, 3) if span_days else None,
        "first_commit": first.isoformat() if first else None,
        "last_commit": last.isoformat() if last else None,
    }



# ---------------------------------------------------------------- verbs

def contributors(repo):
    """Phase 0 step 1 (multi-author scoping): the contributor table."""
    repo = resolve_repo(repo)
    commits = _parse_log(repo)
    clusters, _uf = _build_clusters(commits)
    return {
        "tool": TOOL_NAME, "schema": "dla-contributors/1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": mask_home(repo),
        "scan_root": mask_home(mcp_scan_root()),
        "methodology_commit": METHODOLOGY_SHORT,
        "total_commits": len(commits),
        "contributors": clusters,
        "notes": [
            "emails masked; identities consolidated by shared email or normalized name (rules.md Phase 0)",
            "agent/bot-looking identities flagged via name/email markers; rules.md consolidates them with the human author for analysis",
        ],
    }


def excavate(repo, author=None):
    """Deterministic Phase 0 (ground truth) + Phase 1 (excavate) over one repo."""
    repo = resolve_repo(repo)
    raw_count = _parse_log_count(repo)  # independent method, per Phase 0 checkpoint
    commits = _parse_log(repo)
    if author is not None:
        if not isinstance(author, str) or not author.strip():
            raise ArchaeologyError("author must be a non-empty string when provided")
        needle = author.strip().casefold()
        commits = [c for c in commits
                   if needle in c["author_name"].casefold() or needle in c["author_email"].casefold()]
        if not commits:
            raise ArchaeologyError("no commits match the author filter; call dla.contributors for the table")
    deduped, deduped_dropped = _dedup_branch_copies(commits)
    logical, batch_groups = _batch_merges(deduped)
    clusters, _uf = _build_clusters(deduped)
    humans = [c for c in clusters if not c["agent_marker"]]
    multi_author = len(humans) > 1
    gap_hours = GAP_HOURS_MULTI if (multi_author and author is None) else GAP_HOURS_SINGLE

    active_days = len({c["author_time"].date() for c in logical})
    first = min((c["author_time"] for c in logical), default=None)
    last = max((c["author_time"] for c in logical), default=None)
    span_days = max(((last.date() - first.date()).days + 1) if first else 0, 1) if logical else 0

    files_by_hash = _parse_files(repo)
    co_authored = sum(1 for c in logical if _CO_AUTHOR_RE.search(c["body"]))
    offsets = sorted({c["author_time"].utcoffset() for c in logical})
    truncated = sum(1 for c in commits if len(c["body"]) >= MAX_MESSAGE_CHARS)

    types = _classify_types(logical)
    temp = _temporal(logical, gap_hours)
    hotspots = _hotspots(logical, files_by_hash)

    return {
        "tool": TOOL_NAME, "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": mask_home(repo),
        "scan_root": mask_home(mcp_scan_root()),
        "author_filter": author if isinstance(author, str) else None,
        "contributor_pct_of_repo": (round(100.0 * len(commits) / raw_count, 1) if (author is not None and raw_count) else None),
        "methodology": {
            "name": "Dev Learning Archaeologist",
            "commit": METHODOLOGY_SHORT,
            "covered_here": "Phase 0 (ground truth) + Phase 1 (excavate), deterministic",
            "not_covered_here": "Phase 2-5 (eras, 7 vectors, report) are agent judgment — read the methodology via dla.docs/dla.doc",
        },
        "limits": {
            "max_commits": MAX_COMMITS, "walk_truncated": raw_count > MAX_COMMITS,
            "message_truncated_chars": MAX_MESSAGE_CHARS, "messages_truncated": truncated,
            "raw_rev_list_count": raw_count,
        },
        "ground_truth": _ground_truth(commits, deduped, logical, batch_groups, active_days, span_days, first, last),
        "identities": {
            "clusters": clusters,
            "multi_author": multi_author,
            "gap_boundary_rule": "12h (multi-author, unfiltered)" if gap_hours == GAP_HOURS_MULTI else "6h (single analyzed contributor)",
        },
        "commit_types": types,
        "temporal": temp,
        "hotspots": {
            "note": "levels are repo-wide (3/5/8/12+ modifications); era-scoped frustration is Phase 2+ agent work",
            "files": hotspots,
        },
        "coauthorship": {
            "commits_with_co_authors": co_authored,
            "mer_proxy": round(len(logical) / co_authored, 2) if co_authored else None,
            "mer_proxy_confidence": "LOW" if co_authored else None,
            "ai_tools_seen": _co_author_tools(logical),
        },
        "data_gaps": [
            "session logs not read (.claude/.cursor/Copilot): AI maturity and session frustration vectors are unavailable here",
            "external learning data (Google Takeout) not read: learning-latency vector unavailable here",
            "commit bodies truncated at %d chars for analysis" % MAX_MESSAGE_CHARS,
        ] + (["timezone artifacts: %d distinct UTC offsets in author timestamps (%s)"
              % (len(offsets), ", ".join(sorted({str(o) for o in offsets})))] if len(offsets) > 1 else []),
        "emails_are_masked": True,
    }
