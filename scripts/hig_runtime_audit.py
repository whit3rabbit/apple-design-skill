#!/usr/bin/env python3
"""
hig_runtime_audit.py

Tier-3 Runtime Verification Bridge for Xcode's `XCUIApplication().performAccessibilityAudit()`.
Parses either an `.xcresult` bundle (via `xcrun xcresulttool get test-results`) or raw
`xcodebuild test` log output, maps Apple's runtime accessibility audit failures to verified
HIG rules and SwiftUI/UIKit fixes, and emits compact, non-overwhelming diagnostics for LLMs.

Zero external dependencies (Python 3 stdlib only).

Usage:
  # Print a drop-in XCTest snippet for any iOS/macOS UI test target
  python3 scripts/hig_runtime_audit.py --print-xctest

  # Parse an Xcode .xcresult bundle produced by `xcodebuild test -resultBundlePath ...`
  python3 scripts/hig_runtime_audit.py --xcresult /path/to/TestResults.xcresult --max-findings 5

  # Parse xcodebuild test stdout/log file
  python3 scripts/hig_runtime_audit.py /path/to/xcodebuild.log --format compact
  xcodebuild test ... | python3 scripts/hig_runtime_audit.py --stdin --fail-on critical
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

# Mapping from Apple's XCUIAccessibilityAuditType / Xcode failure messages -> HIG rule & fix
AUDIT_CATEGORY_MAP: list[dict[str, Any]] = [
    {
        "id": "HIG-RT-CONTRAST",
        "category": "contrast",
        "severity": "critical",
        "patterns": [
            re.compile(r"\bcontrast\b", re.I),
            re.compile(r"color contrast ratio", re.I),
        ],
        "citation": "accessibility.md › Vision",
        "quote": "Strive to meet color contrast minimum standards.",
        "fix": "Ensure >= 4.5:1 contrast for text <= 17 pt (>= 3:1 for >= 18 pt or bold) in Light, Dark, and Increase Contrast modes; prefer semantic system colors.",
    },
    {
        "id": "HIG-RT-HIT",
        "category": "hitRegion",
        "severity": "critical",
        "patterns": [
            re.compile(r"\bhit\s*(?:area|region|target)\b", re.I),
            re.compile(r"too small to tap|minimum size|44\s*[x×]\s*44", re.I),
        ],
        "citation": "accessibility.md › Mobility",
        "quote": "Offer sufficiently sized controls.",
        "fix": "Expand the interactive target to 44x44 pt on iOS/iPadOS (min 28x28 pt) or 28x28 pt on macOS (min 20x20 pt) using `.frame(minWidth:minHeight:)` and `.contentShape(Rectangle())`.",
    },
    {
        "id": "HIG-RT-CLIP",
        "category": "textClipped",
        "severity": "high",
        "patterns": [
            re.compile(r"\btext\s*(?:is\s*)?clipped\b|\btruncat(?:ed|ion)\b", re.I),
            re.compile(r"clipping\b", re.I),
        ],
        "citation": "typography.md › Supporting Dynamic Type",
        "quote": "Keep text truncation to a minimum as font size increases.",
        "fix": "Remove fixed `.lineLimit(1)` / fixed frame heights, allow multi-line wrapping, or switch to stacked layout via `ViewThatFits` / `@Environment(\\.dynamicTypeSize)`.",
    },
    {
        "id": "HIG-RT-DYNTYPE",
        "category": "dynamicType",
        "severity": "critical",
        "patterns": [
            re.compile(r"\bdynamic\s*type\b", re.I),
            re.compile(r"font\s*size.*(?:unsupported|does not scale|fixed)", re.I),
        ],
        "citation": "typography.md › Supporting Dynamic Type",
        "quote": "Make sure your app’s layout adapts to all font sizes.",
        "fix": "Replace fixed point sizes with semantic text styles (`.font(.body)`), `Font.custom(..., relativeTo:)`, or `@ScaledMetric` (UIKit: `adjustsFontForContentSizeCategory = true`).",
    },
    {
        "id": "HIG-RT-LABEL",
        "category": "sufficientElementDescription",
        "severity": "critical",
        "patterns": [
            re.compile(r"(?:no|missing|insufficient|empty|duplicate)\s+(?:accessibility\s+)?(?:description|label)", re.I),
            re.compile(r"element\s+has\s+no\s+description|element\s+detection|voiceover", re.I),
        ],
        "citation": "accessibility.md › Vision",
        "quote": "Describe your app’s interface and content for VoiceOver.",
        "fix": "Use `Label(\"Title\", systemImage: \"...\")` or attach `.accessibilityLabel(\"...\")` describing the control's purpose (not its visual shape).",
    },
    {
        "id": "HIG-RT-TRAIT",
        "category": "trait",
        "severity": "high",
        "patterns": [
            re.compile(r"\btrait(?:s)?\b|parent\s*/?\s*child|contained\s+element|potentially\s+inaccessible", re.I),
            re.compile(r"\baction\b.*accessibility|not\s+accessible", re.I),
        ],
        "citation": "voiceover.md › Best practices",
        "quote": "Describe your app’s interface and content for VoiceOver.",
        "fix": "Use standard interactive controls (`Button`, `Toggle`, `Link`) or add `.accessibilityAddTraits(.isButton)` / `.accessibilityElement(children: .combine)`.",
    },
]

FALLBACK_CATEGORY = {
    "id": "HIG-RT-A11Y",
    "category": "accessibilityAudit",
    "severity": "high",
    "citation": "accessibility.md › Vision",
    "quote": "Conduct testing and verify that your app or game supports these technologies, and that your interface elements are appropriately labeled to ensure a great experience.",
    "fix": "Inspect the flagged element in Xcode Accessibility Inspector and verify Dynamic Type, contrast, hit target size, and VoiceOver labels.",
}

XCTEST_SNIPPET = """// Drop into your project's UITests target (iOS 17+ / macOS 14+ / Xcode 15+)
// Run via:
//   xcodebuild test -scheme YourApp -destination 'platform=iOS Simulator,name=iPhone 16' \\
//     -resultBundlePath /tmp/HIGAudit.xcresult
// Parse into compact LLM diagnostics:
//   python3 scripts/hig_runtime_audit.py --xcresult /tmp/HIGAudit.xcresult --max-findings 5

