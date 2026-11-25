#!/usr/bin/env python3
"""
hig_context.py

Universal UI File Scanner, JIT Context Router, and HIG JSON Search CLI.
Queries the compiled HIG knowledge graph (`rules/graph.json`) and quote-verified
rule registry (`rules/hig-rules.json`) so an LLM NEVER needs to read the 1.5 MB
JSON file directly or load 30k+ tokens of raw Markdown.

Supports five composable modes:
  1. File/dir scanner (Swift, Flutter, React Native, Tauri/Electron/Web):
       `hig_context.py path/to/SettingsView.swift --scan`
       `hig_context.py path/to/home_screen.dart --scan`
  2. Git diff hunks:
       `hig_context.py --diff --repo path/to/App --check-static`
  3. Free-text keyword search:
       `hig_context.py --query "destructive button sheet" --severity critical,high`
  4. Direct page slug lookup:
       `hig_context.py --slug accessibility,layout,color --no-fixes`
  5. Direct Apple/framework API lookup:
       `hig_context.py --api NavigationSplitView,.toolbar --platform macos`

Zero external dependencies (Python 3 stdlib only).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from hig_lint import (
    lint_files as run_hig_lint_files,
    strip_swift_comments_preserve_lines,
)
from hig_swift_catalog import (
    CROSS_PLATFORM_API_TO_SLUGS,
    DEFAULT_GRAPH_PATH,
    DEFAULT_RULES_PATH,
    estimate_tokens,
)

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
ORIGIN_BONUS = {"curated": 3.0, "llm": 2.5, "deterministic": 1.0}
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "my", "of", "on", "or", "rules",
    "should", "that", "the", "this", "to", "use", "using", "what", "when", "with",
}

UI_FILE_EXTENSIONS = {".swift", ".dart", ".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte", ".html", ".css", ".scss"}


# ---------------------------------------------------------------------------
# Comment stripping, framework detection & API scanning (with line numbers)
# ---------------------------------------------------------------------------

def detect_file_stack(path: Path, source: str) -> str:
    ext = path.suffix.lower()
    if ext == ".swift":
        return "swift"
    if ext == ".dart":
        return "flutter"
    if ext in (".tsx", ".jsx", ".ts", ".js"):
        if re.search(r"from\s+['\"](?:react-native|@react-navigation|expo-|@stripe/stripe-react-native)", source):
            return "react-native"
        return "web-desktop"
    return "web-desktop"


def detect_frameworks_and_platforms(
    path: Path,
    clean_source: str,
    stack: str,
    platform_override: str | None,
) -> tuple[list[str], list[str]]:
    if stack == "flutter":
        fws = ["Flutter"]
        default_plats = ["ios", "ipados"]
    elif stack == "react-native":
        fws = ["React Native"]
        default_plats = ["ios", "ipados"]
    elif stack == "web-desktop":
        fws = ["Tauri/Electron" if re.search(r"electron|tauri|window-vibrancy|BrowserWindow", clean_source, re.I) else "Web/Desktop"]
        default_plats = ["macos"] if fws == ["Tauri/Electron"] else ["ios", "ipados", "macos"]
    else:
        imports = set(re.findall(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_]*)", clean_source, re.M))
        fws = [
            fw
            for fw in (
                "SwiftUI", "UIKit", "AppKit", "WidgetKit", "PassKit", "Charts",
                "MapKit", "StoreKit", "AuthenticationServices", "AVKit", "ActivityKit", "AppIntents",
            )
            if fw in imports
        ] or ["Swift"]
        plats_set: set[str] = set()
        if "UIKit" in imports or re.search(r"#if\s+os\(iOS\)", clean_source):
            plats_set.update({"ios", "ipados"})
        if "AppKit" in imports or re.search(r"#if\s+os\(macOS\)", clean_source):
            plats_set.add("macos")
        default_plats = sorted(plats_set) if plats_set else ["ios", "ipados", "macos"]

    if platform_override and platform_override != "all":
        return fws, [platform_override]
    if platform_override == "all":
        return fws, ["ios", "ipados", "macos"]
    return fws, default_plats


def extract_apis_with_lines(
    clean_source: str,
    stack: str,
    swift_indexed_apis: set[str],
) -> tuple[list[str], dict[str, list[int]]]:
    """
    Scan source line-by-line and return (sorted_apis, {api: [line_numbers]}).
    Swift uses `rules/graph.json`'s 908 indexed APIs; Flutter/RN/Web use CROSS_PLATFORM_API_TO_SLUGS.
    """
    api_lines: dict[str, list[int]] = defaultdict(list)

    if stack == "swift":
        lines = clean_source.splitlines()
        for lineno, line in enumerate(lines, start=1):
            code_line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
            line_hits: set[str] = set()
            for m in re.finditer(r"(\.[A-Za-z_][A-Za-z0-9_]*)", code_line):
                tok = m.group(1)
                if tok in swift_indexed_apis:
                    line_hits.add(tok)
                elif tok[1:] in swift_indexed_apis:
                    line_hits.add(tok[1:])
            for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b", code_line):
                if m.group(1) in swift_indexed_apis:
                    line_hits.add(m.group(1))
            for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", code_line):
                if m.group(1) in swift_indexed_apis:
                    line_hits.add(m.group(1))
            for hit in line_hits:
                api_lines[hit].append(lineno)
        return sorted(api_lines.keys()), dict(api_lines)

    # Cross-platform stacks (Flutter, React Native, Tauri/Electron/Web)
    # Match against the detected stack plus web-desktop CSS/ARIA tokens when JS/TS/HTML/CSS
    candidate_maps = [CROSS_PLATFORM_API_TO_SLUGS.get(stack, {})]
    if stack == "react-native":
        candidate_maps.append(CROSS_PLATFORM_API_TO_SLUGS["web-desktop"])
    lookup_keys = sorted({k for cmap in candidate_maps for k in cmap}, key=len, reverse=True)

    lines = clean_source.splitlines()
    for lineno, line in enumerate(lines, start=1):
        for key in lookup_keys:
            if key[0].isalpha() and key[-1].isalnum() and "." not in key and "-" not in key:
                if re.search(rf"\b{re.escape(key)}\b", line):
                    api_lines[key].append(lineno)
            else:
                if key in line:
                    api_lines[key].append(lineno)

    return sorted(api_lines.keys()), dict(api_lines)


def tokenize_query(query: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_.+-]+", query.lower())
    tokens = [t.strip(".-") for t in raw_tokens if len(t.strip(".-")) >= 2]
    filtered = [t for t in tokens if t not in STOP_WORDS]
    return filtered or tokens


def lookup_api_slugs(api: str, graph_api_index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Look up HIG slugs for either a Swift/Apple API or a cross-platform equivalency API."""
    entries = graph_api_index.get(api, [])
    if not entries and not api.startswith("."):
        entries = graph_api_index.get("." + api, [])
    if not entries and api.startswith("."):
        entries = graph_api_index.get(api[1:], [])
    if entries:
        return entries

    # Fallback: cross-platform equivalency dictionary
    for stack_map in CROSS_PLATFORM_API_TO_SLUGS.values():
        if api in stack_map:
            return [{"slug": s, "weight": 3 if i == 0 else 2, "origin": "cross-platform"} for i, s in enumerate(stack_map[api])]
    return []


