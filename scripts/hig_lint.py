#!/usr/bin/env python3
"""
hig_lint.py

Dedicated Tier-1 deterministic Apple HIG linter for SwiftUI, UIKit, AppKit, and Info.plist.
Designed to provide crisp, high-signal, non-overwhelming feedback for LLMs, developers,
Xcode Build Phases, and CI pipelines.

Features:
- Zero external dependencies (Python 3 stdlib only).
- 24+ quote-verified Tier-1 static checks (regex + Swift brace-block structural checks).
- Scans `.swift` files and `Info.plist` (`UIDesignRequiresCompatibility`).
- Diff scoping (`--diff` / `--staged` / `--repo`) so only touched lines fire.
- Baseline ratcheting (`--write-baseline` / `--baseline .hig-baseline.json`) using stable
  content fingerprints so legacy repos only flag newly introduced violations.
- Inline suppression (`// hig-ignore: HIG-TYPO-001` or `// hig-ignore: ALL`).
- Output formats: `compact` (LLM-friendly, capped at `--max-findings 5`), `xcode`
  (`file:line:1: warning: ...`), and `json`.
- `--export-swiftlint` emits a ready-to-use `.swiftlint.yml` custom_rules block.

Usage:
  python3 scripts/hig_lint.py /path/to/MyApp/Sources
  python3 scripts/hig_lint.py --diff --repo /path/to/MyApp --max-findings 5
  python3 scripts/hig_lint.py /path/to/MyApp --write-baseline .hig-baseline.json
  python3 scripts/hig_lint.py /path/to/MyApp --baseline .hig-baseline.json --fail-on critical
  python3 scripts/hig_lint.py /path/to/MyApp --format xcode
  python3 scripts/hig_lint.py --export-swiftlint > hig-swiftlint-rules.yml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from hig_swift_ast import HAS_TREE_SITTER, lint_swift_ast
from hig_swift_catalog import CURATED_STATIC_RULES, DEFAULT_RULES_PATH

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------
# Swift comment stripping & brace-block structural helpers
# ---------------------------------------------------------------------------

def strip_swift_comments_preserve_lines(source: str) -> tuple[str, dict[int, set[str]]]:
    """
    Strip Swift // and nested /* ... */ comments while preserving exact line numbers.
    Extracts inline `// hig-ignore: RULE-ID` directives keyed by line number.
    """
    ignores: dict[int, set[str]] = defaultdict(set)
    out: list[str] = []
    i = 0
    n = len(source)
    line = 1
    in_string = False
    triple_string = False
    block_depth = 0

    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        if block_depth > 0:
            if ch == "/" and nxt == "*":
                block_depth += 1
                out.append("  ")
                i += 2
                continue
            if ch == "*" and nxt == "/":
                block_depth -= 1
                out.append("  ")
                i += 2
                continue
            if ch == "\n":
                line += 1
                out.append("\n")
            else:
                out.append(" ")
            i += 1
            continue

        if in_string:
            if triple_string:
                if source[i : i + 3] == '"""':
                    in_string = False
                    triple_string = False
                    out.append('"""')
                    i += 3
                    continue
            else:
                if ch == "\\" and i + 1 < n:
                    out.append(source[i : i + 2])
                    if nxt == "\n":
                        line += 1
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
            if ch == "\n":
                line += 1
            out.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "/":
            end = source.find("\n", i)
            if end == -1:
                end = n
            comment_text = source[i:end]
            im = re.search(r"hig-ignore\s*:\s*([A-Z0-9_,-]+)", comment_text, re.I)
            if im:
                for token in im.group(1).split(","):
                    tok = token.strip().upper()
                    if tok:
                        ignores[line].add(tok)
                        ignores[line + 1].add(tok)
            out.append(" " * (end - i))
            i = end
            continue

        if ch == "/" and nxt == "*":
            block_depth = 1
            out.append("  ")
            i += 2
            continue

        if source[i : i + 3] == '"""':
            in_string = True
            triple_string = True
            out.append('"""')
            i += 3
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if ch == "\n":
            line += 1
        out.append(ch)
        i += 1

    return "".join(out), dict(ignores)