import XCTest

final class HIGAccessibilityAuditTests: XCTestCase {
    @MainActor
    func testPrimaryScreensAccessibilityAudit() throws {
        let app = XCUIApplication()
        app.launch()

        // Runs Apple's runtime checks for contrast, Dynamic Type, hit region,
        // text clipping, VoiceOver labels, and traits on the live rendered UI.
        try app.performAccessibilityAudit(for: .all) { issue in
            // Return true to ignore known/intentional issues; false to report.
            return false
        }
    }
}
"""


def classify_audit_message(message: str) -> dict[str, Any]:
    for entry in AUDIT_CATEGORY_MAP:
        if any(p.search(message) for p in entry["patterns"]):
            return entry
    return FALLBACK_CATEGORY


def _is_audit_failure_text(text: str) -> bool:
    return bool(
        re.search(
            r"performAccessibilityAudit|Accessibility Audit|contrast|Hit\s+(?:area|region)|Dynamic\s+Type|"
            r"Text\s+clipped|Element\s+has\s+no\s+description|Sufficient\s+Element\s+Description|"
            r"Element\s+Detection|Potentially\s+inaccessible|XCUIAccessibilityAudit",
            text,
            re.I,
        )
    )


def parse_raw_log(text: str) -> list[dict[str, Any]]:
    """Parse `xcodebuild test` / XCTest console output for accessibility audit failures."""
    findings: list[dict[str, Any]] = []
    # Match typical XCTest failure lines with file:line and/or audit description
    file_line_re = re.compile(r"(/[^\s:]+\.swift|[A-Za-z0-9_./-]+\.swift):(\d+):\s*(?:error:\s*)?(.*)")
    audit_issue_re = re.compile(
        r"(?:Assertion Failure(?:\s+at\s+([^:]+):(\d+))?:\s*|failed\s*-\s*|Issue:\s*)(.+)",
        re.I,
    )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or not _is_audit_failure_text(line):
            continue

        file_ref = "runtime-ui"
        line_no = 0
        detail = line

        fm = file_line_re.search(line)
        if fm:
            file_ref = fm.group(1)
            line_no = int(fm.group(2))
            detail = fm.group(3).strip() or line
        else:
            am = audit_issue_re.search(line)
            if am:
                if am.group(1):
                    file_ref = am.group(1).strip()
                if am.group(2):
                    line_no = int(am.group(2))
                detail = am.group(3).strip()

        cat = classify_audit_message(detail)
        findings.append(
            {
                "id": cat["id"],
                "category": cat["category"],
                "severity": cat["severity"],
                "file": file_ref,
                "line": line_no,
                "message": detail[:220],
                "fix": cat["fix"],
                "citation": cat["citation"],
                "quote": cat["quote"],
            }
        )

    return _dedupe_and_sort(findings)


def _walk_xcresult_json(obj: Any, test_name: str = "") -> list[dict[str, Any]]:
    """Recursively collect failure messages from xcresulttool JSON output."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        cur_name = str(obj.get("name") or obj.get("testName") or test_name)
        for key in ("failureText", "message", "detailedDescription", "title"):
            val = obj.get(key)
            if isinstance(val, str) and _is_audit_failure_text(val):
                loc_file = str(obj.get("sourceFile") or obj.get("file") or cur_name or "runtime-ui")
                loc_line = int(obj.get("lineNumber") or obj.get("line") or 0)
                cat = classify_audit_message(val)
                found.append(
                    {
                        "id": cat["id"],
                        "category": cat["category"],
                        "severity": cat["severity"],
                        "file": loc_file,
                        "line": loc_line,
                        "test": cur_name,
                        "message": val.strip()[:220],
                        "fix": cat["fix"],
                        "citation": cat["citation"],
                        "quote": cat["quote"],
                    }
                )
        for v in obj.values():
            found.extend(_walk_xcresult_json(v, cur_name))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_walk_xcresult_json(item, test_name))
    return found