# ---------------------------------------------------------------------------
# Git diff scoping across all supported UI files
# ---------------------------------------------------------------------------

def collect_git_diff_ui_files(repo: Path, staged: bool = False) -> dict[Path, set[int]]:
    cmd = ["git", "-C", str(repo), "diff", "--unified=0"]
    if staged:
        cmd.append("--cached")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()}")

    changed: dict[Path, set[int]] = defaultdict(set)
    current_file: Path | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            rel = line[6:].strip()
            candidate = (repo / rel).resolve()
            current_file = candidate if candidate.exists() and candidate.suffix.lower() in UI_FILE_EXTENSIONS else None
        elif line.startswith("@@ ") and current_file is not None:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                if count > 0:
                    changed[current_file].update(range(start, start + count))
    return dict(changed)


# ---------------------------------------------------------------------------
# Graph routing (APIs, slugs, and keyword queries) + 1-hop expansion
# ---------------------------------------------------------------------------

def route_hig_nodes(
    graph: dict[str, Any],
    platforms: list[str],
    matched_apis: list[str] | None = None,
    explicit_slugs: list[str] | None = None,
    query_terms: list[str] | None = None,
    hops: int = 1,
    max_nodes: int = 8,
) -> list[dict[str, Any]]:
    api_index = graph.get("api_index", {})
    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])

    direct_scores: dict[str, float] = defaultdict(float)
    slug_triggers: dict[str, list[str]] = defaultdict(list)

    for raw_slug in explicit_slugs or []:
        slug = raw_slug.strip().removesuffix(".md").lower()
        if slug in nodes:
            direct_scores[slug] += 12.0
            slug_triggers[slug].append(f"slug:{slug}")

    for api in matched_apis or []:
        for entry in lookup_api_slugs(api, api_index):
            slug = entry["slug"]
            node = nodes.get(slug)
            if not node:
                continue
            if platforms and not any(p in node.get("platforms", []) for p in platforms):
                continue
            direct_scores[slug] += float(entry.get("weight", 1))
            if api not in slug_triggers[slug]:
                slug_triggers[slug].append(api)

    if query_terms:
        phrase = " ".join(query_terms)
        for slug, node in nodes.items():
            if platforms and not any(p in node.get("platforms", []) for p in platforms):
                continue
            title_l = node.get("title", "").lower()
            slug_l = slug.replace("-", " ")
            abstract_l = node.get("abstract", "").lower()
            headings_l = " ".join(node.get("headings", [])).lower()
            apis_l = " ".join(node.get("swift_apis", [])).lower()

            score = 0.0
            hits: list[str] = []
            if phrase and (phrase in title_l or phrase in slug_l):
                score += 10.0
            for term in query_terms:
                if term == slug_l or term in title_l.split():
                    score += 5.0
                    hits.append(term)
                elif term in title_l or term in slug_l:
                    score += 3.5
                    hits.append(term)
                elif term in headings_l:
                    score += 2.2
                    hits.append(term)
                elif term in apis_l:
                    score += 2.0
                    hits.append(term)
                elif term in abstract_l:
                    score += 1.2
                    hits.append(term)
            if score > 0:
                direct_scores[slug] += score
                for h in hits:
                    if h not in slug_triggers[slug]:
                        slug_triggers[slug].append(h)

    hop_scores: dict[str, float] = defaultdict(float)
    hop_from: dict[str, set[str]] = defaultdict(set)
    if hops >= 1 and direct_scores:
        out_edges: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for e in edges:
            out_edges[e["source"]].append((e["target"], float(e.get("weight", 1))))

        top_direct = sorted(direct_scores.items(), key=lambda x: -x[1])[:6]
        for src, base_score in top_direct:
            for tgt, ew in out_edges.get(src, []):
                if tgt in direct_scores:
                    continue
                node = nodes.get(tgt)
                if not node:
                    continue
                if platforms and not any(p in node.get("platforms", []) for p in platforms):
                    continue
                hop_scores[tgt] += min(base_score, 8.0) * 0.18 * ew
                hop_from[tgt].add(src)

    ranked: list[dict[str, Any]] = []
    for slug, sc in direct_scores.items():
        node = nodes[slug]
        ranked.append(
            {
                "slug": slug,
                "title": node["title"],
                "file": node["file"],
                "est_tokens": node["est_tokens"],
                "hop": 0,
                "score": round(sc, 2),
                "triggers": slug_triggers[slug][:8],
            }
        )
    for slug, sc in hop_scores.items():
        node = nodes[slug]
        ranked.append(
            {
                "slug": slug,
                "title": node["title"],
                "file": node["file"],
                "est_tokens": node["est_tokens"],
                "hop": 1,
                "score": round(sc, 2),
                "via": sorted(hop_from[slug]),
                "triggers": [],
            }
        )

    ranked.sort(key=lambda x: (x["hop"], -x["score"], x["slug"]))
    return ranked[:max_nodes]


