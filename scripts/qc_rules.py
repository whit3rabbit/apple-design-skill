#!/usr/bin/env python3
"""
qc_rules.py

Sonnet-powered Quality Control (QC) pass over LLM-compiled rules in `rules/hig-rules.json`.

Two-stage pipeline:
1. Deterministic Swift/UIKit/AppKit API pre-audit detects deprecated, invented,
   multi-line/bloated, or suspicious SwiftUI/UIKit fixes across all LLM rules.
2. Batched LLM QC (defaults to Claude Sonnet via OpenRouter or Anthropic) audits
   flagged rules (or `--mode all`) for modern SwiftUI (iOS 18+/26) & UIKit/AppKit
   API accuracy, concise single-line fixes, and accurate tier/severity tags —
   without ever touching verbatim HIG quotes or citations.

Zero external dependencies (Python 3 stdlib only).

Usage:
  # Preview deterministic pre-audit findings without calling any API
  python3 scripts/qc_rules.py --provider dry-run

  # Run Sonnet QC on all flagged rules via OpenRouter (fast & surgical)
  python3 scripts/qc_rules.py --provider openrouter --mode flagged --concurrency 4

  # Run Sonnet QC across ALL LLM-compiled rules
  python3 scripts/qc_rules.py --provider openrouter --mode all --batch-size 25 --concurrency 6
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from build_graph import build_graph_and_rules
from hig_swift_catalog import (
    DEFAULT_CACHE_DIR,
    DEFAULT_GRAPH_PATH,
    DEFAULT_RULES_PATH,
    HIG_DIR,
    REPO_ROOT,
)

DEFAULT_QC_CACHE_DIR = REPO_ROOT / ".cache" / "rule-qc"

# Deterministic SwiftUI/UIKit red flags to catch invented or deprecated APIs from faster models
SUSPICIOUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("invented_button_role_primary", re.compile(r"role\s*:\s*\.(?:primary|normal)\b")),
    ("invented_button_style", re.compile(r"\.buttonStyle\(\s*\.(?:primary|prominent|secondary|destructive|cancel)\s*\)")),
    ("deprecated_foregroundColor", re.compile(r"\.foregroundColor\s*\(")),
    ("deprecated_navigationView", re.compile(r"\bNavigationView\b")),
    ("deprecated_actionSheet", re.compile(r"\.actionSheet\s*\(|\bActionSheet\s*\(")),
    ("deprecated_accentColor", re.compile(r"\.accentColor\s*\(")),
    ("deprecated_cornerRadius", re.compile(r"\.cornerRadius\s*\(")),
    ("deprecated_alert_struct", re.compile(r"\bAlert\s*\(\s*title\s*:")),
    ("deprecated_edgesIgnoringSafeArea", re.compile(r"\.edgesIgnoringSafeArea\s*\(")),
    ("deprecated_tabItem", re.compile(r"\.tabItem\s*\{")),
    ("deprecated_previewProvider", re.compile(r"\bPreviewProvider\b")),
    ("multiline_fix", re.compile(r"\n")),
    ("overlong_fix", re.compile(r"^.{185,}$")),
    ("invented_modifier_guess", re.compile(r"\.(?:prominent|glassBackground|adaptiveColor|hapticFeedback|dynamicFont|voiceOverLabel)\b")),
    ("bare_style_comment", re.compile(r"//\s*(?:or use|todo|etc)", re.I)),
]

QC_SYSTEM_PROMPT = """You are a Staff Apple Frameworks Engineer (SwiftUI, UIKit, AppKit, iOS 18–26, macOS 15–26, Swift 6) performing a strict API accuracy Quality Control (QC) review on compiled Apple HIG lint rules.

For each rule in the input JSON array, check:
1. `fix_swiftui`: Must use ONLY real, modern, non-deprecated SwiftUI APIs!
   - Replace invented APIs like `Button(..., role: .primary)` -> `Button(...) { }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)`.
   - Replace `.buttonStyle(.prominent)` -> `.buttonStyle(.borderedProminent)` or `.buttonStyle(.glassProminent)`.
   - Replace `.foregroundColor(...)` -> `.foregroundStyle(...)`.
   - Replace `NavigationView` -> `NavigationStack` or `NavigationSplitView`.
   - Replace `.accentColor(...)` -> `.tint(...)`.
   - Replace `.cornerRadius(r)` -> `.clipShape(.rect(cornerRadius: r))`.
   - Replace `.actionSheet(...)` -> `.confirmationDialog(...)`.
   - Replace `.edgesIgnoringSafeArea(...)` -> `.ignoresSafeArea(...)`.
   - Keep `fix_swiftui` concise, single-line (NO newlines), <= 150 characters, or "" if the rule is not code-fixable in SwiftUI.
2. `fix_uikit`: Must use real UIKit/AppKit APIs, concise, single-line, <= 150 characters (or "" if N/A).
3. `swiftui_apis`, `uikit_apis`, `appkit_apis`: Keep only real symbol/modifier names (drop hallucinated or generic prose words).
4. `tier`: Verify `static` (objectively AST/pattern-checkable in Swift source) vs `semantic` (intent/copy/flow) vs `visual` (rendered pixels/craft).
5. `severity`: Verify `critical` (accessibility/App Review blocker) | `high` | `medium` | `low`.