def parse_xcresult(bundle_path: Path) -> list[dict[str, Any]]:
    """Query an `.xcresult` bundle using Apple's modern `xcrun xcresulttool get test-results`."""
    if not bundle_path.exists():
        raise FileNotFoundError(f"xcresult bundle not found: {bundle_path}")

    findings: list[dict[str, Any]] = []
    for subcmd in ("summary", "tests"):
        cmd = ["xcrun", "xcresulttool", "get", "test-results", subcmd, "--path", str(bundle_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        try:
            data = json.loads(proc.stdout)
            findings.extend(_walk_xcresult_json(data))
        except json.JSONDecodeError:
            findings.extend(parse_raw_log(proc.stdout))

    return _dedupe_and_sort(findings)


def _dedupe_and_sort(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for f in findings:
        norm_msg = re.sub(r"\s+", " ", f["message"]).strip().lower()
        key = (f["id"], norm_msg)
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    deduped.sort(key=lambda x: (-SEVERITY_RANK.get(x["severity"], 0), x["id"], x["file"], x["line"]))
    return deduped


def render_compact(findings: list[dict[str, Any]], total: int, source_label: str) -> str:
    if not findings:
        return f"HIG-RUNTIME-AUDIT: 0 accessibility audit failures in {source_label}."
    sup = max(0, total - len(findings))
    hdr = f"HIG-RUNTIME-AUDIT ({len(findings)} shown" + (f", +{sup} suppressed (run with --all)" if sup else "") + f") — {source_label}:"
    lines = [hdr]
    for f in findings:
        loc = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
        lines.append(f"  [{f['id']}][{f['severity'].upper()}] {loc} — {f['message']}")
        lines.append(f"    Fix: {f['fix']}")
        lines.append(f"    Ref: {f['citation']}")
    return "\n".join(lines)


def render_xcode(findings: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for f in findings:
        level = "error" if f["severity"] == "critical" else "warning"
        line_no = f["line"] or 1
        lines.append(f"{f['file']}:{line_no}:1: {level}: [{f['id']}] {f['message']} ({f['citation']}) Fix: {f['fix']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse Xcode performAccessibilityAudit() results into compact HIG diagnostics.")
    parser.add_argument("log_file", nargs="?", type=Path, help="Path to xcodebuild test log file.")
    parser.add_argument("--xcresult", type=Path, default=None, help="Path to an Xcode .xcresult bundle.")
    parser.add_argument("--stdin", action="store_true", help="Read xcodebuild test output from stdin.")
    parser.add_argument("--print-xctest", action="store_true", help="Print a drop-in XCTest performAccessibilityAudit() Swift template and exit.")
    parser.add_argument("--max-findings", type=int, default=5, help="Max findings in compact output (default: 5).")
    parser.add_argument("--all", action="store_true", help="Show all findings without capping.")
    parser.add_argument("--format", choices=["compact", "xcode", "json"], default="compact")
    parser.add_argument("--fail-on", choices=["none", "critical", "high", "medium", "low"], default="none")
    args = parser.parse_args(argv)

    if args.print_xctest:
        sys.stdout.write(XCTEST_SNIPPET)
        return 0

    if args.xcresult:
        all_findings = parse_xcresult(args.xcresult.resolve())
        label = args.xcresult.name
    elif args.stdin:
        all_findings = parse_raw_log(sys.stdin.read())
        label = "stdin"
    elif args.log_file:
        all_findings = parse_raw_log(args.log_file.read_text(encoding="utf-8", errors="replace"))
        label = str(args.log_file)
    else:
        parser.error("Provide a log file, --xcresult <bundle>, --stdin, or --print-xctest.")

    cap = None if (args.all or args.format in ("xcode", "json")) else args.max_findings
    displayed = all_findings if cap is None else all_findings[:cap]

    if args.format == "json":
        print(
            json.dumps(
                {
                    "counts": {"total": len(all_findings), "shown": len(displayed), "suppressed": len(all_findings) - len(displayed)},
                    "findings": displayed,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.format == "xcode":
        out = render_xcode(displayed)
        if out:
            print(out)
    else:
        print(render_compact(displayed, len(all_findings), label))

    if args.fail_on != "none":
        threshold = SEVERITY_RANK[args.fail_on]
        if any(SEVERITY_RANK.get(f["severity"], 0) >= threshold for f in all_findings):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