# ---------------------------------------------------------------------------
# Rule ranking & strict token-budget packing (flat + component-grouped)
# ---------------------------------------------------------------------------

def score_rule_query_match(rule: dict[str, Any], query_terms: list[str]) -> float:
    if not query_terms:
        return 0.0
    rule_l = rule.get("rule", "").lower()
    heading_l = rule.get("heading", "").lower()
    quote_l = rule.get("quote", "").lower()
    anti_l = rule.get("anti_pattern", "").lower()
    fix_l = (rule.get("fix_swiftui", "") + " " + rule.get("fix_uikit", "")).lower()
    apis_l = " ".join(rule.get("swiftui_apis", []) + rule.get("uikit_apis", []) + rule.get("appkit_apis", [])).lower()

    phrase = " ".join(query_terms)
    score = 0.0
    if phrase and (phrase in rule_l or phrase in quote_l):
        score += 8.0

    matched_terms = 0
    for term in query_terms:
        hit = False
        if term in rule_l:
            score += 4.0
            hit = True
        if term in heading_l:
            score += 2.5
            hit = True
        if term in apis_l:
            score += 3.0
            hit = True
        if term in quote_l or term in anti_l or term in fix_l:
            score += 1.5
            hit = True
        if hit:
            matched_terms += 1

    if len(query_terms) > 1 and matched_terms == len(query_terms):
        score += 5.0
    return score


