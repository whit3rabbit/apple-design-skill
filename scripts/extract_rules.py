#!/usr/bin/env python3
"""
extract_rules.py

Offline LLM rule compiler for Apple HIG pages (`references/hig/*.md`).
Compiles prose HIG files into concise, API-mapped, 3-tier rules (static | semantic | visual)
with strict anti-hallucination quote & heading verification and SHA-256 incremental caching.

Zero external dependencies (uses Python stdlib urllib.request for OpenAI, Anthropic,
Gemini, or OpenAI-compatible local servers like Ollama/vLLM).

Usage:
  # Preview token/cache stats and prompt without making API calls
  python3 scripts/extract_rules.py --provider dry-run --slugs buttons,sheets,tab-bars

  # Compile specific pages using Gemini, OpenAI, or Anthropic
  GEMINI_API_KEY=... python3 scripts/extract_rules.py --provider gemini --slugs buttons,sheets
  OPENAI_API_KEY=... python3 scripts/extract_rules.py --provider openai --model gpt-4.1-mini
  ANTHROPIC_API_KEY=... python3 scripts/extract_rules.py --provider anthropic

  # Compile via local Ollama (OpenAI-compatible endpoint)
  python3 scripts/extract_rules.py --provider openai --base-url http://localhost:11434/v1 --model qwen2.5:14b --api-key ollama
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from build_graph import build_graph_and_rules, slug_prefix
from hig_swift_catalog import (
    DEFAULT_CACHE_DIR,
    DEFAULT_GRAPH_PATH,
    DEFAULT_RULES_PATH,
    HIG_DIR,
    estimate_tokens,
    parse_hig_markdown,
    verify_quote_in_source,
)

SYSTEM_PROMPT = """You are a senior Apple platform architect compiling Apple Human Interface Guidelines into a compact, machine-routable rule registry for SwiftUI, UIKit, and AppKit code reviews.

Extract the highest-signal, concrete, actionable design rules from the given HIG page.
Return ONLY a valid JSON object matching this exact schema (no markdown fences, no commentary):

{
  "rules": [
    {
      "heading": "Exact H2/H3 heading text from the page",
      "tier": "static | semantic | visual",
      "severity": "critical | high | medium | low",
      "platforms": ["ios", "ipados", "macos"],
      "swiftui_apis": ["Button", ".buttonStyle", ...],
      "uikit_apis": ["UIButton", ...],
      "appkit_apis": ["NSButton", ...],
      "rule": "Concise 1-sentence imperative rule (<= 25 words).",
      "anti_pattern": "Concrete Swift code or UI mistake that breaks this rule.",
      "fix_swiftui": "Concrete SwiftUI API/modifier fix (empty string if not applicable).",
      "fix_uikit": "Concrete UIKit/AppKit fix (empty string if not applicable).",
      "quote": "Exact verbatim sentence copied word-for-word from the source page."
    }
  ]
}

Strict constraints:
1. Extract 4 to 14 high-signal rules per page. Skip trivia, change logs, and image captions.
2. `quote` MUST be ONE single contiguous verbatim sentence copied word-for-word from the source page (no ellipses, no paraphrasing, no skipping words).
3. `heading` MUST match an exact heading from the provided Markdown.
4. Tier definitions:
   - "static": objectively detectable from Swift source AST/patterns (API misuse, missing modifiers, hardcoded values, forbidden roles).
   - "semantic": requires reading code intent, copy/wording, flow, or component choice.
   - "visual": requires rendered pixels, Dynamic Type visual testing, or design craft judgment.
5. Severity definitions:
   - "critical": accessibility failure, App Review rejection risk, or broken usability.
   - "high": major platform convention violation or significant UX friction.
   - "medium": suboptimal pattern or missed system component.
   - "low": polish or minor edge case.