def iter_brace_blocks(clean: str, opener_re: re.Pattern[str]) -> list[tuple[int, int, str]]:
    """
    Find balanced `{ ... }` blocks starting after matches of `opener_re`.
    Returns list of (start_line, end_line, block_body).
    """
    blocks: list[tuple[int, int, str]] = []
    n = len(clean)
    for m in opener_re.finditer(clean):
        brace_pos = clean.find("{", m.end() - 1)
        if brace_pos == -1 or brace_pos - m.end() > 80:
            continue
        depth = 0
        pos = brace_pos
        in_str = False
        while pos < n:
            c = clean[pos]
            if c == '"' and (pos == 0 or clean[pos - 1] != "\\"):
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        start_line = clean[:brace_pos].count("\n") + 1
                        end_line = clean[:pos].count("\n") + 1
                        blocks.append((start_line, end_line, clean[brace_pos : pos + 1]))
                        break
            pos += 1
    return blocks


# ---------------------------------------------------------------------------
# File discovery & git diff scoping
# ---------------------------------------------------------------------------

def collect_target_files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    skip_dirs = {".build", ".git", "DerivedData", "Pods", ".swiftpm", "Carthage", "node_modules"}
    for p in paths:
        if p.is_file() and (p.suffix == ".swift" or p.name == "Info.plist"):
            found.append(p.resolve())
        elif p.is_dir():
            for child in p.rglob("*"):
                if any(part in skip_dirs for part in child.parts):
                    continue
                if child.is_file() and (child.suffix == ".swift" or child.name == "Info.plist"):
                    found.append(child.resolve())
    return sorted(set(found))


def collect_git_diff_targets(repo: Path, staged: bool = False) -> dict[Path, set[int]]:
    cmd = ["git", "-C", str(repo), "diff", "--unified=0"]
    if staged:
        cmd.append("--cached")
    cmd.extend(["--", "*.swift", "*Info.plist"])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")

    changed: dict[Path, set[int]] = defaultdict(set)
    current_file: Path | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            rel = line[6:].strip()
            candidate = (repo / rel).resolve()
            current_file = candidate if candidate.exists() else None
        elif line.startswith("@@ ") and current_file is not None:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                if count > 0:
                    changed[current_file].update(range(start, start + count))
    return dict(changed)


# ---------------------------------------------------------------------------
# Baseline fingerprinting & ratcheting
# ---------------------------------------------------------------------------

def finding_fingerprint(finding: dict[str, Any], root: Path) -> str:
    """Stable content fingerprint immune to line-number shifts in untouched legacy code."""
    fpath = Path(finding["file"])
    try:
        rel = str(fpath.resolve().relative_to(root.resolve()))
    except ValueError:
        rel = fpath.name
    norm_snip = re.sub(r"\s+", " ", finding.get("snippet", "")).strip()
    raw = f"{rel}|{finding['id']}|{norm_snip}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("fingerprints", []))