Return ONLY a valid JSON object (no markdown fences, no commentary) containing ONLY the rules that needed corrections:
{
  "corrections": [
    {
      "id": "HIG-...",
      "fix_swiftui": "...",
      "fix_uikit": "...",
      "swiftui_apis": [...],
      "uikit_apis": [...],
      "appkit_apis": [...],
      "tier": "static | semantic | visual",
      "severity": "critical | high | medium | low",
      "reason": "Short 5-12 word summary of what was fixed"
    }
  ]
}

If a rule is already accurate and concise, omit it from `corrections`. Never invent non-existent Apple APIs.
"""


def pre_audit_rule(rule: dict[str, Any]) -> list[str]:
    """Return list of deterministic QC flag names for a rule (empty if clean)."""
    if rule.get("origin") != "llm":
        return []
    flags: list[str] = []
    fix_sw = str(rule.get("fix_swiftui", "") or "")
    fix_ui = str(rule.get("fix_uikit", "") or "")
    anti = str(rule.get("anti_pattern", "") or "")
    combined_code = f"{fix_sw} {anti}"

    for name, pat in SUSPICIOUS_PATTERNS:
        target = fix_sw
        if pat.search(target):
            flags.append(name)
    if "\n" in fix_ui or len(fix_ui) > 185:
        flags.append("uikit_multiline_or_long")
    return sorted(set(flags))


def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e


def call_qc_llm(provider: str, model: str, user_prompt: str, api_key: str, base_url: str | None) -> str:
    if provider in ("openai", "openrouter"):
        default_base = "https://openrouter.ai/api/v1" if provider == "openrouter" else "https://api.openai.com/v1"
        env_base = os.environ.get("OPENROUTER_BASE_URL") if provider == "openrouter" else os.environ.get("OPENAI_BASE_URL")
        url = (base_url or env_base or default_base).rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": QC_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        resp = _http_post_json(url, payload, headers)
        return resp["choices"][0]["message"]["content"]

    if provider == "anthropic":
        env_base = os.environ.get("ANTHROPIC_BASE_URL")
        url = (base_url or env_base or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        payload = {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0.0,
            "system": QC_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        resp = _http_post_json(url, payload, headers)
        return "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")

    raise ValueError(f"Unsupported provider: {provider}")


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def qc_batch(
    batch: list[tuple[dict[str, Any], list[str]]],
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
    cache_dir: Path,
    force: bool,
) -> dict[str, Any]:
    compact_items = [
        {
            "id": r["id"],
            "citation": r["citation"],
            "flags": flags,
            "tier": r["tier"],
            "severity": r["severity"],
            "rule": r["rule"],
            "swiftui_apis": r.get("swiftui_apis", []),
            "uikit_apis": r.get("uikit_apis", []),
            "appkit_apis": r.get("appkit_apis", []),
            "fix_swiftui": r.get("fix_swiftui", ""),
            "fix_uikit": r.get("fix_uikit", ""),
        }
        for r, flags in batch
    ]
    user_prompt = json.dumps(compact_items, ensure_ascii=False)
    batch_hash = hashlib.sha256(f"{model}|{user_prompt}".encode("utf-8")).hexdigest()[:16]
    cache_file = cache_dir / f"batch-{batch_hash}.json"

    if not force and cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        return {"status": "cached", "corrections": cached.get("corrections", [])}

    raw = call_qc_llm(provider, model, user_prompt, api_key, base_url)
    parsed = parse_json_response(raw)
    corrections = parsed.get("corrections", [])
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({"model": model, "corrections": corrections}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"status": "reviewed", "corrections": corrections}


def default_qc_model(provider: str) -> str:
    return {
        "openrouter": "anthropic/claude-sonnet-4.5",
        "anthropic": "claude-sonnet-4-5",
        "openai": "gpt-4.1",
        "dry-run": "dry-run",
    }[provider]


def resolve_api_key(provider: str, cli_key: str | None) -> str:
    if cli_key:
        return cli_key
    env_var = {
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "dry-run": "",
    }[provider]
    return os.environ.get(env_var, "") if env_var else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Sonnet QC pass on compiled HIG rules.")
    parser.add_argument("--provider", choices=["dry-run", "openrouter", "anthropic", "openai"], default="dry-run")
    parser.add_argument("--model", default=None, help="QC model ID (defaults to Claude Sonnet 4.5).")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--mode", choices=["flagged", "all"], default="flagged", help="QC only pre-audit flagged rules or all LLM rules.")
    parser.add_argument("--batch-size", type=int, default=20, help="Rules per QC batch call (default: 20).")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel QC batch calls.")
    parser.add_argument("--force", action="store_true", help="Ignore QC cache.")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH_PATH)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_QC_CACHE_DIR)
    parser.add_argument("--extract-cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args(argv)

    if not args.rules.exists():
        print("Error: rules/hig-rules.json missing.", file=sys.stderr)
        return 2

    rules_doc = json.loads(args.rules.read_text(encoding="utf-8"))
    rules = rules_doc.get("rules", [])

    flag_counts: dict[str, int] = defaultdict(int)
    candidates: list[tuple[dict[str, Any], list[str]]] = []
    for r in rules:
        if r.get("origin") != "llm":
            continue
        flags = pre_audit_rule(r)
        for fl in flags:
            flag_counts[fl] += 1
        if args.mode == "all" or flags:
            candidates.append((r, flags))

    batches = [candidates[i : i + args.batch_size] for i in range(0, len(candidates), args.batch_size)]

    print(f"Pre-audit across {sum(1 for r in rules if r.get('origin') == 'llm')} LLM rules:")
    print(f"  Flagged rules: {sum(1 for _, fl in candidates if fl)} | Selected ({args.mode}): {len(candidates)} in {len(batches)} batch(es)")
    if flag_counts:
        top_flags = sorted(flag_counts.items(), key=lambda x: -x[1])
        print("  Flags:", ", ".join(f"{k}={v}" for k, v in top_flags))

    if args.provider == "dry-run" or not candidates:
        return 0

    model = args.model or default_qc_model(args.provider)
    api_key = resolve_api_key(args.provider, args.api_key)
    if not api_key and not args.base_url:
        print(f"Error: missing API key for provider '{args.provider}'.", file=sys.stderr)
        return 2

    all_corrections: dict[str, dict[str, Any]] = {}
    reviewed_batches = 0
    cached_batches = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futs = [
            pool.submit(qc_batch, b, args.provider, model, api_key, args.base_url, args.cache_dir, args.force)
            for b in batches
        ]
        for fut in concurrent.futures.as_completed(futs):
            try:
                res = fut.result()
                if res["status"] == "cached":
                    cached_batches += 1
                else:
                    reviewed_batches += 1
                for c in res["corrections"]:
                    if isinstance(c, dict) and c.get("id"):
                        all_corrections[c["id"]] = c
            except Exception as exc:
                print(f"ERROR in QC batch: {exc}", file=sys.stderr)

    # Apply verified corrections (never touching quote, heading, citation, or source_sha256)
    valid_tiers = {"static", "semantic", "visual"}
    valid_sevs = {"critical", "high", "medium", "low"}
    patched_ids: list[tuple[str, str]] = []
    touched_slugs: set[str] = set()

    for r in rules:
        corr = all_corrections.get(r["id"])
        if not corr:
            continue
        changed = False
        for text_field in ("fix_swiftui", "fix_uikit"):
            if text_field in corr and isinstance(corr[text_field], str):
                cleaned = re.sub(r"\s+", " ", corr[text_field]).strip()
                if cleaned != r.get(text_field, ""):
                    r[text_field] = cleaned
                    changed = True
        for list_field in ("swiftui_apis", "uikit_apis", "appkit_apis"):
            if list_field in corr and isinstance(corr[list_field], list):
                cleaned_list = [str(x).strip() for x in corr[list_field] if str(x).strip()]
                if cleaned_list != r.get(list_field, []):
                    r[list_field] = cleaned_list
                    changed = True
        if corr.get("tier") in valid_tiers and corr["tier"] != r.get("tier"):
            r["tier"] = corr["tier"]
            changed = True
        if corr.get("severity") in valid_sevs and corr["severity"] != r.get("severity"):
            r["severity"] = corr["severity"]
            changed = True
        if changed:
            r["qc_model"] = model
            patched_ids.append((r["id"], str(corr.get("reason", ""))))
            touched_slugs.add(r["slug"])

    # Persist updated rules back to rules/hig-rules.json AND per-page extract cache so future rebuilds keep QC fixes
    args.rules.write_text(json.dumps(rules_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.extract_cache_dir.exists() and touched_slugs:
        by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rules:
            if r.get("origin") == "llm":
                by_slug[r["slug"]].append(r)
        for slug in touched_slugs:
            cf = args.extract_cache_dir / f"{slug}.json"
            if cf.exists():
                cdata = json.loads(cf.read_text(encoding="utf-8"))
                cdata["rules"] = by_slug[slug]
                cdata["qc_model"] = model
                cf.write_text(json.dumps(cdata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Rebuild graph.json & refresh stats in hig-rules.json (preserving all QC'd LLM rules + folding APIs)
    graph, refreshed_rules = build_graph_and_rules(HIG_DIR, existing_rules_path=args.rules, preserve_llm=True)
    args.graph.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.rules.write_text(json.dumps(refreshed_rules, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"QC complete ({model}): batches reviewed={reviewed_batches}, cached={cached_batches}, "
        f"rules patched={len(patched_ids)} across {len(touched_slugs)} page(s)"
    )
    for rid, reason in patched_ids[:12]:
        print(f"  - {rid}: {reason}")
    if len(patched_ids) > 12:
        print(f"  ... +{len(patched_ids) - 12} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