6. Only include platforms actually covered by that section.
"""

VALID_TIERS = {"static", "semantic", "visual"}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_PLATFORMS = {"ios", "ipados", "macos"}


def trim_for_llm(raw_markdown: str, max_chars: int = 24000) -> str:
    """Drop Change log and massive Specifications tables to save input tokens."""
    out_lines: list[str] = []
    skip_section = False
    for line in raw_markdown.splitlines():
        if re.match(r"^##\s+(Specifications|Change log|Contents)\b", line, re.I):
            skip_section = True
            continue
        if skip_section and re.match(r"^##\s+", line):
            skip_section = False
        if not skip_section:
            out_lines.append(line)
    trimmed = "\n".join(out_lines).strip()
    if len(trimmed) > max_chars:
        trimmed = trimmed[:max_chars] + "\n\n[Truncated for length]"
    return trimmed


def build_user_prompt(page: dict[str, Any], raw_markdown: str) -> str:
    headings_list = ", ".join(h["text"] for h in page["headings"] if h["text"] not in ("Contents", "Change log", "Related guidelines"))
    trimmed = trim_for_llm(raw_markdown)
    return (
        f"HIG Page: {page['title']} (`{page['slug']}.md`)\n"
        f"Section: {page['section']}\n"
        f"Platforms covered: {', '.join(page['platforms'])}\n"
        f"Valid headings: {headings_list or 'Overview'}\n\n"
        f"--- BEGIN SOURCE MARKDOWN ---\n{trimmed}\n--- END SOURCE MARKDOWN ---"
    )


def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = 90) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} from {url}: {body}") from e


def call_llm(
    provider: str,
    model: str,
    user_prompt: str,
    api_key: str,
    base_url: str | None = None,
) -> str:
    if provider in ("openai", "openrouter"):
        default_base = "https://openrouter.ai/api/v1" if provider == "openrouter" else "https://api.openai.com/v1"
        env_base = os.environ.get("OPENAI_BASE_URL") if provider == "openai" else os.environ.get("OPENROUTER_BASE_URL")
        url = (base_url or env_base or default_base).rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
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
            "temperature": 0.1,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        resp = _http_post_json(url, payload, headers)
        return "".join(block.get("text", "") for block in resp.get("content", []) if block.get("type") == "text")

    if provider == "gemini":
        base = base_url.rstrip("/") if base_url else "https://generativelanguage.googleapis.com/v1beta"
        url = f"{base}/models/{model}:generateContent?key={api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
            },
        }
        resp = _http_post_json(url, payload, {})
        parts = resp["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    raise ValueError(f"Unsupported provider: {provider}")


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def verify_and_normalize_llm_rules(
    raw_rules: list[dict[str, Any]],
    page: dict[str, Any],
    raw_markdown: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Verify every quote and heading against the source HIG file; drop unverified claims."""
    slug = page["slug"]
    prefix = slug_prefix(slug)
    valid_headings = {h["text"].lower(): h["text"] for h in page["headings"]}
    valid_headings["overview"] = "Overview"

    verified: list[dict[str, Any]] = []
    warnings: list[str] = []

    for idx, item in enumerate(raw_rules, start=1):
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", "")).strip()
        rule_text = str(item.get("rule", "")).strip()
        if not rule_text:
            continue

        if not verify_quote_in_source(quote, raw_markdown):
            warnings.append(f"{slug}.md rule #{idx}: dropped unverified quote ({quote[:60]!r})")
            continue

        heading_raw = str(item.get("heading", "Overview")).strip()
        heading = valid_headings.get(heading_raw.lower())
        if not heading:
            # Fuzzy fallback: match prefix/substring of an actual heading on the page
            matches = [v for k, v in valid_headings.items() if heading_raw.lower() in k or k in heading_raw.lower()]
            heading = matches[0] if matches else "Overview"

        tier = str(item.get("tier", "semantic")).lower()
        if tier not in VALID_TIERS:
            tier = "semantic"

        severity = str(item.get("severity", "medium")).lower()
        if severity not in VALID_SEVERITIES:
            severity = "medium"

        platforms = [p.lower() for p in item.get("platforms", []) if str(p).lower() in VALID_PLATFORMS]
        if not platforms:
            platforms = list(page["platforms"])

        def _str_list(key: str) -> list[str]:
            vals = item.get(key, [])
            return [str(v).strip() for v in vals if str(v).strip()] if isinstance(vals, list) else []

        verified.append(
            {
                "id": f"HIG-{prefix}-L{len(verified) + 1:02d}",
                "slug": slug,
                "heading": heading,
                "citation": f"{slug}.md › {heading}",
                "tier": tier,
                "severity": severity,
                "platforms": platforms,
                "swiftui_apis": _str_list("swiftui_apis"),
                "uikit_apis": _str_list("uikit_apis"),
                "appkit_apis": _str_list("appkit_apis"),
                "rule": rule_text,
                "anti_pattern": str(item.get("anti_pattern", "")).strip(),
                "fix_swiftui": str(item.get("fix_swiftui", "")).strip(),
                "fix_uikit": str(item.get("fix_uikit", "")).strip(),
                "quote": quote,
                "origin": "llm",
                "source_file": page["file"],
                "source_sha256": page["sha256"],
            }
        )

    return verified, warnings


