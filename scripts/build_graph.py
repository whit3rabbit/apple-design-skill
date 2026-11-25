#!/usr/bin/env python3
"""
build_graph.py

Builds the Obsidian-style HIG knowledge graph (`rules/graph.json`) and
baseline rule registry (`rules/hig-rules.json`) from `references/hig/*.md`.

Features:
- Zero external dependencies (Python 3 stdlib only).
- Extracts 124 HIG nodes, 700+ directed cross-file edges (inline + related),
  reverse backlinks, hub centrality scores, and 600+ Apple Developer doc links.
- Merges curated SwiftUI/UIKit/AppKit API -> HIG mappings so code can be routed
  deterministically without loading 30k+ tokens of raw Markdown.
- Extracts Apple's ~1,600+ bold lead-in imperative rules into a structured
  baseline rule registry, merged with curated Tier-1 static Swift rules and any
  previously LLM-compiled rules (from `extract_rules.py`).
- Strictly verifies every rule quote against its source HIG file.
- Optional `--obsidian <dir>` exports an Obsidian-ready graph vault with YAML
  frontmatter, [[wikilinks]], and Topic Maps of Content (MOCs).

Usage:
  python3 scripts/build_graph.py
  python3 scripts/build_graph.py --obsidian .cache/obsidian-vault
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hig_swift_catalog import (
    CURATED_STATIC_RULES,
    DEFAULT_GRAPH_PATH,
    DEFAULT_RULES_PATH,
    HIG_DIR,
    SWIFT_API_TO_SLUGS,
    parse_hig_markdown,
    verify_quote_in_source,
)

# Heuristic classifiers for deterministically extracted baseline rules
_CRITICAL_RE = re.compile(
    r"\b(voiceover|accessibility|contrast|dynamic type|minimum|apple pay|never use the apple logo|privacy|permission|destructive|legib|flashing|seizure|color alone|solely on color|hit region|44x44)\b",
    re.I,
)
_HIGH_RE = re.compile(
    r"\b(never|don['’]t|avoid|always|must|required|standard system|size class|dark mode|liquid glass|overflow|tab bar|toolbar|menu bar|keyboard|cancel|undo|swipe to dismiss|safe area)\b",
    re.I,
)
_STATIC_HINT_RE = re.compile(
    r"\b(dynamic type|text style|font weight|system color|hard-cod|fixed width|size class|sf symbol|accessibility|label|badge|role|prominent|reduce motion|reduce transparency|apple pay|title-style|ellipsis|tab bar|toolbar)\b",
    re.I,
)
_VISUAL_HINT_RE = re.compile(
    r"\b(contrast|lighting|visual|illusion|align|spacing|padding|shadow|blur|vibrancy|icon|artwork|color profile|p3|srgb|screenshot|mockup|animation|motion|hover)\b",
    re.I,
)


def classify_baseline_rule(slug: str, lead: str, detail: str) -> tuple[str, str]:
    """Heuristically classify (tier, severity) for deterministic baseline rules."""
    combined = f"{lead} {detail}"

    # Severity
    if slug in ("accessibility", "voiceover", "apple-pay") and _CRITICAL_RE.search(combined):
        severity = "critical"
    elif _CRITICAL_RE.search(lead):
        severity = "high"
    elif _HIGH_RE.search(lead):
        severity = "high"
    else:
        severity = "medium"

    # Tier (conservative: deterministic baseline marks rules as semantic or visual;
    # Tier-1 `static` is reserved for curated/LLM-verified code-checkable rules)
    if _VISUAL_HINT_RE.search(lead) and not _STATIC_HINT_RE.search(lead):
        tier = "visual"
    else:
        tier = "semantic"

    return tier, severity


def slug_prefix(slug: str) -> str:
    parts = [p for p in slug.split("-") if p not in ("for", "and", "the", "with", "in", "to")]
    if len(parts) == 1:
        return parts[0][:5].upper()
    return "".join(p[:3] for p in parts[:2]).upper()


def build_graph_and_rules(
    hig_dir: Path,
    existing_rules_path: Path | None = None,
    preserve_llm: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    md_files = sorted(hig_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"No markdown files found in {hig_dir}")

    parsed_pages: dict[str, dict[str, Any]] = {}
    raw_texts: dict[str, str] = {}
    for path in md_files:
        parsed = parse_hig_markdown(path)
        parsed_pages[parsed["slug"]] = parsed
        raw_texts[parsed["slug"]] = path.read_text(encoding="utf-8")

    valid_slugs = set(parsed_pages.keys())

    # Build directed edges and backlinks
    edges: list[dict[str, Any]] = []
    backlinks: dict[str, set[str]] = defaultdict(set)
    out_links_all: dict[str, set[str]] = defaultdict(set)

    for slug, page in parsed_pages.items():
        for target in page["inline_links"]:
            if target in valid_slugs:
                edges.append({"source": slug, "target": target, "kind": "inline", "weight": 2})
                backlinks[target].add(slug)
                out_links_all[slug].add(target)
        for target in page["related_links"]:
            if target in valid_slugs:
                if target not in page["inline_links"]:
                    edges.append({"source": slug, "target": target, "kind": "related", "weight": 1})
                backlinks[target].add(slug)
                out_links_all[slug].add(target)

    # Reverse API -> slugs index (merge curated Swift catalog + Apple doc links)
    api_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    slug_to_apis: dict[str, set[str]] = defaultdict(set)

    for api, slugs in SWIFT_API_TO_SLUGS.items():
        for rank, slug in enumerate(slugs):
            if slug in valid_slugs:
                weight = 3 if rank == 0 else 2
                api_index[api].append({"slug": slug, "weight": weight, "origin": "curated"})
                slug_to_apis[slug].add(api)

    # Extract clean Swift/UIKit/AppKit symbol names from Apple doc links inside each HIG page
    generic_doc_noise = {
        "SwiftUI", "UIKit", "AppKit", "Foundation", "View", "Text", "HStack", "VStack", "ZStack",
        "Color", "Image", "Button", "Label", "List", "Menu", "Form", "Table", "Window",
    }
    symbol_re = re.compile(r"^[A-Z][A-Za-z0-9_.]*(?:\([^)]*\))?$|^[a-z][A-Za-z0-9_]*\([^)]*\)$")
    for slug, page in parsed_pages.items():
        for fw in ("swiftui", "uikit", "appkit", "widgetkit", "passkit", "accessibility", "appintents"):
            for label in page["apple_docs"].get(fw, []):
                clean = label.strip()
                if symbol_re.match(clean) and len(clean) >= 3:
                    # Normalize leading dot for modifiers like leading(_:) -> .leading
                    base_sym = clean.split("(", 1)[0]
                    if base_sym in generic_doc_noise:
                        continue
                    if base_sym not in SWIFT_API_TO_SLUGS:
                        existing_slugs = {e["slug"] for e in api_index[base_sym]}
                        if slug not in existing_slugs:
                            api_index[base_sym].append({"slug": slug, "weight": 1, "origin": f"doc:{fw}"})
                        slug_to_apis[slug].add(base_sym)

    # Load existing LLM-extracted rules if requested (done before finalizing api_index
    # so LLM-extracted SwiftUI/UIKit/AppKit APIs can also enrich graph routing!)
    preserved_llm_by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if preserve_llm and existing_rules_path and existing_rules_path.exists():
        try:
            old = json.loads(existing_rules_path.read_text(encoding="utf-8"))
            for r in old.get("rules", []):
                if r.get("origin") == "llm":
                    r_slug = r.get("slug", "")
                    if r_slug in parsed_pages and r.get("source_sha256") == parsed_pages[r_slug]["sha256"]:
                        preserved_llm_by_slug[r_slug].append(r)
        except Exception:
            pass

    # Fold clean LLM-extracted SwiftUI/UIKit/AppKit APIs into api_index
    llm_sym_re = re.compile(r"^\.?[A-Za-z_][A-Za-z0-9_.]*$")
    for slug, rlist in preserved_llm_by_slug.items():
        for r in rlist:
            for key in ("swiftui_apis", "uikit_apis", "appkit_apis"):
                for raw_api in r.get(key, []):
                    sym = str(raw_api).strip().split("(", 1)[0].strip()
                    if not sym or len(sym) < 3 or not llm_sym_re.match(sym):
                        continue
                    bare = sym.lstrip(".")
                    if bare in generic_doc_noise or sym in generic_doc_noise:
                        continue
                    existing_slugs = {e["slug"] for e in api_index[sym]}
                    if slug not in existing_slugs:
                        api_index[sym].append({"slug": slug, "weight": 2, "origin": "llm"})
                    slug_to_apis[slug].add(sym)

    # Assemble graph nodes
    nodes: dict[str, dict[str, Any]] = {}
    for slug, page in parsed_pages.items():
        in_deg = len(backlinks[slug])
        out_deg = len(out_links_all[slug])
        nodes[slug] = {
            "slug": slug,
            "title": page["title"],
            "file": page["file"],
            "source_url": page["source_url"],
            "section": page["section"],
            "platforms": page["platforms"],
            "last_changed": page["last_changed"],
            "is_curated": page["is_curated"],
            "abstract": page["abstract"],
            "sha256": page["sha256"],
            "line_count": page["line_count"],
            "est_tokens": page["est_tokens"],
            "headings": [h["text"] for h in page["headings"]],
            "links_out": sorted(out_links_all[slug]),
            "links_in": sorted(backlinks[slug]),
            "in_degree": in_deg,
            "out_degree": out_deg,
            "hub_score": round(in_deg * 1.5 + out_deg * 0.5, 2),
            "swift_apis": sorted(slug_to_apis[slug]),
            "apple_doc_frameworks": sorted(page["apple_docs"].keys()),
        }

    top_hubs = sorted(
        [{"slug": s, "in_degree": n["in_degree"], "hub_score": n["hub_score"]} for s, n in nodes.items()],
        key=lambda x: (-x["in_degree"], -x["hub_score"], x["slug"]),
    )[:20]

    # Build canonical rules list:
    # 1) Verified curated Tier-1 static Swift rules
    # 2) Preserved LLM-compiled rules (for unchanged pages) OR deterministic bold-lead baseline rules
    rules: list[dict[str, Any]] = []
    quote_failures: list[str] = []

    for rule in CURATED_STATIC_RULES:
        slug = rule["slug"]
        if slug not in raw_texts:
            quote_failures.append(f"{rule['id']}: missing file {slug}.md")
            continue
        if not verify_quote_in_source(rule["quote"], raw_texts[slug]):
            quote_failures.append(f"{rule['id']}: quote not verified in {slug}.md")
            continue
        entry = dict(rule)
        entry["origin"] = "curated"
        entry["source_file"] = f"references/hig/{slug}.md"
        entry["source_sha256"] = parsed_pages[slug]["sha256"]
        entry["citation"] = f"{slug}.md › {rule['heading']}"
        rules.append(entry)

    for slug, page in parsed_pages.items():
        if slug in preserved_llm_by_slug:
            rules.extend(preserved_llm_by_slug[slug])
            continue

        prefix = slug_prefix(slug)
        for idx, br in enumerate(page["bold_rules"], start=1):
            tier, severity = classify_baseline_rule(slug, br["lead"], br["detail"])
            rule_id = f"HIG-{prefix}-D{idx:03d}"
            quote = br["lead"]
            if not verify_quote_in_source(quote, raw_texts[slug]):
                quote_failures.append(f"{rule_id}: quote not verified in {slug}.md")
                continue
            rules.append(
                {
                    "id": rule_id,
                    "slug": slug,
                    "heading": br["heading"],
                    "citation": f"{slug}.md › {br['heading']}",
                    "tier": tier,
                    "severity": severity,
                    "platforms": br["platforms"],
                    "swiftui_apis": sorted(slug_to_apis[slug])[:12],
                    "uikit_apis": [],
                    "appkit_apis": [],
                    "rule": br["lead"],
                    "detail": br["detail"],
                    "quote": quote,
                    "origin": "deterministic",
                    "source_file": page["file"],
                    "source_sha256": page["sha256"],
                    "line": br["line"],
                }
            )

    if quote_failures:
        raise RuntimeError("Quote verification failed:\n  " + "\n  ".join(quote_failures))

    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    graph_doc = {
        "schema_version": "1.0",
        "built_at": built_at,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "api_symbol_count": len(api_index),
            "total_hig_tokens_est": sum(n["est_tokens"] for n in nodes.values()),
        },
        "hubs": top_hubs,
        "nodes": nodes,
        "edges": edges,
        "api_index": dict(sorted(api_index.items())),
    }

    tier_counts: dict[str, int] = defaultdict(int)
    origin_counts: dict[str, int] = defaultdict(int)
    for r in rules:
        tier_counts[r["tier"]] += 1
        origin_counts[r["origin"]] += 1

    rules_doc = {
        "schema_version": "1.0",
        "built_at": built_at,
        "stats": {
            "total_rules": len(rules),
            "by_tier": dict(sorted(tier_counts.items())),
            "by_origin": dict(sorted(origin_counts.items())),
            "pages_covered": len({r["slug"] for r in rules}),
        },
        "rules": rules,
    }

    return graph_doc, rules_doc


def export_obsidian_vault(graph: dict[str, Any], rules_doc: dict[str, Any], out_dir: Path) -> None:
    """Export an Obsidian-ready graph view with YAML frontmatter, [[wikilinks]], and MOCs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes_dir = out_dir / "nodes"
    nodes_dir.mkdir(parents=True, exist_ok=True)

    rules_by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rules_doc["rules"]:
        rules_by_slug[r["slug"]].append(r)

    by_section: dict[str, list[str]] = defaultdict(list)

    for slug, node in graph["nodes"].items():
        by_section[node["section"]].append(slug)
        fm_lines = [
            "---",
            f"title: \"{node['title'].replace('\"', '')}\"",
            f"slug: {slug}",
            f"section: \"{node['section']}\"",
            f"platforms: [{', '.join(node['platforms'])}]",
            f"last_changed: \"{node['last_changed'] or 'unknown'}\"",
            f"in_degree: {node['in_degree']}",
            f"hub_score: {node['hub_score']}",
            f"est_tokens: {node['est_tokens']}",
            f"source_file: \"{node['file']}\"",
            f"swift_apis: [{', '.join(json.dumps(a) for a in node['swift_apis'][:20])}]",
            "---",
            "",
            f"# {node['title']}",
            "",
            node["abstract"],
            "",
        ]
        if node["swift_apis"]:
            fm_lines.append("## Swift / Apple APIs")
            fm_lines.append(", ".join(f"`{a}`" for a in node["swift_apis"]))
            fm_lines.append("")

        node_rules = rules_by_slug.get(slug, [])
        if node_rules:
            fm_lines.append(f"## Rules ({len(node_rules)})")
            for r in node_rules[:15]:
                fm_lines.append(f"- **[{r['id']}][{r['severity'].upper()}][{r['tier']}]** ({r['heading']}): {r['rule']}")
            fm_lines.append("")

        if node["links_out"]:
            fm_lines.append("## Outgoing Links")
            for target in node["links_out"]:
                fm_lines.append(f"- [[{target}]]")
            fm_lines.append("")

        if node["links_in"]:
            fm_lines.append("## Backlinks")
            for src in node["links_in"]:
                fm_lines.append(f"- [[{src}]]")
            fm_lines.append("")

        (nodes_dir / f"{slug}.md").write_text("\n".join(fm_lines), encoding="utf-8")

    # Root MOC index
    moc_lines = [
        "# Apple HIG Knowledge Graph (MOC)",
        "",
        f"- **Nodes:** {graph['stats']['node_count']}",
        f"- **Edges:** {graph['stats']['edge_count']}",
        f"- **Swift/Apple API symbols indexed:** {graph['stats']['api_symbol_count']}",
        f"- **Total rules:** {rules_doc['stats']['total_rules']}",
        "",
        "## Top Hub Nodes",
        "",
    ]
    for hub in graph["hubs"][:12]:
        moc_lines.append(f"- [[{hub['slug']}]] (in-degree: {hub['in_degree']}, hub score: {hub['hub_score']})")
    moc_lines.append("")

    for section, slugs in sorted(by_section.items()):
        moc_lines.append(f"## {section}")
        moc_lines.append("")
        for s in sorted(slugs):
            moc_lines.append(f"- [[{s}]]")
        moc_lines.append("")

    (out_dir / "MOC-Index.md").write_text("\n".join(moc_lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Apple HIG knowledge graph and baseline rule registry.")
    parser.add_argument("--hig-dir", type=Path, default=HIG_DIR, help="Directory of pulled HIG markdown files.")
    parser.add_argument("--graph-out", type=Path, default=DEFAULT_GRAPH_PATH, help="Output path for graph.json.")
    parser.add_argument("--rules-out", type=Path, default=DEFAULT_RULES_PATH, help="Output path for hig-rules.json.")
    parser.add_argument("--no-preserve-llm", action="store_true", help="Do not preserve existing LLM-compiled rules.")
    parser.add_argument("--obsidian", type=Path, default=None, help="Optional directory to export an Obsidian vault.")
    args = parser.parse_args(argv)

    graph, rules_doc = build_graph_and_rules(
        hig_dir=args.hig_dir,
        existing_rules_path=args.rules_out,
        preserve_llm=not args.no_preserve_llm,
    )

    args.graph_out.parent.mkdir(parents=True, exist_ok=True)
    args.rules_out.parent.mkdir(parents=True, exist_ok=True)
    args.graph_out.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.rules_out.write_text(json.dumps(rules_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.obsidian:
        export_obsidian_vault(graph, rules_doc, args.obsidian)
        print(f"Exported Obsidian vault to {args.obsidian}")

    print(
        f"Built {args.graph_out.name}: {graph['stats']['node_count']} nodes, "
        f"{graph['stats']['edge_count']} edges, {graph['stats']['api_symbol_count']} indexed APIs"
    )
    print(
        f"Built {args.rules_out.name}: {rules_doc['stats']['total_rules']} verified rules "
        f"(tiers: {rules_doc['stats']['by_tier']}, origins: {rules_doc['stats']['by_origin']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