def save_baseline(path: Path, findings: list[dict[str, Any]], root: Path) -> int:
    fps = sorted({finding_fingerprint(f, root) for f in findings})
    payload = {
        "schema_version": "1.0",
        "count": len(fps),
        "fingerprints": fps,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(fps)


# ---------------------------------------------------------------------------
# Core Tier-1 linting engine (shared with hig_context.py)
# ---------------------------------------------------------------------------

def _make_finding(rule: dict[str, Any], path: Path, lineno: int, snippet: str, message: str | None = None) -> dict[str, Any]:
    return {
        "id": rule["id"],
        "severity": rule["severity"],
        "file": str(path),
        "line": lineno,
        "snippet": snippet.strip()[:120],
        "message": message or rule["rule"],
        "fix": rule.get("fix_swiftui") or rule.get("fix_uikit") or "",
        "citation": rule.get("citation") or f"{rule['slug']}.md › {rule['heading']}",
        "quote": rule.get("quote", ""),
    }


def _is_ignored(rid: str, lineno: int, ignores: dict[int, set[str]]) -> bool:
    s = ignores.get(lineno, set())
    return rid in s or "ALL" in s


def lint_files(
    files_data: list[dict[str, Any]],
    rules_list: list[dict[str, Any]] | None = None,
    platforms: list[str] | None = None,
    max_findings: int | None = 5,
    baseline_fps: set[str] | None = None,
    baseline_root: Path | None = None,
    engine: str = "auto",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Run all Tier-1 static checks (tree-sitter-swift AST when available, else stdlib regex/brace engine, plus Info.plist).
    Returns (displayed_findings, summary_counts).
    """
    if engine == "ast" and not HAS_TREE_SITTER:
        raise RuntimeError("--engine ast requested, but `tree-sitter` and `tree-sitter-swift` are not installed.")
    use_ast = (engine == "ast") or (engine == "auto" and HAS_TREE_SITTER)
    active_engine = "ast" if use_ast else "regex"

    source_rules = rules_list if rules_list is not None else CURATED_STATIC_RULES
    static_rules = [r for r in source_rules if r.get("tier") == "static"]
    rules_by_id = {r["id"]: r for r in static_rules}
    rules_by_struct = {r["structural"]: r for r in static_rules if r.get("structural")}
    regex_rules = [r for r in static_rules if r.get("pattern")]

    all_findings: list[dict[str, Any]] = []

    for fdata in files_data:
        path: Path = fdata["path"]
        changed_lines: set[int] | None = fdata.get("changed_lines")

        # 1. Info.plist checks
        if path.name == "Info.plist":
            raw = fdata.get("raw") or path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(raw.splitlines(), start=1):
                if changed_lines is not None and lineno not in changed_lines:
                    continue
                if "UIDesignRequiresCompatibility" in line:
                    rule = rules_by_id.get("HIG-LG-003")
                    if rule:
                        all_findings.append(_make_finding(rule, path, lineno, line))
            continue

        # 2. Swift checks (AST backend if enabled, else stdlib regex/brace backend)
        clean: str = fdata["clean"]
        ignores: dict[int, set[str]] = fdata["ignores"]

        if use_ast:
            all_findings.extend(
                lint_swift_ast(
                    raw_source=clean,
                    path=path,
                    rules_by_id=rules_by_id,
                    ignores=ignores,
                    changed_lines=changed_lines,
                    platforms=platforms,
                )
            )
            continue

        lines = clean.splitlines()

        # 2a. Regex rules
        for rule in regex_rules:
            r_plats = rule.get("platforms", [])
            if platforms and r_plats and not any(p in r_plats for p in platforms):
                continue
            rid = rule["id"]
            if rid == "HIG-LG-003":
                continue  # Info.plist-only key
            pat = re.compile(rule["pattern"])

            if "[\\s\\S]" in rule["pattern"]:
                for m in pat.finditer(clean):
                    lineno = clean[: m.start()].count("\n") + 1
                    if changed_lines is not None and lineno not in changed_lines:
                        continue
                    if _is_ignored(rid, lineno, ignores):
                        continue
                    if rid == "HIG-A11Y-001":
                        matched_block = m.group(0)
                        end_line = clean[: m.end()].count("\n") + 1
                        after_window = "\n".join(lines[end_line : min(len(lines), end_line + 4)])
                        if "Label(" in matched_block or ".accessibilityLabel(" in matched_block or ".accessibilityLabel(" in after_window:
                            continue
                        if matched_block.count("Button") > 1:
                            continue
                    all_findings.append(_make_finding(rule, path, lineno, lines[lineno - 1]))
            else:
                for lineno, text in enumerate(lines, start=1):
                    if changed_lines is not None and lineno not in changed_lines:
                        continue
                    if _is_ignored(rid, lineno, ignores):
                        continue
                    if not pat.search(text):
                        continue
                    if rid == "HIG-MOTION-001" and "accessibilityReduceMotion" in clean:
                        continue
                    if rid == "HIG-A11Y-002":
                        # Only flag small frames on interactive views/controls (skip decorative shapes/dividers/icons)
                        prev_ctx = "\n".join(lines[max(0, lineno - 4) : lineno])
                        if not re.search(r"\b(?:Button|Toggle|Link|onTapGesture|Picker|Stepper)\b", prev_ctx):
                            continue
                    all_findings.append(_make_finding(rule, path, lineno, text))

        # 2b. Structural block checks
        # - HIG-LG-005: .glassEffect inside List / ForEach / LazyVStack
        if "glass_in_list" in rules_by_struct:
            rule = rules_by_struct["glass_in_list"]
            for start_ln, _, body in iter_brace_blocks(clean, re.compile(r"\b(?:List|ForEach|LazyVStack)\b")):
                for gm in re.finditer(r"\.glassEffect\s*\(", body):
                    lineno = start_ln + body[: gm.start()].count("\n")
                    if changed_lines is not None and lineno not in changed_lines:
                        continue
                    if not _is_ignored(rule["id"], lineno, ignores):
                        all_findings.append(_make_finding(rule, path, lineno, lines[lineno - 1]))

        # - HIG-DUO-004: Spacer() inside .toolbar / ToolbarItemGroup
        if "toolbar_spacer" in rules_by_struct:
            rule = rules_by_struct["toolbar_spacer"]
            for start_ln, _, body in iter_brace_blocks(clean, re.compile(r"(?:\.toolbar|ToolbarItemGroup)\b")):
                for sm in re.finditer(r"\bSpacer\s*\(", body):
                    lineno = start_ln + body[: sm.start()].count("\n")
                    if changed_lines is not None and lineno not in changed_lines:
                        continue
                    if not _is_ignored(rule["id"], lineno, ignores):
                        all_findings.append(_make_finding(rule, path, lineno, lines[lineno - 1]))

        # - HIG-TAB-001: TabView with > 5 tabs (without .sidebarAdaptable)
        if "too_many_tabs" in rules_by_struct and ".sidebarAdaptable" not in clean:
            rule = rules_by_struct["too_many_tabs"]
            for start_ln, _, body in iter_brace_blocks(clean, re.compile(r"\bTabView\b")):
                tab_count = max(len(re.findall(r"\bTab\s*\(", body)), len(re.findall(r"\.tabItem\s*\{", body)))
                if tab_count > 5:
                    if changed_lines is None or start_ln in changed_lines:
                        if not _is_ignored(rule["id"], start_ln, ignores):
                            all_findings.append(
                                _make_finding(
                                    rule,
                                    path,
                                    start_ln,
                                    lines[start_ln - 1],
                                    message=f"TabView defines {tab_count} tabs (> 5 causes a hidden More overflow tab on compact widths).",
                                )
                            )

        # - HIG-SHEET-001: Multi-detent sheet hiding its drag indicator
        if "sheet_hidden_grabber" in rules_by_struct:
            rule = rules_by_struct["sheet_hidden_grabber"]
            for start_ln, _, body in iter_brace_blocks(clean, re.compile(r"\.sheet\s*\(")):
                detent_m = re.search(r"\.presentationDetents\s*\(\s*\[([^\]]+)\]", body)
                hidden_m = re.search(r"\.presentationDragIndicator\s*\(\s*\.hidden\s*\)", body)
                if detent_m and hidden_m and "," in detent_m.group(1):
                    lineno = start_ln + body[: hidden_m.start()].count("\n")
                    if changed_lines is None or lineno in changed_lines:
                        if not _is_ignored(rule["id"], lineno, ignores):
                            all_findings.append(_make_finding(rule, path, lineno, lines[lineno - 1]))

        # - HIG-BTN-002: > 2 prominent buttons in one view
        prominent_lines = [
            idx
            for idx, ln in enumerate(lines, start=1)
            if re.search(r"\.buttonStyle\(\s*\.(?:borderedProminent|glassProminent)\s*\)", ln)
            and (changed_lines is None or idx in changed_lines)
        ]
        if len(prominent_lines) > 2:
            lineno = prominent_lines[2]
            if not _is_ignored("HIG-BTN-002", lineno, ignores):
                all_findings.append(
                    {
                        "id": "HIG-BTN-002",
                        "severity": "high",
                        "file": str(path),
                        "line": lineno,
                        "snippet": lines[lineno - 1].strip()[:120],
                        "message": f"Found {len(prominent_lines)} prominent buttons in one view (HIG recommends 1–2 max).",
                        "fix": "Keep prominent style on only 1–2 primary actions; use `.bordered` or `.glass` for secondary actions.",
                        "citation": "buttons.md › Style",
                        "quote": "Keep the number of prominent buttons to one or two per view.",
                    }
                )

        # - HIG-LG-004: Custom .glassEffect without accessibilityReduceTransparency
        glass_lines = [
            idx
            for idx, ln in enumerate(lines, start=1)
            if ".glassEffect(" in ln and (changed_lines is None or idx in changed_lines)
        ]
        if glass_lines and "accessibilityReduceTransparency" not in clean:
            lineno = glass_lines[0]
            if not _is_ignored("HIG-LG-004", lineno, ignores):
                all_findings.append(
                    {
                        "id": "HIG-LG-004",
                        "severity": "high",
                        "file": str(path),
                        "line": lineno,
                        "snippet": lines[lineno - 1].strip()[:120],
                        "message": "Custom `.glassEffect` used without `@Environment(\\.accessibilityReduceTransparency)` fallback.",
                        "fix": "Read `@Environment(\\.accessibilityReduceTransparency)` and provide an opaque surface when enabled.",
                        "citation": "liquid-glass.md › Review checklist",
                        "quote": "Reduce Transparency gets an opaque fallback",
                    }
                )

    # Deduplicate
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for f in all_findings:
        key = (f["id"], f["file"], f["line"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    # Apply baseline ratcheting if provided
    baselined_count = 0
    if baseline_fps is not None:
        root = baseline_root or Path.cwd()
        filtered: list[dict[str, Any]] = []
        for f in deduped:
            if finding_fingerprint(f, root) in baseline_fps:
                baselined_count += 1
            else:
                filtered.append(f)
        deduped = filtered

    deduped.sort(key=lambda x: (-SEVERITY_RANK.get(x["severity"], 0), x["file"], x["line"], x["id"]))

    total_active = len(deduped)
    if max_findings is not None and total_active > max_findings:
        displayed = deduped[:max_findings]
        suppressed = total_active - max_findings
    else:
        displayed = deduped
        suppressed = 0

    counts: dict[str, Any] = {
        "engine": active_engine,
        "total_active": total_active,
        "shown": len(displayed),
        "suppressed": suppressed,
        "baselined": baselined_count,
    }
    return displayed, counts


# ---------------------------------------------------------------------------
# Output formatters & SwiftLint export
# ---------------------------------------------------------------------------

def render_compact(findings: list[dict[str, Any]], counts: dict[str, Any], file_count: int) -> str:
    eng = counts.get("engine", "regex")
    if not findings:
        extra = f" ({counts['baselined']} baselined)" if counts["baselined"] else ""
        return f"HIG-LINT [{eng}]: 0 violations across {file_count} file(s){extra}."

    parts = [f"{counts['shown']} shown"]
    if counts["suppressed"]:
        parts.append(f"+{counts['suppressed']} lower-severity suppressed (run with --all)")
    if counts["baselined"]:
        parts.append(f"{counts['baselined']} baselined")
    lines = [f"HIG-LINT [{eng}] ({', '.join(parts)}) — {file_count} file(s):"]
    for f in findings:
        lines.append(f"  [{f['id']}][{f['severity'].upper()}] {f['file']}:{f['line']} — {f['message']}")
        if f["fix"]:
            lines.append(f"    Fix: {f['fix']}")
        lines.append(f"    Ref: {f['citation']}")
    return "\n".join(lines)


def render_xcode(findings: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for f in findings:
        level = "error" if f["severity"] == "critical" else "warning"
        fix_part = f" Fix: {f['fix']}" if f["fix"] else ""
        lines.append(f"{f['file']}:{f['line']}:1: {level}: [{f['id']}] {f['message']} ({f['citation']}){fix_part}")
    return "\n".join(lines)


def export_swiftlint_yaml() -> str:
    """Export single-line deterministic HIG rules as a SwiftLint custom_rules YAML block."""
    lines = [
        "# Generated by apple-design-skill/scripts/hig_lint.py --export-swiftlint",
        "# Merge under `custom_rules:` in your project's `.swiftlint.yml`.",
        "custom_rules:",
    ]
    for rule in CURATED_STATIC_RULES:
        pat = rule.get("pattern")
        if not pat or "[\\s\\S]" in pat or rule["id"] in ("HIG-LG-003", "HIG-A11Y-002"):
            continue
        key = rule["id"].lower().replace("-", "_")
        sev = "error" if rule["severity"] == "critical" else "warning"
        msg = f"{rule['rule']} ({rule['slug']}.md › {rule['heading']})"
        lines.extend(
            [
                f"  {key}:",
                f"    name: \"{rule['id']}\"",
                f"    regex: {json.dumps(pat)}",
                f"    message: {json.dumps(msg)}",
                f"    severity: {sev}",
            ]
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic Tier-1 Apple HIG linter for Swift projects.")
    parser.add_argument("paths", nargs="*", type=Path, help="Swift files, Info.plist, or directories to lint.")
    parser.add_argument("--diff", action="store_true", help="Lint only modified lines from `git diff`.")
    parser.add_argument("--staged", action="store_true", help="Lint only staged lines from `git diff --cached`.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository root for --diff/--staged and baseline paths.")
    parser.add_argument("--platform", choices=["ios", "ipados", "macos", "all"], default="all")
    parser.add_argument("--engine", choices=["auto", "ast", "regex"], default="auto", help="Swift parser backend: auto (tree-sitter-swift if installed, else regex), ast, or regex.")
    parser.add_argument("--max-findings", type=int, default=5, help="Max findings in compact output (default: 5).")
    parser.add_argument("--all", action="store_true", help="Show all findings without capping.")
    parser.add_argument("--format", choices=["compact", "xcode", "json"], default="compact")
    parser.add_argument("--baseline", type=Path, default=None, help="Path to .hig-baseline.json to suppress legacy findings.")
    parser.add_argument("--write-baseline", type=Path, default=None, help="Write current findings as a new baseline and exit 0.")
    parser.add_argument("--fail-on", choices=["none", "critical", "high", "medium", "low"], default="none", help="Exit 1 if any active finding meets or exceeds this severity.")
    parser.add_argument("--export-swiftlint", action="store_true", help="Print a SwiftLint custom_rules YAML snippet and exit.")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH, help="Optional hig-rules.json (falls back to built-in catalog).")
    args = parser.parse_args(argv)

    if args.export_swiftlint:
        sys.stdout.write(export_swiftlint_yaml())
        return 0

    changed_map: dict[Path, set[int]] = {}
    if args.diff or args.staged:
        changed_map = collect_git_diff_targets(args.repo.resolve(), staged=args.staged)
        target_files = sorted(changed_map.keys())
        if not target_files:
            if args.format == "json":
                print(json.dumps({"counts": {"total_active": 0, "shown": 0, "suppressed": 0, "baselined": 0}, "findings": []}, indent=2))
            elif args.format == "compact":
                print("HIG-LINT: 0 modified Swift/Info.plist files in git diff.")
            return 0
    else:
        if not args.paths:
            parser.error("Provide Swift file/dir path(s), pass --diff/--staged, or use --export-swiftlint.")
        target_files = collect_target_files(args.paths)
        if not target_files:
            print("No .swift or Info.plist files found.", file=sys.stderr)
            return 1

    files_data: list[dict[str, Any]] = []
    for path in target_files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if path.name == "Info.plist":
            files_data.append({"path": path, "raw": raw, "clean": "", "ignores": {}, "changed_lines": changed_map.get(path)})
        else:
            clean, ignores = strip_swift_comments_preserve_lines(raw)
            files_data.append({"path": path, "clean": clean, "ignores": ignores, "changed_lines": changed_map.get(path)})

    platforms = ["ios", "ipados", "macos"] if args.platform == "all" else [args.platform]
    rules_list = CURATED_STATIC_RULES
    if args.rules.exists():
        try:
            loaded = json.loads(args.rules.read_text(encoding="utf-8")).get("rules", [])
            if any(r.get("origin") == "curated" for r in loaded):
                rules_list = [r for r in loaded if r.get("tier") == "static"]
        except Exception:
            pass

    if args.write_baseline:
        all_found, _ = lint_files(files_data, rules_list=rules_list, platforms=platforms, max_findings=None, engine=args.engine)
        written = save_baseline(args.write_baseline, all_found, args.repo.resolve())
        print(f"Wrote {written} baseline fingerprint(s) to {args.write_baseline}")
        return 0

    baseline_fps = load_baseline(args.baseline) if args.baseline else None
    cap = None if (args.all or args.format in ("xcode", "json")) else args.max_findings

    # Always compute uncapped active findings for accurate --fail-on threshold checking
    active_all, _ = lint_files(
        files_data,
        rules_list=rules_list,
        platforms=platforms,
        max_findings=None,
        baseline_fps=baseline_fps,
        baseline_root=args.repo.resolve(),
        engine=args.engine,
    )
    findings, counts = lint_files(
        files_data,
        rules_list=rules_list,
        platforms=platforms,
        max_findings=cap,
        baseline_fps=baseline_fps,
        baseline_root=args.repo.resolve(),
        engine=args.engine,
    )

    if args.format == "json":
        print(json.dumps({"counts": counts, "findings": findings}, indent=2, ensure_ascii=False))
    elif args.format == "xcode":
        out = render_xcode(findings)
        if out:
            print(out)
    else:
        print(render_compact(findings, counts, len(files_data)))

    if args.fail_on != "none":
        threshold = SEVERITY_RANK[args.fail_on]
        if any(SEVERITY_RANK.get(f["severity"], 0) >= threshold for f in active_all):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