def default_model_for(provider: str) -> str:
    return {
        "openai": "gpt-4.1-mini",
        "openrouter": "anthropic/claude-haiku-4.5",
        "anthropic": "claude-haiku-4-5",
        "gemini": "gemini-2.5-flash",
        "dry-run": "dry-run",
    }[provider]


def resolve_api_key(provider: str, cli_key: str | None) -> str:
    if cli_key:
        return cli_key
    env_var = {
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "dry-run": "",
    }[provider]
    return os.environ.get(env_var, "") if env_var else ""


def process_page(
    path: Path,
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None,
    cache_dir: Path,
    force: bool,
) -> dict[str, Any]:
    raw_markdown = path.read_text(encoding="utf-8")
    page = parse_hig_markdown(path)
    slug = page["slug"]
    cache_file = cache_dir / f"{slug}.json"

    if not force and cache_file.exists():
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        if cached.get("source_sha256") == page["sha256"]:
            return {
                "slug": slug,
                "status": "cached",
                "rules": cached.get("rules", []),
                "warnings": [],
                "input_tokens_est": 0,
            }

    user_prompt = build_user_prompt(page, raw_markdown)
    in_tok = estimate_tokens(SYSTEM_PROMPT + user_prompt)

    if provider == "dry-run":
        return {
            "slug": slug,
            "status": "dry-run",
            "rules": [],
            "warnings": [],
            "input_tokens_est": in_tok,
            "sample_prompt": user_prompt[:400],
        }

    response_text = call_llm(provider, model, user_prompt, api_key, base_url)
    parsed = parse_json_response(response_text)
    verified, warnings = verify_and_normalize_llm_rules(parsed.get("rules", []), page, raw_markdown)

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "slug": slug,
                "source_sha256": page["sha256"],
                "provider": provider,
                "model": model,
                "rules": verified,
                "warnings": warnings,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "slug": slug,
        "status": "compiled",
        "rules": verified,
        "warnings": warnings,
        "input_tokens_est": in_tok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile Apple HIG pages into verified rules using an LLM.")
    parser.add_argument("--provider", choices=["dry-run", "openai", "openrouter", "anthropic", "gemini"], default="dry-run")
    parser.add_argument("--model", default=None, help="Model ID (defaults per provider).")
    parser.add_argument("--api-key", default=None, help="API key (defaults to provider env var).")
    parser.add_argument("--base-url", default=None, help="Optional base URL (e.g. http://localhost:11434/v1 for Ollama).")
    parser.add_argument("--slugs", default=None, help="Comma-separated list of HIG slugs to process (default: all).")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel API requests.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="Incremental SHA-256 cache directory.")
    parser.add_argument("--force", action="store_true", help="Ignore cache and re-extract.")
    parser.add_argument("--hig-dir", type=Path, default=HIG_DIR)
    parser.add_argument("--graph-out", type=Path, default=DEFAULT_GRAPH_PATH)
    parser.add_argument("--rules-out", type=Path, default=DEFAULT_RULES_PATH)
    args = parser.parse_args(argv)

    model = args.model or default_model_for(args.provider)
    api_key = resolve_api_key(args.provider, args.api_key)
    if args.provider != "dry-run" and not api_key and not args.base_url:
        print(f"Error: missing API key for provider '{args.provider}'. Set env var or pass --api-key.", file=sys.stderr)
        return 2

    all_files = sorted(args.hig_dir.glob("*.md"))
    if args.slugs:
        wanted = {s.strip().removesuffix(".md") for s in args.slugs.split(",") if s.strip()}
        target_files = [p for p in all_files if p.stem in wanted]
        missing = wanted - {p.stem for p in target_files}
        if missing:
            print(f"Warning: unknown slugs ignored: {', '.join(sorted(missing))}", file=sys.stderr)
    else:
        target_files = all_files

    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futs = {
            pool.submit(
                process_page,
                p,
                args.provider,
                model,
                api_key,
                args.base_url,
                args.cache_dir,
                args.force,
            ): p
            for p in target_files
        }
        for fut in concurrent.futures.as_completed(futs):
            path = futs[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                print(f"ERROR compiling {path.name}: {exc}", file=sys.stderr)

    results.sort(key=lambda r: r["slug"])
    total_in_tok = sum(r["input_tokens_est"] for r in results)
    compiled = [r for r in results if r["status"] == "compiled"]
    cached = [r for r in results if r["status"] == "cached"]
    all_warnings = [w for r in results for w in r["warnings"]]

    if args.provider == "dry-run":
        print(f"Dry run across {len(results)} HIG pages:")
        print(f"  Already cached:      {len(cached)}")
        print(f"  Pending LLM compile: {len(results) - len(cached)}")
        print(f"  Est input tokens:    ~{total_in_tok:,} (avg ~{total_in_tok // max(1, len(results)):,}/page)")
        print(f"  Est output tokens:   ~{len(results) * 900:,} (~900/page)")
        print(f"  Cache dir:           {args.cache_dir}")
        return 0

    # Merge verified LLM rules from cache into rules/hig-rules.json via build_graph
    llm_by_slug: dict[str, list[dict[str, Any]]] = {}
    if args.cache_dir.exists():
        for cf in sorted(args.cache_dir.glob("*.json")):
            data = json.loads(cf.read_text(encoding="utf-8"))
            if data.get("rules"):
                llm_by_slug[data["slug"]] = data["rules"]

    # Seed existing rules file with cached LLM rules so build_graph preserves them cleanly
    graph, rules_doc = build_graph_and_rules(args.hig_dir, existing_rules_path=None, preserve_llm=False)
    # Replace deterministic rules on pages that have verified up-to-date LLM rules
    sha_by_slug = {s: n["sha256"] for s, n in graph["nodes"].items()}
    active_llm_slugs = {
        s
        for s, rlist in llm_by_slug.items()
        if s in sha_by_slug and rlist and rlist[0].get("source_sha256") == sha_by_slug[s]
    }
    merged_rules = [
        r for r in rules_doc["rules"] if not (r["origin"] == "deterministic" and r["slug"] in active_llm_slugs)
    ]
    for s in sorted(active_llm_slugs):
        merged_rules.extend(llm_by_slug[s])

    rules_doc["rules"] = merged_rules
    rules_doc["stats"]["total_rules"] = len(merged_rules)
    tier_counts: dict[str, int] = {}
    origin_counts: dict[str, int] = {}
    for r in merged_rules:
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1
        origin_counts[r["origin"]] = origin_counts.get(r["origin"], 0) + 1
    rules_doc["stats"]["by_tier"] = dict(sorted(tier_counts.items()))
    rules_doc["stats"]["by_origin"] = dict(sorted(origin_counts.items()))

    args.graph_out.parent.mkdir(parents=True, exist_ok=True)
    args.graph_out.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.rules_out.write_text(json.dumps(rules_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"Done: compiled={len(compiled)}, cached={len(cached)}, "
        f"active_llm_pages={len(active_llm_slugs)}, total_rules={len(merged_rules)}"
    )
    if all_warnings:
        print(f"Quote verification dropped {len(all_warnings)} unverified rule(s):")
        for w in all_warnings[:10]:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