def select_rules_within_budget(
    ranked_nodes: list[dict[str, Any]],
    matched_apis: set[str],
    query_terms: list[str],
    rules_doc: dict[str, Any],
    platforms: list[str],
    allowed_tiers: set[str],
    allowed_severities: set[str],
    budget_tokens: int,
    include_fixes: bool = True,
    max_per_slug: int = 3,
) -> tuple[list[dict[str, Any]], int, int]:
    node_map = {n["slug"]: n for n in ranked_nodes}
    candidates: list[tuple[float, dict[str, Any]]] = []

    for rule in rules_doc.get("rules", []):
        slug = rule.get("slug")
        if rule.get("tier") not in allowed_tiers:
            continue
        if rule.get("severity") not in allowed_severities:
            continue
        r_plats = rule.get("platforms", [])
        if platforms and r_plats and not any(p in r_plats for p in platforms):
            continue

        q_score = score_rule_query_match(rule, query_terms)
        node_info = node_map.get(slug)
        if node_info is None and q_score < 4.0:
            continue

        rule_apis = set(rule.get("swiftui_apis", []) + rule.get("uikit_apis", []) + rule.get("appkit_apis", []))
        api_overlap = len(rule_apis & matched_apis)

        node_bonus = (10.0 if node_info["hop"] == 0 else 4.0) if node_info else 2.0
        node_score_term = min(node_info["score"], 10.0) * 0.35 if node_info else 0.0

        score = (
            q_score * 1.6
            + node_bonus
            + SEVERITY_RANK.get(rule.get("severity", "medium"), 1) * 2.5
            + ORIGIN_BONUS.get(rule.get("origin", "deterministic"), 1.0) * 1.5
            + min(api_overlap, 3) * 2.5
            + node_score_term
        )
        candidates.append((score, rule))

    candidates.sort(key=lambda x: (-x[0], x[1]["id"]))

    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    per_slug_count: dict[str, int] = defaultdict(int)
    used_tokens = 110

    def try_add(rule: dict[str, Any], cap: int) -> bool:
        nonlocal used_tokens
        if rule["id"] in seen_ids or per_slug_count[rule["slug"]] >= cap:
            return False
        tok = estimate_tokens(format_rule_compact(rule, include_fixes=include_fixes))
        if used_tokens + tok > budget_tokens and selected:
            return False
        selected.append(rule)
        seen_ids.add(rule["id"])
        per_slug_count[rule["slug"]] += 1
        used_tokens += tok
        return True

    cap_p1 = max_per_slug if len(node_map) == 1 else 1
    for _, rule in candidates:
        try_add(rule, cap=cap_p1)
    cap_p2 = 12 if len(node_map) == 1 else max_per_slug
    for _, rule in candidates:
        if used_tokens >= budget_tokens:
            break
        try_add(rule, cap=cap_p2)

    return selected, used_tokens, len(candidates)


