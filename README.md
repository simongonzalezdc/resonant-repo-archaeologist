# addon.dev-learning-archaeologist

A [ResonantOS](https://github.com/) 2.0.0-alpha add-on wrapping the [Dev Learning Archaeologist](https://github.com/KyaniteLabs/dev-learning-archaeologist) (upstream commit `fbb375b`): a tool that turns a repo's git history into evidence for a learning diagnostic.

## What it honestly does

Upstream is an Interpretable Context Methodology (ICM) specialist — a folder of markdown rules for a conversational agent, **not a CLI**. This add-on serves the honest headless subset:

- **`dla.excavate`** — deterministic **Phase 0 (ground truth)** and **Phase 1 (excavate)** over one repo's git history: commit counts verified against `git rev-list`, identity consolidation, branch-copy dedup (5-minute tolerance), batch-merge detection (3+ commits / 60s), commit-type taxonomy with scope analysis and verb fallback, hourly/daily distributions, burst-gap cycles (6h/12h gap boundaries, 4h bursts), frustration-level rework hotspots (3/5/8/12+ modifications), Co-Authored-By MER proxy.
- **`dla.contributors`** — the Phase 0 contributor table for picking one author in multi-author repos (rules.md's scoping rule); `dla.excavate` accepts an `author` filter and reports what percent of repo activity the analyzed set represents.
- **`dla.docs` / `dla.doc`** — the vendored methodology documents (identity, rules with the 5-phase pipeline and 7 vectors, signal heuristics, output schemas, HTML report spec, verified creators), byte-identical to upstream commit `fbb375b` and hash-pinned in `vendor/VENDOR-PINS.json`.

**What it does NOT do:** Phase 2-5 (era stratification, the 7 analysis vectors, the HTML report) are agent judgment work. The service computes the evidence; an agent (or you) reads the methodology via `dla.doc` and does the interpretation. The service claims neither.

## Boundaries (read before trusting it)

- **Scan-root confinement:** repos must sit under `DLA_SCAN_ROOT` (default `var/scan-root` inside the add-on). Traversal, absolute escapes, and symlink escapes are refused. It never reads the whole filesystem.
- **Read-only:** every git command reads history; nothing writes to the analyzed repo.
- **Subprocess boundary:** the service layer spawns nothing. The vendored engine (`vendor/archaeology.py`) runs `git` as a subprocess — reading history is the tool's function.
- **Privacy:** author emails are masked (`a***@e***.com`) before any output exists; home paths are redacted on disk AND in responses; service logs are content-free. Privacy scan notes: vendored docs, fixtures, and sample outputs contain no real identities or raw emails — the only real name is upstream's own "Who Made This" attribution in the vendored `README.md` (kept byte-identical per the hash-pin gate) and the MIT LICENSE copyright line.
- **Hardening:** strict per-method parameters, control-character rejection (400), body cap 64 KiB (413 + close), 30s socket timeout with 408 + close on incomplete bodies, single-flight history reads (409), exit 78 on port-bind failure.

## Run

```bash
python3 server.py                    # http-json on 127.0.0.1:4897
curl -s localhost:4897/health
curl -s -X POST localhost:4897/ -d '{"method":"dla.status"}'
```

Put (or symlink) repos to analyze under `var/scan-root/`, or point `DLA_SCAN_ROOT` at an existing directory. Dev-only port override: `DLA_PORT`.

## Gates this build passed

- vendor hash-pin vs the committed `fbb375b` state (`tests` check recorded pins always; live `git show` comparison when the upstream clone is present)
- full test suite green twice (34 tests: fixtures are 100% synthetic — invented names/emails only)
- ResonantOS validator: 0 errors AND 0 warnings (sideloaded) — `sh run-validator-check.sh <path-to-2.0.0-alpha-clone>`
- live adversarial matrix 8/8 (traversal, symlink escape, absolute escape, control chars, oversized body, incomplete body, unknown method, unknown field)
- privacy scan: no raw emails, no home paths, no real identities in vendored fixtures, outputs, or records

## License

Upstream is MIT (© 2026 Simon Gonzalez de Cruz), vendored verbatim as `vendor/methodology/LICENSE`. The add-on wrapper follows the same license.