def build_component_groups(
    api_locations: dict[str, list[str]],
    graph: dict[str, Any],
    rules_doc: dict[str, Any],
    platforms: list[str],
    allowed_tiers: set[str],
    allowed_severities: set[str],
    budget_tokens: int,
    include_fixes: bool = True,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Group best-practice rules directly under each detected component/API + line number,
    strictly bounded by `budget_tokens`.
    """
    api_index = graph.get("api_index", {})
    rules_by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_candidates = 0

    for r in rules_doc.get("rules", []):
        if r.get("tier") not in allowed_tiers or r.get("severity") not in allowed_severities:
            continue
        r_plats = r.get("platforms", [])
        if platforms and r_plats and not any(p in r_plats for p in platforms):
            continue
        rules_by_slug[r["slug"]].append(r)

    # Sort rules within each slug by severity + origin
    for slug in rules_by_slug:
        rules_by_slug[slug].sort(
            key=lambda r: (
                -SEVERITY_RANK.get(r.get("severity", "medium"), 1),
                -ORIGIN_BONUS.get(r.get("origin", "deterministic"), 1.0),
                r["id"],
            )
        )

    # Rank detected APIs by their highest mapping weight, then first occurrence
    scored_apis: list[tuple[int, str, list[str], list[str]]] = []
    for api, locs in api_locations.items():
        entries = lookup_api_slugs(api, api_index)
        slugs = [e["slug"] for e in entries if e["slug"] in rules_by_slug]
        if not slugs:
            continue
        top_weight = max(e.get("weight", 1) for e in entries)
        scored_apis.append((top_weight, api, locs, slugs))
        total_candidates += sum(len(rules_by_slug[s]) for s in slugs)

    scored_apis.sort(key=lambda x: (-x[0], x[1]))

    groups: list[dict[str, Any]] = []
    seen_rule_ids: set[str] = set()
    used_tokens = 110

    # Two passes: pass 1 gives every detected API 1 rule for breadth; pass 2 adds a 2nd rule while budget remains
    api_bucket: dict[str, dict[str, Any]] = {}

    for pass_limit in (1, 2):
        for _, api, locs, slugs in scored_apis:
            if used_tokens >= budget_tokens:
                break
            bucket = api_bucket.get(api)
            if bucket and len(bucket["rules"]) >= pass_limit:
                continue

            # Prefer rules that explicitly mention this API or belong to its primary slug
            primary_slug = slugs[0]
            pool = rules_by_slug[primary_slug] + [r for s in slugs[1:2] for r in rules_by_slug[s]]
            pool_sorted = sorted(
                pool,
                key=lambda r: (
                    0 if api in (r.get("swiftui_apis", []) + r.get("uikit_apis", []) + r.get("appkit_apis", [])) else 1,
                    0 if r["slug"] == primary_slug else 1,
                    -SEVERITY_RANK.get(r.get("severity", "medium"), 1),
                    -ORIGIN_BONUS.get(r.get("origin", "deterministic"), 1.0),
                    r["id"],
                ),
            )
            for rule in pool_sorted:
                if rule["id"] in seen_rule_ids:
                    continue
                header_cost = 14 if bucket is None else 0
                tok = header_cost + estimate_tokens(format_rule_compact(rule, include_fixes=include_fixes))
                if used_tokens + tok > budget_tokens and api_bucket:
                    break
                if bucket is None:
                    bucket = {"api": api, "locations": locs[:3], "slugs": slugs[:2], "rules": []}
                    api_bucket[api] = bucket
                    groups.append(bucket)
                bucket["rules"].append(rule)
                seen_rule_ids.add(rule["id"])
                used_tokens += tok
                break

    return groups, used_tokens, total_candidates


# ---------------------------------------------------------------------------
# Formatting (compact LLM-native, markdown, json)
# ---------------------------------------------------------------------------

def format_rule_compact(rule: dict[str, Any], include_fixes: bool = True) -> str:
    sev = rule["severity"].upper()
    parts = [f"- [{rule['id']}][{sev}] {rule['rule']} ({rule['citation']})"]
    if include_fixes:
        fix = (rule.get("fix_swiftui") or rule.get("fix_uikit") or "").strip()
        if fix:
            one_line = re.sub(r"\s+", " ", fix)
            if len(one_line) > 160:
                one_line = one_line[:157].rstrip() + "..."
            parts.append(f"  Fix: {one_line}")
    return "\n".join(parts)


def render_compact_output(payload: dict[str, Any], include_fixes: bool = True) -> str:
    lines: list[str] = []
    meta = payload["meta"]
    fw_str = ", ".join(meta["frameworks"]) or "UI"
    lines.append(
        f"HIG JIT CONTEXT | frameworks: {fw_str} | platforms: {', '.join(meta['platforms'])} | "
        f"~{meta['card_tokens_est']} tok (vs ~{meta['raw_hig_tokens_est']:,} raw, {meta['savings_pct']}% saved)"
    )
    if meta.get("query"):
        lines.append(f"Query: {meta['query']!r}")
    if meta.get("cross_platform_note"):
        lines.append("Translate Apple terms using `references/cross-platform.md` (SwiftUI fixes omitted via --no-fixes).")

    if payload.get("static_findings") is not None:
        sf = payload["static_findings"]
        sup = payload.get("static_suppressed", 0)
        lines.append("")
        if sf:
            hdr = f"STATIC CHECKS ({len(sf)} shown" + (f", +{sup} suppressed" if sup else "") + "):"
            lines.append(hdr)
            for f in sf:
                lines.append(f"  [{f['id']}][{f['severity'].upper()}] {f['file']}:{f['line']} — {f['message']}")
                if f["fix"]:
                    lines.append(f"    Fix: {f['fix']}")
                lines.append(f"    Ref: {f['citation']}")
        else:
            lines.append("STATIC CHECKS: 0 Tier-1 violations.")

    if payload.get("groups") is not None:
        lines.append("")
        lines.append(f"DETECTED COMPONENTS & BEST PRACTICES ({len(payload['groups'])} components, budget={meta['budget_tokens']} tok):")
        for grp in payload["groups"]:
            loc_str = ", ".join(grp["locations"])
            slug_str = ", ".join(f"{s}.md" for s in grp["slugs"])
            hdr = f"{grp['api']} ({loc_str}) -> {slug_str}" if loc_str else f"{grp['api']} -> {slug_str}"
            lines.append(f"  [{hdr}]")
            for r in grp["rules"]:
                for rline in format_rule_compact(r, include_fixes=include_fixes).splitlines():
                    lines.append("  " + rline)
    else:
        if meta["matched_apis"]:
            lines.append(f"Matched APIs ({len(meta['matched_apis'])}): {', '.join(meta['matched_apis'][:18])}")
        lines.append("")
        lines.append(f"RULES ({len(payload['rules'])} of {meta['candidate_rules']} matched, budget={meta['budget_tokens']} tok):")
        for r in payload["rules"]:
            lines.append(format_rule_compact(r, include_fixes=include_fixes))

    if payload["nodes"]:
        lines.append("")
        node_refs = [f"{n['file']} ({'direct' if n['hop'] == 0 else '1-hop'})" for n in payload["nodes"]]
        lines.append("Open raw HIG only if spec tables/edge nuance needed: " + ", ".join(node_refs))
    return "\n".join(lines)


def render_markdown_output(payload: dict[str, Any], include_fixes: bool = True) -> str:
    meta = payload["meta"]
    lines = [
        "## Apple HIG JIT Rule Card",
        "",
        f"- **Frameworks:** {', '.join(meta['frameworks']) or 'UI'} | **Platforms:** {', '.join(meta['platforms'])}",
        f"- **Context budget:** ~{meta['card_tokens_est']} tok ({meta['savings_pct']}% saved vs ~{meta['raw_hig_tokens_est']:,} raw HIG)",
    ]
    if meta.get("query"):
        lines.append(f"- **Query:** `{meta['query']}`")
    lines.append("")

    if payload.get("groups") is not None:
        for grp in payload["groups"]:
            loc_str = f" ({', '.join(grp['locations'])})" if grp["locations"] else ""
            lines.append(f"### `{grp['api']}`{loc_str}")
            for r in grp["rules"]:
                lines.append(f"- **`{r['id']}` [{r['severity'].upper()}]** {r['rule']} (`{r['citation']}`)")
                if include_fixes:
                    fix = r.get("fix_swiftui") or r.get("fix_uikit")
                    if fix:
                        lines.append(f"  - **Fix:** {fix}")
            lines.append("")
    else:
        lines.append(f"### Applicable HIG Rules ({len(payload['rules'])})")
        lines.append("")
        for r in payload["rules"]:
            lines.append(f"- **`{r['id']}` [{r['severity'].upper()} / {r['tier']}]** {r['rule']} (`{r['citation']}`)")
            if include_fixes:
                fix = r.get("fix_swiftui") or r.get("fix_uikit")
                if fix:
                    lines.append(f"  - **Fix:** {fix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def collect_ui_files(paths: list[Path]) -> list[Path]:
    skip_dirs = {".build", ".git", "DerivedData", "Pods", ".swiftpm", "node_modules", "build", ".dart_tool"}
    found: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix.lower() in UI_FILE_EXTENSIONS:
            found.append(p.resolve())
        elif p.is_dir():
            for f in p.rglob("*"):
                if any(part in skip_dirs for part in f.parts):
                    continue
                if f.is_file() and f.suffix.lower() in UI_FILE_EXTENSIONS:
                    found.append(f.resolve())
    return sorted(set(found))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan UI files or query compiled Apple HIG graph & rules into a compact rule card.")
    parser.add_argument("paths", nargs="*", type=Path, help="UI files or directories (.swift, .dart, .tsx, .jsx, .vue, .css, etc.).")
    parser.add_argument("--diff", action="store_true", help="Analyze only changed UI files/lines from `git diff`.")
    parser.add_argument("--staged", action="store_true", help="Analyze only staged UI files/lines from `git diff --cached`.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Git repository root for --diff/--staged.")
    parser.add_argument("--query", "-q", default=None, help="Free-text keyword search over HIG graph & rules (no file needed).")
    parser.add_argument("--slug", default=None, help="Comma-separated HIG page slugs (e.g. sheets,buttons,modality).")
    parser.add_argument("--api", default=None, help="Comma-separated Apple or Flutter/RN/Web APIs (e.g. TabView,BackdropFilter).")
    parser.add_argument("--group-by", choices=["flat", "component"], default="flat", help="Group output as a flat prioritized list or grouped under each detected component/API.")
    parser.add_argument("--scan", action="store_true", help="Shorthand for `--group-by component` (component-by-component file scan).")
    parser.add_argument("--platform", choices=["ios", "ipados", "macos", "all"], default=None, help="Target platform filter.")
    parser.add_argument("--tier", default="static,semantic", help="Comma-separated tiers: static,semantic,visual,all.")
    parser.add_argument("--severity", default="critical,high,medium,low", help="Comma-separated severities: critical,high,medium,low.")
    parser.add_argument("--budget", type=int, default=850, help="Max token budget for the rule card (default: 850).")
    parser.add_argument("--hops", type=int, choices=[0, 1], default=1, help="Graph expansion hops (0 or 1, default: 1).")
    parser.add_argument("--max-nodes", type=int, default=8, help="Maximum HIG graph nodes to route to.")
    parser.add_argument("--no-fixes", action="store_true", help="Omit SwiftUI/UIKit Fix lines (auto-enabled for non-Swift files).")
    parser.add_argument("--with-fixes", action="store_true", help="Force-include SwiftUI/UIKit Fix lines even on non-Swift files.")
    parser.add_argument("--check-static", action="store_true", help="Also run deterministic Tier-1 static checks on provided Swift files.")
    parser.add_argument("--max-findings", type=int, default=5, help="Max static findings to display (default: 5).")
    parser.add_argument("--format", choices=["compact", "markdown", "json"], default="compact")
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH_PATH)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    args = parser.parse_args(argv)

    if args.scan:
        args.group_by = "component"

    if not args.graph.exists() or not args.rules.exists():
        print("Error: rules/graph.json or rules/hig-rules.json missing. Run `python3 scripts/build_graph.py` first.", file=sys.stderr)
        return 2

    has_query_input = any([args.query, args.slug, args.api])
    has_code_input = bool(args.paths or args.diff or args.staged)
    if not has_query_input and not has_code_input:
        parser.error("Provide UI file path(s), --diff/--staged, or at least one of --query, --slug, --api.")

    try:
        graph = json.loads(args.graph.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Error: {args.graph} is not valid JSON ({exc}). Re-run `python3 scripts/build_graph.py` first.", file=sys.stderr)
        return 2
    try:
        rules_doc = json.loads(args.rules.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Error: {args.rules} is not valid JSON ({exc}). Re-run `python3 scripts/extract_rules.py` first.", file=sys.stderr)
        return 2
    indexed_apis = set(graph.get("api_index", {}).keys())

    changed_map: dict[Path, set[int]] = {}
    target_files: list[Path] = []
    if args.diff or args.staged:
        changed_map = collect_git_diff_ui_files(args.repo.resolve(), staged=args.staged)
        target_files = sorted(changed_map.keys())
        if not target_files and not has_query_input:
            print("No modified UI files in git diff.")
            return 0
    elif args.paths:
        target_files = collect_ui_files(args.paths)
        if not target_files and not has_query_input:
            print("No supported UI files found in given path(s).", file=sys.stderr)
            return 1

    all_apis: set[str] = set()
    api_locations: dict[str, list[str]] = defaultdict(list)
    all_frameworks: set[str] = set()
    all_platforms: set[str] = set()
    stacks_seen: set[str] = set()
    swift_files_data: list[dict[str, Any]] = []

    for tf in target_files:
        raw = tf.read_text(encoding="utf-8", errors="replace")
        stack = detect_file_stack(tf, raw)
        stacks_seen.add(stack)
        if stack in ("swift", "flutter", "react-native"):
            clean, ignores = strip_swift_comments_preserve_lines(raw)
        else:
            clean, ignores = raw, {}
        fws, plats = detect_frameworks_and_platforms(tf, clean, stack, args.platform)
        apis, line_map = extract_apis_with_lines(clean, stack, indexed_apis)

        all_frameworks.update(fws)
        all_platforms.update(plats)
        all_apis.update(apis)
        for api, lns in line_map.items():
            loc = f"{tf.name}:L{lns[0]}" if len(target_files) > 1 else f"L{lns[0]}"
            if loc not in api_locations[api]:
                api_locations[api].append(loc)

        if stack == "swift":
            swift_files_data.append(
                {
                    "path": tf,
                    "clean": clean,
                    "ignores": ignores,
                    "changed_lines": changed_map.get(tf),
                    "apis": apis,
                }
            )

    if args.api:
        for tok in args.api.split(","):
            t = tok.strip()
            if t:
                all_apis.add(t)
                api_locations.setdefault(t, [])

    explicit_slugs = [s.strip() for s in args.slug.split(",") if s.strip()] if args.slug else []
    query_terms = tokenize_query(args.query) if args.query else []

    if args.platform and args.platform != "all":
        platforms = [args.platform]
    elif all_platforms:
        platforms = sorted(all_platforms)
    else:
        platforms = ["ios", "ipados", "macos"]

    tiers = {"static", "semantic", "visual"} if "all" in args.tier else {t.strip().lower() for t in args.tier.split(",") if t.strip()}
    severities = {s.strip().lower() for s in args.severity.split(",") if s.strip()}

    # Auto-disable SwiftUI/UIKit fixes for pure cross-platform scans unless --with-fixes is passed
    is_pure_cross_platform = bool(stacks_seen) and "swift" not in stacks_seen
    include_fixes = args.with_fixes or (not args.no_fixes and not is_pure_cross_platform)

    effective_hops = args.hops
    if explicit_slugs and not query_terms and not all_apis and "--hops" not in sys.argv:
        effective_hops = 0

    ranked_nodes = route_hig_nodes(
        graph=graph,
        platforms=platforms,
        matched_apis=sorted(all_apis),
        explicit_slugs=explicit_slugs,
        query_terms=query_terms,
        hops=effective_hops,
        max_nodes=args.max_nodes,
    )

    groups: list[dict[str, Any]] | None = None
    selected_rules: list[dict[str, Any]] = []
    if args.group_by == "component" and api_locations:
        groups, used_tokens, total_candidates = build_component_groups(
            api_locations=dict(api_locations),
            graph=graph,
            rules_doc=rules_doc,
            platforms=platforms,
            allowed_tiers=tiers,
            allowed_severities=severities,
            budget_tokens=args.budget,
            include_fixes=include_fixes,
        )
        cited_slugs = {s for g in groups for s in g["slugs"]} | {n["slug"] for n in ranked_nodes}
    else:
        selected_rules, used_tokens, total_candidates = select_rules_within_budget(
            ranked_nodes=ranked_nodes,
            matched_apis=all_apis,
            query_terms=query_terms,
            rules_doc=rules_doc,
            platforms=platforms,
            allowed_tiers=tiers,
            allowed_severities=severities,
            budget_tokens=args.budget,
            include_fixes=include_fixes,
        )
        cited_slugs = {n["slug"] for n in ranked_nodes} | {r["slug"] for r in selected_rules}

    raw_hig_tokens = sum(graph["nodes"][s]["est_tokens"] for s in cited_slugs if s in graph["nodes"])
    savings_pct = round((1 - used_tokens / max(1, raw_hig_tokens)) * 100, 1) if raw_hig_tokens else 0.0

    payload: dict[str, Any] = {
        "meta": {
            "query": args.query,
            "slugs": explicit_slugs,
            "files": [str(f) for f in target_files],
            "frameworks": sorted(all_frameworks),
            "platforms": platforms,
            "matched_apis": sorted(all_apis),
            "group_by": args.group_by,
            "cross_platform_note": is_pure_cross_platform and not include_fixes,
            "budget_tokens": args.budget,
            "card_tokens_est": used_tokens,
            "raw_hig_tokens_est": raw_hig_tokens,
            "savings_pct": savings_pct,
            "candidate_rules": total_candidates,
        },
        "nodes": ranked_nodes,
        "rules": selected_rules,
    }
    if groups is not None:
        payload["groups"] = groups

    if args.check_static and swift_files_data:
        findings, counts = run_hig_lint_files(
            files_data=swift_files_data,
            rules_list=rules_doc.get("rules"),
            platforms=platforms,
            max_findings=args.max_findings,
        )
        payload["static_findings"] = findings
        payload["static_suppressed"] = counts["suppressed"]

    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(render_markdown_output(payload, include_fixes=include_fixes))
    else:
        print(render_compact_output(payload, include_fixes=include_fixes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
