#!/usr/bin/env python3
"""
design_md_audit.py

Deterministic multi-format design-system auditor supporting:
  1. Google Stitch `DESIGN.md` (Core & Extended with `design.extensions.yaml`)
  2. Open Design packages (`manifest.json`, `tokens.css`, `DESIGN.md`, `design-tokens.json`)
  3. Claude Design System packages (`design-system.json`, `tokens.json`, `README.md`, `components/`)
  4. Designer-MCP capture packages (`DESIGN.md`, `DESIGN.evidence.json`, `theme.json`, `styles.css`)

Performs schema/alias/cross-file drift validation, real WCAG 2.2 relative luminance
contrast computation, Apple HIG token checks (Dynamic Type floors, Dark Mode parity,
light-mode leakage, hit targets, Reduced Motion), and studio-craft checks.

Zero external dependencies (Python 3.10+ stdlib only).

Usage:
  python3 scripts/design_md_audit.py path/to/DESIGN.md --show-contrast --all
  python3 scripts/design_md_audit.py path/to/opendesign/airbnb --show-contrast
  python3 scripts/design_md_audit.py path/to/claude-design-systems/agentic --show-contrast
  python3 scripts/design_md_audit.py path/to/design-system --profile capture --fail-on high
  python3 scripts/design_md_audit.py path/to/DESIGN.md --platform ios,macos --format json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}

GOOGLE_TOP_LEVEL_FIELDS = {
    "version",
    "name",
    "description",
    "omitted",
    "colors",
    "typography",
    "rounded",
    "spacing",
    "motion",
    "components",
}

CANONICAL_SECTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("overview", "Overview", ("overview", "introduction", "about")),
    ("colors", "Colors", ("colors", "color", "color palette", "palette")),
    ("typography", "Typography", ("typography", "type", "fonts")),
    ("layout", "Layout", ("layout", "spacing", "grid", "layout & spacing", "layout and spacing")),
    ("elevation", "Elevation & Depth", ("elevation & depth", "elevation and depth", "elevation", "depth", "shadows")),
    ("shapes", "Shapes", ("shapes", "shape", "radius", "geometry", "border radius")),
    ("motion", "Motion & Micro-interaction", ("motion & micro-interaction", "motion and micro-interaction", "motion & micro-interactions", "motion", "animation")),
    ("voice", "Voice & Content Strategy", ("voice & content strategy", "voice and content strategy", "voice & tone", "voice and tone", "content strategy", "writing")),
    ("components", "Components", ("components", "component", "patterns", "controls")),
    ("guidelines", "Do's and Don'ts", ("do's and don'ts", "do's & don'ts", "dos and donts", "do and don't", "guidelines", "constraints")),
]

OPENDESIGN_SECTIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("theme", "Visual Theme & Atmosphere", ("visual theme & atmosphere", "visual theme and atmosphere", "visual theme", "atmosphere")),
    ("color", "Color", ("color", "colors", "color palette")),
    ("typography", "Typography", ("typography", "type")),
    ("spacing", "Spacing & Grid", ("spacing & grid", "spacing and grid", "spacing")),
    ("layout", "Layout & Composition", ("layout & composition", "layout and composition", "layout")),
    ("components", "Components", ("components", "component")),
    ("motion", "Motion & Interaction", ("motion & interaction", "motion and interaction", "motion")),
    ("voice", "Voice & Brand", ("voice & brand", "voice and brand", "voice & tone", "voice")),
    ("antipatterns", "Anti-patterns", ("anti-patterns", "anti patterns", "do's and don'ts", "don'ts")),
]

SECTION_INDEX = {sid: idx for idx, (sid, _, _) in enumerate(CANONICAL_SECTIONS)}
SECTION_LABEL = {sid: label for sid, label, _ in CANONICAL_SECTIONS}

TOKEN_REF_RE = re.compile(r"^\{([A-Za-z0-9_.-]+)\}$")
CSS_VAR_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(?:,\s*([^)]+))?\)")
DIMENSION_RE = re.compile(r"^-?(?:\d+(?:\.\d+)?)(?:px|em|rem|pt|dp|sp|%|vw|vh)$", re.I)
DURATION_RE = re.compile(r"^(?:\d+(?:\.\d+)?)(?:ms|s)$", re.I)
HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
RGB_COLOR_RE = re.compile(
    r"^rgba?\(\s*(\d{1,3}(?:\.\d+)?)\s*[, ]\s*(\d{1,3}(?:\.\d+)?)\s*[, ]\s*(\d{1,3}(?:\.\d+)?)(?:\s*[,/]\s*(\d*(?:\.\d+)?%?))?\s*\)$",
    re.I,
)
HSL_OR_MODERN_COLOR_RE = re.compile(r"^(?:hsla?|oklch|oklab|lch|lab|color|color-mix)\(.+\)$", re.I)
NAMED_CSS_COLORS: dict[str, tuple[int, int, int, float]] = {
    "black": (0, 0, 0, 1.0),
    "white": (255, 255, 255, 1.0),
    "transparent": (0, 0, 0, 0.0),
    "red": (255, 0, 0, 1.0),
    "green": (0, 128, 0, 1.0),
    "blue": (0, 0, 255, 1.0),
    "gray": (128, 128, 128, 1.0),
    "grey": (128, 128, 128, 1.0),
}

DISPLAY_ONLY_FONTS = {
    "playfair display",
    "abril fatface",
    "cinzel",
    "bodoni moda",
    "didot",
    "lobster",
    "pacifico",
    "bebas neue",
    "anton",
    "syne",
    "fraunces",
    "instrument serif",
}

SYSTEM_FONT_KEYWORDS = {
    "system-ui",
    "-apple-system",
    "sf pro",
    "sf pro text",
    "sf pro display",
    "sf mono",
    "new york",
    "ui-sans-serif",
    "ui-serif",
    "ui-monospace",
    "sans-serif",
    "serif",
    "monospace",
    "helvetica neue",
    "helvetica",
    "arial",
    "georgia",
    "times new roman",
    "menlo",
    "monaco",
    "consolas",
    "roboto",
    "segoe ui",
}

PLACEHOLDER_PATTERNS = [
    re.compile(r"\bdefine the product['’]s visual identity\b", re.I),
    re.compile(r"\bdescribe intended users\b", re.I),
    re.compile(r"\bdefine how background, surface, text\b", re.I),
    re.compile(r"\bdefine corner radius usage for each component\b", re.I),
    re.compile(r"\blorem ipsum\b", re.I),
    re.compile(r"\b(?:TODO|TBD|FIXME)\b"),
]


# ---------------------------------------------------------------------------
# Pure Python stdlib YAML subset parser (with 1-based line tracking)
# ---------------------------------------------------------------------------

def _strip_inline_comment(val: str) -> str:
    in_single = False
    in_double = False
    for i, ch in enumerate(val):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            if i == 0 or val[i - 1] != "\\":
                in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or val[i - 1].isspace():
                return val[:i].rstrip()
    return val.strip()


def _split_inline_csv(text: str) -> list[str]:
    parts: list[str] = []
    cur: list[str] = []
    in_s = False
    in_d = False
    depth = 0
    for ch in text:
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif not in_s and not in_d:
            if ch in "[{(":
                depth += 1
            elif ch in "]})":
                depth = max(0, depth - 1)
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _parse_scalar(raw: str) -> Any:
    s = _strip_inline_comment(raw).strip()
    if not s:
        return ""
    if (s.startswith('"') and s.endswith('"') and len(s) >= 2) or (
        s.startswith("'") and s.endswith("'") and len(s) >= 2
    ):
        inner = s[1:-1]
        if s[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        else:
            inner = inner.replace("''", "'")
        return inner
    if s.startswith("[") and s.endswith("]"):
        body = s[1:-1].strip()
        if not body:
            return []
        return [_parse_scalar(part) for part in _split_inline_csv(body)]
    if s.startswith("{") and s.endswith("}"):
        if TOKEN_REF_RE.match(s):
            return s
        body = s[1:-1].strip()
        if not body:
            return {}
        if ":" not in body:
            return s
        out: dict[str, Any] = {}
        for part in _split_inline_csv(body):
            if ":" in part:
                k, v = part.split(":", 1)
                out[str(_parse_scalar(k)).strip()] = _parse_scalar(v)
        return out
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except ValueError:
            return s
    if re.fullmatch(r"-?\d+\.\d+", s):
        try:
            return float(s)
        except ValueError:
            return s
    return s


def _find_mapping_colon(line: str) -> int:
    in_s = False
    in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            if i == 0 or line[i - 1] != "\\":
                in_d = not in_d
        elif ch == ":" and not in_s and not in_d:
            if i + 1 == len(line) or line[i + 1].isspace():
                return i
    return -1


def parse_yaml_with_lines(
    text: str,
    start_line: int = 1,
) -> tuple[dict[str, Any], dict[str, int], list[tuple[str, int, int]], list[tuple[int, str]]]:
    raw_lines = text.splitlines()
    entries: list[tuple[int, int, str]] = []
    for offset, raw in enumerate(raw_lines):
        line_no = start_line + offset
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]:
            indent = len(raw.expandtabs(2)) - len(raw.expandtabs(2).lstrip(" "))
        entries.append((line_no, indent, stripped))

    root: dict[str, Any] = {}
    line_map: dict[str, int] = {}
    duplicates: list[tuple[str, int, int]] = []
    errors: list[tuple[int, str]] = []

    def parse_block(idx: int, min_indent: int, prefix: str) -> tuple[Any, int]:
        if idx >= len(entries):
            return {}, idx
        _, first_indent, first_text = entries[idx]
        if first_indent < min_indent:
            return {}, idx

        if first_text == "-" or first_text.startswith("- "):
            seq: list[Any] = []
            seq_indent = first_indent
            while idx < len(entries):
                lno, ind, txt = entries[idx]
                if ind < seq_indent:
                    break
                if ind > seq_indent:
                    errors.append((lno, f"Unexpected indentation in list item: {txt[:40]}"))
                    idx += 1
                    continue
                if not (txt == "-" or txt.startswith("- ")):
                    break
                rest = txt[1:].strip() if txt.startswith("- ") else ""
                idx += 1
                if not rest:
                    if idx < len(entries) and entries[idx][1] > seq_indent:
                        item, idx = parse_block(idx, seq_indent + 1, f"{prefix}[{len(seq)}]")
                        seq.append(item)
                    else:
                        seq.append(None)
                else:
                    colon_idx = _find_mapping_colon(rest)
                    if colon_idx != -1:
                        k = str(_parse_scalar(rest[:colon_idx])).strip()
                        v_raw = rest[colon_idx + 1 :].strip()
                        item_dict: dict[str, Any] = {}
                        item_prefix = f"{prefix}[{len(seq)}]"
                        if v_raw:
                            item_dict[k] = _parse_scalar(v_raw)
                            line_map[f"{item_prefix}.{k}"] = lno
                        else:
                            if idx < len(entries) and entries[idx][1] > seq_indent:
                                sub_val, idx = parse_block(idx, seq_indent + 1, f"{item_prefix}.{k}")
                                item_dict[k] = sub_val
                            else:
                                item_dict[k] = {}
                            line_map[f"{item_prefix}.{k}"] = lno
                        if idx < len(entries) and entries[idx][1] > seq_indent and not entries[idx][2].startswith("- "):
                            extra, idx = parse_block(idx, seq_indent + 1, item_prefix)
                            if isinstance(extra, dict):
                                item_dict.update(extra)
                        seq.append(item_dict)
                    else:
                        seq.append(_parse_scalar(rest))
            return seq, idx

        mapping: dict[str, Any] = {}
        map_indent = first_indent
        while idx < len(entries):
            lno, ind, txt = entries[idx]
            if ind < map_indent:
                break
            if ind > map_indent:
                errors.append((lno, f"Unexpected indentation: {txt[:50]}"))
                idx += 1
                continue
            if txt.startswith("- ") or txt == "-":
                break
            colon_idx = _find_mapping_colon(txt)
            if colon_idx == -1:
                errors.append((lno, f"Expected `key: value` mapping entry, got: {txt[:50]}"))
                idx += 1
                continue
            raw_key = txt[:colon_idx].strip()
            key = str(_parse_scalar(raw_key)).strip()
            v_raw = txt[colon_idx + 1 :].strip()
            dotted = f"{prefix}.{key}" if prefix else key
            if key in mapping:
                duplicates.append((dotted, line_map.get(dotted, lno), lno))
            line_map[dotted] = lno
            idx += 1

            if v_raw in ("|", ">", "|-", ">-", "|+", ">+"):
                block_lines: list[str] = []
                while idx < len(entries) and entries[idx][1] > map_indent:
                    block_lines.append(entries[idx][2])
                    idx += 1
                joiner = "\n" if v_raw.startswith("|") else " "
                mapping[key] = joiner.join(block_lines)
                continue

            val_clean = _strip_inline_comment(v_raw)
            if val_clean != "":
                mapping[key] = _parse_scalar(v_raw)
            else:
                if idx < len(entries) and entries[idx][1] > map_indent:
                    sub_val, idx = parse_block(idx, map_indent + 1, dotted)
                    mapping[key] = sub_val
                elif idx < len(entries) and entries[idx][1] == map_indent and (
                    entries[idx][2].startswith("- ") or entries[idx][2] == "-"
                ):
                    sub_val, idx = parse_block(idx, map_indent, dotted)
                    mapping[key] = sub_val
                else:
                    mapping[key] = None
        return mapping, idx

    if entries:
        parsed, _ = parse_block(0, entries[0][1], "")
        if isinstance(parsed, dict):
            root = parsed
        else:
            errors.append((start_line, "Top-level YAML block must be a mapping (key: value)."))

    return root, line_map, duplicates, errors


# ---------------------------------------------------------------------------
# Color math (WCAG 2.2), typography & dimension helpers
# ---------------------------------------------------------------------------

def parse_color_rgba(value: str) -> tuple[int, int, int, float] | None:
    s = value.strip()
    low = s.lower()
    if low in NAMED_CSS_COLORS:
        return NAMED_CSS_COLORS[low]
    hm = HEX_COLOR_RE.match(s)
    if hm:
        h = hm.group(1)
        if len(h) == 3:
            r, g, b = (int(c * 2, 16) for c in h)
            return (r, g, b, 1.0)
        if len(h) == 4:
            r, g, b, a = (int(c * 2, 16) for c in h)
            return (r, g, b, a / 255.0)
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
        if len(h) == 8:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) / 255.0)
    rm = RGB_COLOR_RE.match(s)
    if rm:
        r, g, b = float(rm.group(1)), float(rm.group(2)), float(rm.group(3))
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            return None
        a_str = rm.group(4)
        a = 1.0
        if a_str:
            a = float(a_str[:-1]) / 100.0 if a_str.endswith("%") else float(a_str)
            a = max(0.0, min(1.0, a))
        return (int(round(r)), int(round(g)), int(round(b)), a)
    return None


def is_valid_color_syntax(value: str) -> bool:
    if parse_color_rgba(value) is not None:
        return True
    return bool(HSL_OR_MODERN_COLOR_RE.match(value.strip()))


def composite_over(
    fg: tuple[int, int, int, float],
    bg: tuple[int, int, int, float],
) -> tuple[int, int, int]:
    a = fg[3]
    if a >= 0.999:
        return (fg[0], fg[1], fg[2])
    r = int(round(fg[0] * a + bg[0] * (1.0 - a)))
    g = int(round(fg[1] * a + bg[1] * (1.0 - a)))
    b = int(round(fg[2] * a + bg[2] * (1.0 - a)))
    return (r, g, b)


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        s = c / 255.0
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = channel(rgb[0]), channel(rgb[1]), channel(rgb[2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(
    fg: tuple[int, int, int, float],
    bg: tuple[int, int, int, float],
) -> float:
    bg_rgb = (bg[0], bg[1], bg[2])
    fg_rgb = composite_over(fg, bg)
    l1 = relative_luminance(fg_rgb)
    l2 = relative_luminance(bg_rgb)
    hi, lo = (l1, l2) if l1 >= l2 else (l2, l1)
    return (hi + 0.05) / (lo + 0.05)


def parse_px_value(val: Any) -> float | None:
    if isinstance(val, (int, float)):
        return float(val)
    if not isinstance(val, str):
        return None
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(px|pt|dp|sp|rem|em)?\s*$", val, re.I)
    if not m:
        return None
    num = float(m.group(1))
    unit = (m.group(2) or "px").lower()
    if unit in ("rem", "em"):
        return num * 16.0
    return num


def parse_ms_value(val: Any) -> float | None:
    if isinstance(val, (int, float)):
        return float(val)
    if not isinstance(val, str):
        return None
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s)\s*$", val, re.I)
    if not m:
        return None
    num = float(m.group(1))
    return num * 1000.0 if m.group(2).lower() == "s" else num


def parse_font_weight(val: Any) -> int | None:
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        if s.isdigit():
            return int(s)
        table = {
            "ultralight": 100,
            "thin": 100,
            "extralight": 200,
            "light": 300,
            "regular": 400,
            "normal": 400,
            "medium": 500,
            "semibold": 600,
            "demibold": 600,
            "bold": 700,
            "extrabold": 800,
            "heavy": 800,
            "black": 900,
        }
        return table.get(s)
    return None


def primary_font_name(font_stack: str) -> str:
    first = font_stack.split(",", 1)[0].strip().strip('"').strip("'")
    return first


def is_display_only_font(font_stack: str) -> bool:
    return primary_font_name(font_stack).lower() in DISPLAY_ONLY_FONTS


def is_custom_font_stack(font_stack: str) -> bool:
    first = primary_font_name(font_stack).lower()
    return bool(first) and first not in SYSTEM_FONT_KEYWORDS


# ---------------------------------------------------------------------------
# Markdown + YAML + CSS + Prose token extractors
# ---------------------------------------------------------------------------

def merge_token_trees(
    base: dict[str, Any],
    incoming: dict[str, Any],
    base_lines: dict[str, int],
    inc_lines: dict[str, int],
    duplicates: list[tuple[str, int, int]],
    prefix: str = "",
) -> None:
    for k, v in incoming.items():
        dotted = f"{prefix}.{k}" if prefix else k
        if k in base:
            if isinstance(base[k], dict) and isinstance(v, dict):
                merge_token_trees(base[k], v, base_lines, inc_lines, duplicates, dotted)
            else:
                duplicates.append((dotted, base_lines.get(dotted, 1), inc_lines.get(dotted, 1)))
                base[k] = v
                if dotted in inc_lines:
                    base_lines[dotted] = inc_lines[dotted]
        else:
            base[k] = v
            if dotted in inc_lines:
                base_lines[dotted] = inc_lines[dotted]
            for sub_path, lno in inc_lines.items():
                if sub_path == dotted or sub_path.startswith(dotted + "."):
                    base_lines[sub_path] = lno


def normalize_heading(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"^\d+\.\s*", "", s)
    s = s.replace("’", "'").replace("`", "")
    s = re.sub(r"\s+", " ", s)
    return s


def canonical_section_id(heading: str) -> str | None:
    norm = normalize_heading(heading)
    for sid, _, aliases in CANONICAL_SECTIONS:
        if norm in aliases:
            return sid
    return None


def opendesign_section_id(heading: str) -> str | None:
    norm = normalize_heading(heading)
    for sid, _, aliases in OPENDESIGN_SECTIONS:
        if norm in aliases:
            return sid
    return None


def parse_markdown_doc(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()

    tokens: dict[str, Any] = {}
    token_lines: dict[str, int] = {}
    duplicates: list[tuple[str, int, int]] = []
    yaml_errors: list[tuple[int, str]] = []
    yaml_block_count = 0

    body_start_idx = 0
    has_frontmatter = False

    if lines and lines[0].strip() == "---":
        close_idx = None
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                close_idx = i
                break
        if close_idx is not None:
            has_frontmatter = True
            yaml_block_count += 1
            fm_text = "\n".join(lines[1:close_idx])
            fm_data, fm_lines, fm_dups, fm_errs = parse_yaml_with_lines(fm_text, start_line=2)
            merge_token_trees(tokens, fm_data, token_lines, fm_lines, duplicates)
            duplicates.extend(fm_dups)
            yaml_errors.extend(fm_errs)
            body_start_idx = close_idx + 1
        else:
            yaml_errors.append((1, "Unclosed YAML frontmatter delimiter `---`."))

    sections: list[dict[str, Any]] = []
    h1_title: tuple[int, str] | None = None
    external_refs: list[tuple[int, str]] = []
    in_fence = False
    fence_lang = ""
    fence_start_line = 0
    fence_buf: list[str] = []
    current_section: dict[str, Any] | None = None

    for idx in range(body_start_idx, len(lines)):
        line_no = idx + 1
        line = lines[idx]
        stripped = line.strip()

        if not in_fence and stripped.startswith("```"):
            in_fence = True
            fence_lang = stripped[3:].strip().lower().split()[0] if stripped[3:].strip() else ""
            fence_start_line = line_no + 1
            fence_buf = []
            continue
        if in_fence:
            if stripped.startswith("```"):
                in_fence = False
                if fence_lang in ("yaml", "yml"):
                    block_text = "\n".join(fence_buf)
                    blk_data, blk_lines, blk_dups, blk_errs = parse_yaml_with_lines(
                        block_text, start_line=fence_start_line
                    )
                    if blk_data and any(k in GOOGLE_TOP_LEVEL_FIELDS for k in blk_data):
                        yaml_block_count += 1
                        merge_token_trees(tokens, blk_data, token_lines, blk_lines, duplicates)
                        duplicates.extend(blk_dups)
                        yaml_errors.extend(blk_errs)
                fence_lang = ""
                fence_buf = []
            else:
                fence_buf.append(line)
            continue

        hm = re.match(r"^(#{1,3})\s+(.+?)\s*#*$", line)
        if hm:
            level = len(hm.group(1))
            title = hm.group(2).strip()
            if level == 1 and h1_title is None:
                h1_title = (line_no, title)
            elif level == 2:
                current_section = {
                    "heading": title,
                    "id": canonical_section_id(title),
                    "od_id": opendesign_section_id(title),
                    "line": line_no,
                    "subsections": [],
                    "body_lines": [],
                }
                sections.append(current_section)
            elif level == 3 and current_section is not None:
                current_section["subsections"].append({"heading": title, "line": line_no})
                current_section["body_lines"].append((line_no, line))
            continue

        if current_section is not None:
            current_section["body_lines"].append((line_no, line))

        for lm in re.finditer(r"\[[^\]]+\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)", line):
            target = lm.group(1).strip()
            if not target.startswith(("http://", "https://", "mailto:", "#")):
                external_refs.append((line_no, target))

    return {
        "path": path,
        "raw": raw,
        "lines": lines,
        "has_frontmatter": has_frontmatter,
        "yaml_block_count": yaml_block_count,
        "tokens": tokens,
        "token_lines": token_lines,
        "duplicates": duplicates,
        "yaml_errors": yaml_errors,
        "h1": h1_title,
        "sections": sections,
        "external_refs": external_refs,
    }


def extract_prose_color_bullets(doc: dict[str, Any]) -> dict[str, tuple[str, int]]:
    """
    Extract bullet color declarations from Open Design / Claude Markdown prose, e.g.:
      - **Primary:** `#60a5fa` — ...
      - **Surface:** `#131b2f` — ...
    Returns lowercase role -> (hex_value, line_no).
    """
    out: dict[str, tuple[str, int]] = {}
    for sec in doc["sections"]:
        if sec.get("od_id") == "color" or sec.get("id") == "colors":
            for lno, line in sec["body_lines"]:
                m = re.match(
                    r"^\s*[-*]\s+\*\*([A-Za-z0-9 _-]+?):?\*\*:?\s*`?(#[0-9a-fA-F]{3,8})`?",
                    line,
                )
                if m:
                    role = m.group(1).strip().lower()
                    out[role] = (m.group(2).strip().lower(), lno)
    return out


def extract_markdown_motion_table(doc: dict[str, Any]) -> list[tuple[str, str, int]]:
    """
    Extract motion tokens from Claude Design README.md `## Motion` markdown tables:
      | `--motion-scene` | `880ms` |
    """
    rows: list[tuple[str, str, int]] = []
    for sec in doc["sections"]:
        if sec.get("id") == "motion" or sec.get("od_id") == "motion":
            for lno, line in sec["body_lines"]:
                m = re.match(r"^\|\s*`?(--[A-Za-z0-9_-]+)`?\s*\|\s*`?([^|`]+?)`?\s*\|", line.strip())
                if m:
                    rows.append((m.group(1).strip(), m.group(2).strip(), lno))
    return rows


def parse_tokens_css(path: Path) -> dict[str, Any]:
    """
    Parse CSS custom properties inside :root blocks in `tokens.css`.
    Preserves line numbers and records later-block overrides (e.g. font overrides).
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Strip /* ... */ comments while keeping newlines for exact line numbers
    cleaned_chars: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i : i + 2] == "/*":
            end = raw.find("*/", i + 2)
            if end == -1:
                end = n
            cleaned_chars.append("\n" * raw[i:end].count("\n"))
            i = end + 2
        else:
            cleaned_chars.append(raw[i])
            i += 1
    cleaned = "".join(cleaned_chars)

    props: dict[str, str] = {}
    prop_lines: dict[str, int] = {}
    overrides: list[tuple[str, str, int, str, int]] = []  # (name, old_val, old_line, new_val, new_line)

    # Walk line-by-line tracking :root { ... } blocks
    in_root = False
    brace_depth = 0
    for lno, line in enumerate(cleaned.splitlines(), start=1):
        stripped = line.strip()
        if not in_root:
            if re.search(r":root\b", stripped) and "{" in stripped:
                in_root = True
                brace_depth = stripped.count("{") - stripped.count("}")
                if brace_depth <= 0:
                    in_root = False
            continue
        brace_depth += stripped.count("{") - stripped.count("}")
        for m in re.finditer(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);", line):
            name = m.group(1).strip()
            val = m.group(2).strip()
            if name in props and props[name] != val:
                overrides.append((name, props[name], prop_lines[name], val, lno))
            props[name] = val
            prop_lines[name] = lno
        if brace_depth <= 0:
            in_root = False

    def resolve_css(val: str, seen: set[str] | None = None) -> str:
        s = seen or set()
        def repl(match: re.Match[str]) -> str:
            var_name = match.group(1)
            fallback = match.group(2)
            if var_name in props and var_name not in s:
                return resolve_css(props[var_name], s | {var_name})
            return fallback.strip() if fallback else match.group(0)
        return CSS_VAR_RE.sub(repl, val)

    resolved = {k: resolve_css(v) for k, v in props.items()}
    return {
        "path": path,
        "raw": raw,
        "props": props,
        "resolved": resolved,
        "lines": prop_lines,
        "overrides": overrides,
    }


def lookup_dotted(tree: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    cur: Any = tree
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def resolve_token_value(
    tree: dict[str, Any],
    val: Any,
    stack: list[str] | None = None,
) -> tuple[Any, str | None, list[str]]:
    chain = list(stack or [])
    if not isinstance(val, str):
        return val, None, chain
    m = TOKEN_REF_RE.match(val.strip())
    if not m:
        return val, None, chain
    ref_path = m.group(1)
    if ref_path in chain:
        return val, "cycle", chain + [ref_path]
    found, target = lookup_dotted(tree, ref_path)
    if not found:
        return val, "missing", chain + [ref_path]
    return resolve_token_value(tree, target, chain + [ref_path])


def iter_leaf_tokens(tree: Any, prefix: str = "") -> list[tuple[str, Any]]:
    leaves: list[tuple[str, Any]] = []
    if isinstance(tree, dict):
        for k, v in tree.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                leaves.extend(iter_leaf_tokens(v, p))
            else:
                leaves.append((p, v))
    return leaves


# ---------------------------------------------------------------------------
# Shared normalized semantic contrast checker (Open Design & Claude Design)
# ---------------------------------------------------------------------------

def evaluate_flat_semantic_palette(
    color_map: dict[str, tuple[str, int, str]],  # name -> (value, line, note)
    *,
    theme_label: str,
    file_path: Path,
    contrast_rows: list[dict[str, Any]],
    add_finding: Any,
) -> None:
    """
    Evaluate WCAG 2.2 contrast across Open Design / Claude Design normalized color slots:
      bg, surface, surface-warm, fg, fg-2, muted, meta, accent, accent-on,
      accent-hover, accent-active, success, warn, danger.
    """
    def check(pair_label: str, fg_key: str, bg_key: str, min_ratio: float = 4.5, severity_fail: str = "critical") -> None:
        if fg_key not in color_map or bg_key not in color_map:
            return
        fg_val, fg_line, fg_note = color_map[fg_key]
        bg_val, _, _ = color_map[bg_key]
        fg_rgba = parse_color_rgba(fg_val)
        bg_rgba = parse_color_rgba(bg_val)
        if fg_rgba is None or bg_rgba is None:
            return
        ratio = contrast_ratio(fg_rgba, bg_rgba)
        passed = ratio >= min_ratio
        full_label = f"{theme_label}.{pair_label}" if theme_label else pair_label
        contrast_rows.append(
            {
                "pair": full_label,
                "fg": fg_val,
                "bg": bg_val,
                "ratio": round(ratio, 2),
                "required": min_ratio,
                "pass": passed,
                "line": fg_line,
            }
        )
        if not passed:
            citation = "dark-mode.md › Dark Mode colors" if "dark" in theme_label.lower() else "accessibility.md › Vision"
            note_suffix = f" [token note: {fg_note}]" if fg_note and "below" in fg_note.lower() else ""
            add_finding(
                "DESIGN-A11Y-001",
                severity_fail,
                fg_line,
                f"Low color contrast for `{full_label}` (`{fg_val}` on `{bg_val}`): {ratio:.2f}:1 < {min_ratio:.1f}:1.{note_suffix}",
                f"Adjust `{fg_key}` or `{bg_key}` luminance so `{full_label}` reaches at least {min_ratio:.1f}:1 (or restrict usage to large bold >= 18 pt / 24 px where >= 3.0:1 applies).",
                citation,
                file_path=file_path,
            )

    bg_val = color_map.get("bg", ("", 1, ""))[0]
    surf_val = color_map.get("surface", ("", 1, ""))[0]
    distinct_surface = bool(surf_val and surf_val.lower() != bg_val.lower())

    for fg_key, sev in [("fg", "critical"), ("fg-2", "critical"), ("muted", "high"), ("meta", "high")]:
        check(f"{fg_key} / bg", fg_key, "bg", 4.5, sev)
        if distinct_surface:
            check(f"{fg_key} / surface", fg_key, "surface", 4.5, sev)

    check("accent-on / accent", "accent-on", "accent", 4.5, "critical")
    if "accent-hover" in color_map:
        check("accent-on / accent-hover", "accent-on", "accent-hover", 4.5, "high")
    if "accent-active" in color_map:
        check("accent-on / accent-active", "accent-on", "accent-active", 4.5, "high")

    for st_key in ("success", "warn", "warning", "danger", "error"):
        check(f"{st_key} / bg", st_key, "bg", 4.5, "high")
        if distinct_surface:
            check(f"{st_key} / surface", st_key, "surface", 4.5, "high")


# ---------------------------------------------------------------------------
# Format detection & target resolution
# ---------------------------------------------------------------------------

def detect_package_context(target: Path, profile_arg: str) -> dict[str, Any]:
    base_dir = target if target.is_dir() else target.parent
    design_md = None
    for name in ("DESIGN.md", "design.md"):
        cand = base_dir / name
        if cand.exists():
            design_md = cand
            break
    if target.is_file() and target.name.lower() == "design.md":
        design_md = target

    manifest_json = base_dir / "manifest.json"
    tokens_css = base_dir / "tokens.css"
    design_tokens_json = base_dir / "design-tokens.json"
    claude_ds_json = base_dir / "design-system.json"
    claude_tokens_json = base_dir / "tokens.json"
    readme_md = base_dir / "README.md"
    if not readme_md.exists() and (base_dir / "readme.md").exists():
        readme_md = base_dir / "readme.md"
    extensions_yaml = base_dir / "design.extensions.yaml"
    evidence_json = base_dir / "DESIGN.evidence.json"
    theme_json = base_dir / "theme.json"
    styles_css = base_dir / "styles.css"

    is_claude_tokens = False
    if claude_tokens_json.exists():
        try:
            tj = json.loads(claude_tokens_json.read_text(encoding="utf-8"))
            if isinstance(tj, dict) and ("color" in tj or "type" in tj):
                is_claude_tokens = True
        except Exception:
            pass

    is_od_manifest = False
    if manifest_json.exists():
        try:
            mj = json.loads(manifest_json.read_text(encoding="utf-8"))
            if isinstance(mj, dict) and str(mj.get("schemaVersion", "")).startswith("od-design-system"):
                is_od_manifest = True
        except Exception:
            pass

    detected = profile_arg
    if detected == "package":
        detected = "opendesign"
    if detected == "auto":
        if target.is_file() and target.name in ("design-system.json", "tokens.json"):
            detected = "claude"
        elif claude_ds_json.exists() or (is_claude_tokens and not design_md):
            detected = "claude"
        elif is_od_manifest or design_tokens_json.exists() or (tokens_css.exists() and not evidence_json.exists()):
            detected = "opendesign"
        elif evidence_json.exists() or (theme_json.exists() and design_md is not None):
            detected = "capture"
        elif extensions_yaml.exists():
            detected = "extended"
        else:
            detected = "core"

    return {
        "target": target,
        "base_dir": base_dir,
        "profile": detected,
        "design_md": design_md,
        "manifest_json": manifest_json,
        "tokens_css": tokens_css,
        "design_tokens_json": design_tokens_json,
        "claude_ds_json": claude_ds_json,
        "claude_tokens_json": claude_tokens_json,
        "readme_md": readme_md,
        "extensions_yaml": extensions_yaml,
        "evidence_json": evidence_json,
        "theme_json": theme_json,
        "styles_css": styles_css,
    }


def detect_platforms_generic(
    text_blob: str,
    platform_arg: str,
    *,
    ext_data: dict[str, Any] | None = None,
    default_platforms: set[str] | None = None,
) -> set[str]:
    if platform_arg != "auto":
        if platform_arg == "all":
            return {"ios", "ipados", "macos", "web", "android"}
        return {p.strip().lower() for p in platform_arg.split(",") if p.strip()}

    found: set[str] = set()
    if ext_data and isinstance(ext_data.get("platforms"), dict):
        for k in ext_data["platforms"]:
            kl = str(k).lower()
            if kl in ("ios", "ipados", "macos", "web", "android"):
                found.add(kl)
    if re.search(r"\biOS\b", text_blob):
        found.add("ios")
    if re.search(r"\biPadOS\b", text_blob):
        found.add("ipados")
    if re.search(r"\bmacOS\b", text_blob):
        found.add("macos")
    if re.search(r"\bAndroid\b", text_blob, re.I):
        found.add("android")
    if re.search(r"\bWeb\b", text_blob):
        found.add("web")
    return found or (default_platforms or {"ios", "macos", "web"})


# ---------------------------------------------------------------------------
# Open Design Package Auditor (`opendesign`)
# ---------------------------------------------------------------------------

def audit_opendesign_package(ctx: dict[str, Any], *, platform_arg: str) -> dict[str, Any]:
    base_dir: Path = ctx["base_dir"]
    manifest_file: Path = ctx["manifest_json"]
    tokens_css_file: Path = ctx["tokens_css"]
    design_md_file: Path | None = ctx["design_md"]
    design_tokens_file: Path = ctx["design_tokens_json"]

    findings: list[dict[str, Any]] = []
    contrast_rows: list[dict[str, Any]] = []

    def add(rule_id: str, severity: str, line: int, message: str, fix: str, citation: str, file_path: Path = base_dir) -> None:
        findings.append(
            {
                "id": rule_id,
                "severity": severity,
                "file": str(file_path),
                "line": max(1, int(line)),
                "message": message,
                "fix": fix,
                "citation": citation,
            }
        )

    manifest: dict[str, Any] = {}
    ds_name = base_dir.name
    if not manifest_file.exists():
        add(
            "DESIGN-PKG-001",
            "high",
            1,
            "Open Design package missing `manifest.json`.",
            'Add `manifest.json` with `schemaVersion: "od-design-system-project/v1"`, `id`, `name`, `files: { design, tokens }`.',
            "Open Design › Package schema",
            file_path=base_dir / "manifest.json",
        )
    else:
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            ds_name = manifest.get("name") or ds_name
            for req in ("schemaVersion", "id", "name", "files"):
                if req not in manifest:
                    add("DESIGN-PKG-001", "high", 1, f"`manifest.json` missing required field `{req}`.", f"Add `{req}` to `manifest.json`.", "Open Design › Manifest schema", file_path=manifest_file)
            files_map = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
            for key in ("design", "tokens"):
                if not files_map.get(key):
                    add("DESIGN-PKG-001", "high", 1, f"`manifest.json` missing `files.{key}`.", f"Declare `files.{key}` in `manifest.json`.", "Open Design › Manifest schema", file_path=manifest_file)
            for key, rel in files_map.items():
                if isinstance(rel, str) and not (base_dir / rel).exists():
                    add("DESIGN-PKG-001", "high", 1, f"`manifest.json` references missing file `files.{key}: {rel}`.", f"Create or regenerate `{rel}`.", "Open Design › Package schema", file_path=manifest_file)
            for extra_key in ("usage", "componentsManifest"):
                rel = manifest.get(extra_key)
                if isinstance(rel, str) and not (base_dir / rel).exists():
                    add("DESIGN-PKG-001", "medium", 1, f"`manifest.json` references missing companion `{extra_key}: {rel}`.", f"Generate `{rel}` or remove the stale manifest pointer.", "Open Design › Package schema", file_path=manifest_file)
        except json.JSONDecodeError as exc:
            add("DESIGN-PKG-001", "critical", 1, f"Invalid JSON in `manifest.json`: {exc}.", "Fix JSON syntax in `manifest.json`.", "Open Design › Manifest schema", file_path=manifest_file)

    # Detect target platforms (Open Design defaults to web unless Apple platforms are declared or passed via --platform)
    doc = parse_markdown_doc(design_md_file) if (design_md_file and design_md_file.exists()) else None
    blob = (doc["raw"] if doc else "") + " " + json.dumps(manifest)
    platforms = detect_platforms_generic(blob, platform_arg, default_platforms={"web"})
    apple_targeted = bool(platforms & {"ios", "ipados", "macos"})

    # Parse tokens.css
    css_data = None
    if not tokens_css_file.exists():
        add("DESIGN-PKG-001", "high", 1, "Open Design package missing `tokens.css`.", "Provide normalized CSS custom properties in `tokens.css`.", "Open Design › Token contract", file_path=tokens_css_file)
    else:
        css_data = parse_tokens_css(tokens_css_file)
        res = css_data["resolved"]
        clines = css_data["lines"]

        # Flag conflicting later :root overrides (e.g. Inter -> Playfair Display)
        for prop_name, old_val, old_line, new_val, new_line in css_data["overrides"]:
            add(
                "DESIGN-OD-003",
                "medium",
                new_line,
                f"`tokens.css` redefines `{prop_name}` from `{old_val}` (line {old_line}) to `{new_val}`.",
                f"Consolidate `{prop_name}` into a single canonical `:root` declaration so tools and humans read the same value.",
                "Open Design › Token contract",
                file_path=tokens_css_file,
            )

        # Infer light vs dark polarity from --bg luminance
        theme_label = "theme"
        bg_rgba = parse_color_rgba(res.get("--bg", "")) if "--bg" in res else None
        if bg_rgba is not None:
            theme_label = "dark" if relative_luminance((bg_rgba[0], bg_rgba[1], bg_rgba[2])) < 0.25 else "light"

        # Single-theme dark-mode check
        has_alt_theme = bool(re.search(r"prefers-color-scheme|data-theme|\[theme=|\.dark\b|\.light\b", css_data["raw"]))
        if not has_alt_theme:
            sev = "high" if apple_targeted else "low"
            missing_mode = "light" if theme_label == "dark" else "dark"
            add(
                "DESIGN-COLOR-001",
                sev,
                clines.get("--bg", 1),
                f"Single-theme Open Design package (`{theme_label}` only; no `{missing_mode}` mode tokens in `tokens.css`).",
                f"Add `{missing_mode}` mode overrides (or `@media (prefers-color-scheme: {missing_mode})`) if targeting adaptive Light/Dark apps.",
                "dark-mode.md › Best practices",
                file_path=tokens_css_file,
            )

        # Build color map for contrast checks
        color_map: dict[str, tuple[str, int, str]] = {}
        for slot in ("bg", "surface", "surface-warm", "fg", "fg-2", "muted", "meta", "accent", "accent-on", "accent-hover", "accent-active", "success", "warn", "danger"):
            css_key = f"--{slot}"
            if css_key in res:
                color_map[slot] = (res[css_key], clines.get(css_key, 1), "")
        evaluate_flat_semantic_palette(
            color_map,
            theme_label=theme_label,
            file_path=tokens_css_file,
            contrast_rows=contrast_rows,
            add_finding=add,
        )

        # Check typography tokens in tokens.css
        min_floor = 11.0 if (platforms & {"ios", "ipados"}) else (10.0 if "macos" in platforms else 11.0)
        for k, v in res.items():
            if k.startswith("--text-"):
                px = parse_px_value(v)
                if px is not None:
                    if px < min_floor:
                        add("DESIGN-TYPO-001", "critical", clines.get(k, 1), f"`{k}: {v}` is below minimum legible floor ({int(min_floor)}px/pt).", f"Raise `{k}` to at least `{int(min_floor)}px`.", "typography.md › Ensuring legibility", file_path=tokens_css_file)
                    elif k == "--text-base" and (platforms & {"ios", "ipados"}) and px < 16.0:
                        add("DESIGN-TYPO-001", "medium", clines.get(k, 1), f"`--text-base: {v}` is smaller than Apple's 17 pt iOS/iPadOS body default.", "Map iOS/iPadOS body copy to Dynamic Type `.body` (17 pt default).", "typography.md › Platform considerations", file_path=tokens_css_file)

        body_font = res.get("--font-body", "")
        if body_font and is_display_only_font(body_font):
            add(
                "DESIGN-TYPO-004",
                "high",
                clines.get("--font-body", 1),
                f"`--font-body` uses display typeface `{primary_font_name(body_font)}` for UI/body text.",
                "Reserve high-contrast display serifs for large headings and pair `--font-body` with a legible text face (e.g. SF Pro / system sans or readable text serif).",
                "typography.md › Ensuring legibility",
                file_path=tokens_css_file,
            )

        lead_body = res.get("--leading-body")
        if lead_body:
            try:
                if float(lead_body) < 1.25:
                    add("DESIGN-TYPO-003", "medium", clines.get("--leading-body", 1), f"`--leading-body: {lead_body}` is tight for multi-line reading.", "Use >= 1.35–1.55 line-height for body text.", "typography.md › Ensuring legibility", file_path=tokens_css_file)
            except ValueError:
                pass

        # Motion duration & focus ring checks
        for k, v in res.items():
            if k.startswith("--motion-"):
                ms = parse_ms_value(v)
                if ms is not None and ms > 500:
                    add("DESIGN-MOTION-002", "medium", clines.get(k, 1), f"`{k}: {v}` exceeds 500ms and may feel sluggish for UI interactions.", "Keep interactive UI transitions within 100–350ms (reserve longer durations for non-blocking ambient scenes).", "motion.md › Best practices", file_path=tokens_css_file)

        if "--focus-ring" not in res:
            add("DESIGN-COMP-001", "medium", 1, "`tokens.css` does not define a `--focus-ring` token for keyboard focus visibility.", "Add `--focus-ring` with high-contrast outline/ring styling for `:focus-visible`.", "accessibility.md › Mobility", file_path=tokens_css_file)

    # Check design-tokens.json sync with tokens.css
    if design_tokens_file.exists():
        try:
            dt = json.loads(design_tokens_file.read_text(encoding="utf-8"))
            summary = dt.get("summary", {}) if isinstance(dt, dict) else {}
            if summary.get("recommendRebuild") is True:
                add("DESIGN-OD-002", "medium", 1, "`design-tokens.json` reports `summary.recommendRebuild: true`.", "Regenerate `design-tokens.json` from `tokens.css` and `DESIGN.md`.", "Open Design › Token contract", file_path=design_tokens_file)
            if css_data and isinstance(dt.get("tokens"), list):
                dt_map = {t.get("name"): str(t.get("value", "")) for t in dt["tokens"] if isinstance(t, dict) and t.get("name")}
                mismatches = [
                    (k, css_data["props"][k], dt_map[k])
                    for k in css_data["props"]
                    if k in dt_map and css_data["props"][k].strip().lower() != dt_map[k].strip().lower()
                ]
                for prop_k, css_v, dt_v in mismatches[:5]:
                    add(
                        "DESIGN-OD-002",
                        "high",
                        css_data["lines"].get(prop_k, 1),
                        f"Token drift between `tokens.css` (`{prop_k}: {css_v}`) and `design-tokens.json` (`{dt_v}`).",
                        "Re-export `design-tokens.json` from `tokens.css` so compiled tokens never diverge from source tokens.",
                        "Open Design › Token contract",
                        file_path=design_tokens_file,
                    )
        except json.JSONDecodeError as exc:
            add("DESIGN-PKG-001", "high", 1, f"Invalid JSON in `design-tokens.json`: {exc}.", "Fix or regenerate `design-tokens.json`.", "Open Design › Token contract", file_path=design_tokens_file)

    # Audit companion Open Design DESIGN.md prose & cross-file color drift
    if doc is None:
        add("DESIGN-PKG-001", "high", 1, "Open Design package missing `DESIGN.md`.", "Include `DESIGN.md` alongside `manifest.json` and `tokens.css`.", "Open Design › Package schema", file_path=base_dir / "DESIGN.md")
    else:
        if css_data:
            prose_colors = extract_prose_color_bullets(doc)
            role_to_css = {
                "primary": "--accent",
                "surface": "--surface",
                "text": "--fg",
                "background": "--bg",
                "success": "--success",
                "warning": "--warn",
                "danger": "--danger",
                "error": "--danger",
            }
            for role, (hex_val, lno) in prose_colors.items():
                css_var = role_to_css.get(role)
                if css_var and css_var in css_data["resolved"]:
                    css_hex = css_data["resolved"][css_var].strip().lower()
                    if HEX_COLOR_RE.match(css_hex) and css_hex != hex_val:
                        add(
                            "DESIGN-OD-001",
                            "high",
                            lno,
                            f"Cross-file color drift: `DESIGN.md` `{role.title()}` is `{hex_val}`, but `tokens.css` `{css_var}` is `{css_hex}`.",
                            f"Align `DESIGN.md` and `tokens.css` `{css_var}` to a single canonical hex value.",
                            "Open Design › Single source of truth",
                            file_path=doc["path"],
                        )

        # Reduced motion check
        if not re.search(r"reduced[- ]motion|prefers-reduced-motion|reduce motion", doc["raw"] + (css_data["raw"] if css_data else ""), re.I):
            add(
                "DESIGN-MOTION-001",
                "high",
                _section_line(doc, "motion"),
                "Open Design package defines motion tokens/guidance without a Reduced Motion fallback.",
                "Add `@media (prefers-reduced-motion: reduce)` guidance in `DESIGN.md` `## 7. Motion & Interaction` (and `tokens.css` if applicable).",
                "motion.md › Best practices",
                file_path=doc["path"],
            )

        # Check Apple platform requirements if Apple platforms were explicitly requested/declared
        if apple_targeted:
            if not re.search(r"dynamic type|text scaling|system font|sf pro", doc["raw"], re.I):
                add("DESIGN-TYPO-003", "high", _section_line(doc, "typography"), "Apple platforms targeted, but `DESIGN.md` omits Dynamic Type / text-scaling guidance.", "Specify Dynamic Type mappings and 200% text enlargement support.", "typography.md › Supporting Dynamic Type", file_path=doc["path"])
            if not re.search(r"44\s*(?:pt|px|×|x)|safe area|touch target|hit target", doc["raw"], re.I):
                add("DESIGN-LAYOUT-001", "medium", _section_line(doc, "layout"), "Apple platforms targeted, but `DESIGN.md` omits hit-target (44×44 pt / 28×28 pt) and safe-area rules.", "Document minimum hit targets and safe-area insets for iOS/macOS.", "accessibility.md › Mobility", file_path=doc["path"])

    findings.sort(key=lambda f: (-SEVERITY_RANK.get(f["severity"], 0), f["file"], f["line"], f["id"]))
    return {
        "file": str(base_dir),
        "profile": "opendesign",
        "platforms": sorted(platforms),
        "tokens_summary": {
            "name": ds_name,
            "schemaVersion": manifest.get("schemaVersion"),
            "has_tokens_css": tokens_css_file.exists(),
            "has_design_tokens_json": design_tokens_file.exists(),
            "has_design_md": doc is not None,
            "css_token_count": len(css_data["props"]) if css_data else 0,
        },
        "contrast": contrast_rows,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Claude Design System Package Auditor (`claude`)
# ---------------------------------------------------------------------------

def audit_claude_package(ctx: dict[str, Any], *, platform_arg: str) -> dict[str, Any]:
    base_dir: Path = ctx["base_dir"]
    ds_json_file: Path = ctx["claude_ds_json"]
    tokens_json_file: Path = ctx["claude_tokens_json"]
    readme_file: Path = ctx["readme_md"]

    findings: list[dict[str, Any]] = []
    contrast_rows: list[dict[str, Any]] = []

    def add(rule_id: str, severity: str, line: int, message: str, fix: str, citation: str, file_path: Path = base_dir) -> None:
        findings.append(
            {
                "id": rule_id,
                "severity": severity,
                "file": str(file_path),
                "line": max(1, int(line)),
                "message": message,
                "fix": fix,
                "citation": citation,
            }
        )

    ds_meta: dict[str, Any] = {}
    ds_name = base_dir.name
    if ds_json_file.exists():
        try:
            ds_meta = json.loads(ds_json_file.read_text(encoding="utf-8"))
            ds_name = ds_meta.get("title") or ds_name
            for req in ("v", "title", "namespace"):
                if req not in ds_meta:
                    add("DESIGN-CLAUDE-001", "medium", 1, f"`design-system.json` missing field `{req}`.", f"Add `{req}` to `design-system.json`.", "Claude Design › design-system.json", file_path=ds_json_file)
        except json.JSONDecodeError as exc:
            add("DESIGN-CLAUDE-001", "critical", 1, f"Invalid JSON in `design-system.json`: {exc}.", "Fix JSON syntax in `design-system.json`.", "Claude Design › design-system.json", file_path=ds_json_file)
    else:
        add("DESIGN-CLAUDE-001", "medium", 1, "Claude Design package missing `design-system.json`.", "Include `design-system.json` (`v: 3`, `layout: \"files\"`, `title`, `namespace`).", "Claude Design › Package structure", file_path=ds_json_file)

    readme_doc = parse_markdown_doc(readme_file) if readme_file.exists() else None
    blob = (readme_doc["raw"] if readme_doc else "") + " " + json.dumps(ds_meta)
    platforms = detect_platforms_generic(blob, platform_arg, default_platforms={"web"})
    apple_targeted = bool(platforms & {"ios", "ipados", "macos"})

    tokens_data: dict[str, Any] = {}
    if not tokens_json_file.exists():
        add("DESIGN-CLAUDE-001", "critical", 1, "Claude Design package missing `tokens.json`.", "Provide `tokens.json` with `color`, `type`, `spacing`, `radius`, and `shadow` groups.", "Claude Design › tokens.json", file_path=tokens_json_file)
    else:
        try:
            tokens_data = json.loads(tokens_json_file.read_text(encoding="utf-8"))
            ds_name = tokens_data.get("name") or ds_name
        except json.JSONDecodeError as exc:
            add("DESIGN-CLAUDE-001", "critical", 1, f"Invalid JSON in `tokens.json`: {exc}.", "Fix JSON syntax in `tokens.json`.", "Claude Design › tokens.json", file_path=tokens_json_file)

    # Line lookup helper for tokens.json
    tokens_raw_lines = tokens_json_file.read_text(encoding="utf-8", errors="replace").splitlines() if tokens_json_file.exists() else []
    def find_json_line(token_name: str) -> int:
        needle = f'"name": "{token_name}"'
        for idx, line in enumerate(tokens_raw_lines, start=1):
            if needle in line:
                return idx
        return 1

    # 1. Color themes & contrast audit
    color_section = tokens_data.get("color") if isinstance(tokens_data.get("color"), dict) else {}
    themes = color_section.get("themes") if isinstance(color_section.get("themes"), list) else []
    color_tokens = color_section.get("tokens") if isinstance(color_section.get("tokens"), list) else []

    if len(themes) <= 1:
        only_id = themes[0].get("id", "single") if themes and isinstance(themes[0], dict) else "single"
        sev = "high" if apple_targeted else "low"
        add(
            "DESIGN-COLOR-001",
            sev,
            1,
            f"Claude Design `tokens.json` defines only 1 color theme (`{only_id}`); no alternate Light/Dark theme.",
            "Add both `light` and `dark` theme entries with per-theme values when targeting adaptive apps.",
            "dark-mode.md › Best practices",
            file_path=tokens_json_file,
        )

    # Build per-theme color maps (supports both single `value` and multi-theme `values: {id: hex}`)
    theme_ids = [t.get("id", "default") for t in themes if isinstance(t, dict)] or ["default"]
    first_theme_colors: dict[str, str] = {}
    for tid in theme_ids:
        cmap: dict[str, tuple[str, int, str]] = {}
        for tok in color_tokens:
            if not isinstance(tok, dict) or not tok.get("name"):
                continue
            tname = str(tok["name"]).strip()
            tusage = str(tok.get("usage", ""))
            tval = None
            if isinstance(tok.get("values"), dict) and tid in tok["values"]:
                tval = str(tok["values"][tid])
            elif isinstance(tok.get("value"), str):
                tval = tok["value"]
            if tval:
                lno = find_json_line(tname)
                cmap[tname] = (tval, lno, tusage)
                if tid == theme_ids[0]:
                    first_theme_colors[tname] = tval.strip().lower()
        evaluate_flat_semantic_palette(
            cmap,
            theme_label=tid,
            file_path=tokens_json_file,
            contrast_rows=contrast_rows,
            add_finding=add,
        )

    # 2. Typography audit (`type.families`, `type.fonts`, `type.groups[].styles[]`)
    type_sec = tokens_data.get("type") if isinstance(tokens_data.get("type"), dict) else {}
    families = type_sec.get("families") if isinstance(type_sec.get("families"), dict) else {}
    bundled_fonts = type_sec.get("fonts") if isinstance(type_sec.get("fonts"), list) else []

    body_fam = str(families.get("body", ""))
    if body_fam and is_display_only_font(body_fam):
        add(
            "DESIGN-TYPO-004",
            "high",
            1,
            f"`type.families.body` uses display typeface `{primary_font_name(body_fam)}` for body text.",
            "Use a readable text face for `type.families.body` and keep display serifs in `type.families.display`.",
            "typography.md › Ensuring legibility",
            file_path=tokens_json_file,
        )

    custom_fams = [primary_font_name(str(v)) for v in families.values() if isinstance(v, str) and is_custom_font_stack(v)]
    if custom_fams and not bundled_fonts:
        uniq = ", ".join(dict.fromkeys(custom_fams))
        add(
            "DESIGN-CLAUDE-002",
            "low",
            1,
            f"`type.fonts` is empty while referencing hosted/custom font families ({uniq}); fallback faces will render offline.",
            "Bundle font files/descriptors or verify fallback stacks match target platforms (`SF Pro` / `system-ui`).",
            "Claude Design › Font portability",
            file_path=tokens_json_file,
        )

    min_floor = 11.0 if (platforms & {"ios", "ipados"}) else (10.0 if "macos" in platforms else 11.0)
    for grp in type_sec.get("groups", []) if isinstance(type_sec.get("groups"), list) else []:
        if not isinstance(grp, dict):
            continue
        for st in grp.get("styles", []) if isinstance(grp.get("styles"), list) else []:
            if not isinstance(st, dict):
                continue
            sname = str(st.get("name", "style"))
            lno = find_json_line(sname)
            px = parse_px_value(st.get("fontSize"))
            fw = parse_font_weight(st.get("fontWeight"))
            lh = st.get("lineHeight")
            if px is not None:
                if px < min_floor:
                    add("DESIGN-TYPO-001", "critical", lno, f"Typography style `{sname}` size `{st.get('fontSize')}` is below minimum floor ({int(min_floor)}px/pt).", f"Raise `{sname}.fontSize` to at least `{int(min_floor)}px`.", "typography.md › Ensuring legibility", file_path=tokens_json_file)
                elif sname in ("text-base", "body") and (platforms & {"ios", "ipados"}) and px < 16.0:
                    add("DESIGN-TYPO-001", "medium", lno, f"Body style `{sname}` is `{st.get('fontSize')}`, smaller than Apple's 17 pt iOS/iPadOS body default.", "Map iOS/iPadOS body text to Dynamic Type `.body` (17 pt default).", "typography.md › Platform considerations", file_path=tokens_json_file)
                if fw is not None and fw <= 300 and px <= 16.0:
                    add("DESIGN-TYPO-002", "high", lno, f"Typography style `{sname}` uses thin weight ({fw}) at small size `{st.get('fontSize')}`.", "Use Regular (400) or Medium (500) at small sizes.", "typography.md › Ensuring legibility", file_path=tokens_json_file)
            if isinstance(lh, (int, float)) and sname in ("text-base", "body", "text-sm", "text-xs") and lh < 1.25:
                add("DESIGN-TYPO-003", "medium", lno, f"Body style `{sname}` lineHeight `{lh}` is tight for multi-line copy.", "Increase body lineHeight to at least `1.35`–`1.55`.", "typography.md › Ensuring legibility", file_path=tokens_json_file)

    # 3. Shadow / focus-ring check
    shadow_tokens = (tokens_data.get("shadow") or {}).get("tokens", []) if isinstance(tokens_data.get("shadow"), dict) else []
    shadow_names = {str(t.get("name", "")).lower() for t in shadow_tokens if isinstance(t, dict)}
    if shadow_tokens and not any("focus" in n for n in shadow_names):
        add("DESIGN-COMP-001", "medium", 1, "`tokens.json` `shadow.tokens` lacks a keyboard `focus-ring` token.", "Add a high-contrast `focus-ring` shadow/outline token for keyboard navigation.", "accessibility.md › Mobility", file_path=tokens_json_file)

    # 4. README.md checks: Motion table (since Claude tokens.json has no motion family), Reduced Motion, cross-file color drift
    if readme_doc is None:
        add("DESIGN-CLAUDE-001", "medium", 1, "Claude Design package missing `README.md` guidance.", "Include `README.md` with visual rationale, motion token table, and anti-patterns.", "Claude Design › Package structure", file_path=readme_file)
    else:
        motion_rows = extract_markdown_motion_table(readme_doc)
        for m_name, m_val, m_line in motion_rows:
            ms = parse_ms_value(m_val)
            if ms is not None and ms > 500:
                add(
                    "DESIGN-MOTION-002",
                    "medium",
                    m_line,
                    f"Motion token `{m_name}` is `{m_val}` (> 500ms) in `README.md`.",
                    "Clarify that durations > 500ms are strictly non-blocking/ambient and keep interactive transitions <= 350ms.",
                    "motion.md › Best practices",
                    file_path=readme_file,
                )
        if not re.search(r"reduced[- ]motion|prefers-reduced-motion|reduce motion", readme_doc["raw"], re.I):
            add(
                "DESIGN-MOTION-001",
                "high",
                _section_line(readme_doc, "motion"),
                "Claude Design `README.md` defines motion guidance without a Reduced Motion fallback.",
                "Add explicit `prefers-reduced-motion` / Reduce Motion fallback guidance under `## Motion`.",
                "motion.md › Best practices",
                file_path=readme_file,
            )

        # Cross-file color drift between README.md and tokens.json
        prose_colors = extract_prose_color_bullets(readme_doc)
        role_to_tok = {
            "primary": "accent",
            "surface": "surface",
            "text": "fg",
            "background": "bg",
            "success": "success",
            "warning": "warn",
            "danger": "danger",
            "error": "danger",
        }
        for role, (hex_val, lno) in prose_colors.items():
            tok_name = role_to_tok.get(role)
            if tok_name and tok_name in first_theme_colors:
                tok_hex = first_theme_colors[tok_name]
                if HEX_COLOR_RE.match(tok_hex) and tok_hex != hex_val:
                    add(
                        "DESIGN-CLAUDE-001",
                        "high",
                        lno,
                        f"Cross-file color drift: `README.md` `{role.title()}` is `{hex_val}`, but `tokens.json` `{tok_name}` is `{tok_hex}`.",
                        f"Sync `README.md` and `tokens.json` `{tok_name}` to the same hex value.",
                        "Claude Design › Token consistency",
                        file_path=readme_file,
                    )

        if apple_targeted:
            if not re.search(r"dynamic type|text scaling|system font|sf pro", readme_doc["raw"], re.I):
                add("DESIGN-TYPO-003", "high", _section_line(readme_doc, "typography"), "Apple platforms targeted, but `README.md` omits Dynamic Type / text-scaling guidance.", "Add Dynamic Type scaling rules (200% enlargement) and native font mappings.", "typography.md › Supporting Dynamic Type", file_path=readme_file)
            if not re.search(r"44\s*(?:pt|px|×|x)|safe area|touch target|hit target", readme_doc["raw"], re.I):
                add("DESIGN-LAYOUT-001", "medium", _section_line(readme_doc, "layout"), "Apple platforms targeted, but `README.md` omits hit-target (44×44 pt / 28×28 pt) and safe-area rules.", "Document minimum touch/hit targets and safe areas.", "accessibility.md › Mobility", file_path=readme_file)

    findings.sort(key=lambda f: (-SEVERITY_RANK.get(f["severity"], 0), f["file"], f["line"], f["id"]))
    return {
        "file": str(base_dir),
        "profile": "claude",
        "platforms": sorted(platforms),
        "tokens_summary": {
            "name": ds_name,
            "version": tokens_data.get("version"),
            "themes": theme_ids,
            "color_token_count": len(color_tokens),
            "has_design_system_json": ds_json_file.exists(),
            "has_tokens_json": tokens_json_file.exists(),
            "has_readme_md": readme_doc is not None,
        },
        "contrast": contrast_rows,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Google Stitch DESIGN.md + Extended + Capture Package Auditor
# ---------------------------------------------------------------------------

def extract_omitted_set(tokens: dict[str, Any]) -> set[str]:
    omitted_raw = tokens.get("omitted")
    out: set[str] = set()
    if isinstance(omitted_raw, list):
        for item in omitted_raw:
            if isinstance(item, str):
                out.add(item.strip().lower())
            elif isinstance(item, dict) and "section" in item:
                out.add(str(item["section"]).strip().lower())
    return out


def audit_google_design_md(
    ctx: dict[str, Any],
    *,
    platform_arg: str,
    extensions_override: Path | None = None,
) -> dict[str, Any]:
    design_path: Path = ctx["design_md"]
    base_dir: Path = ctx["base_dir"]
    detected_profile: str = ctx["profile"]

    doc = parse_markdown_doc(design_path)
    tokens: dict[str, Any] = doc["tokens"]
    tlines: dict[str, int] = doc["token_lines"]
    findings: list[dict[str, Any]] = []
    contrast_rows: list[dict[str, Any]] = []

    def add(rule_id: str, severity: str, line: int, message: str, fix: str, citation: str, file_path: Path = design_path) -> None:
        findings.append(
            {
                "id": rule_id,
                "severity": severity,
                "file": str(file_path),
                "line": max(1, int(line)),
                "message": message,
                "fix": fix,
                "citation": citation,
            }
        )

    ext_file = extensions_override or ctx["extensions_yaml"]
    ext_data: dict[str, Any] | None = None
    ext_lines: dict[str, int] = {}
    if ext_file.exists():
        ext_raw = ext_file.read_text(encoding="utf-8", errors="replace")
        ext_data, ext_lines, ext_dups, ext_errs = parse_yaml_with_lines(ext_raw, start_line=1)
        for dpath, first_l, dup_l in ext_dups:
            add("DESIGN-SCHEMA-004", "high", dup_l, f"Duplicate key `{dpath}` in {ext_file.name} (first defined at line {first_l}).", f"Remove or merge the duplicate `{dpath}` entry.", "DESIGN.md spec › Extensions", file_path=ext_file)
        for elno, emsg in ext_errs:
            add("DESIGN-PKG-002", "high", elno, f"YAML syntax error in {ext_file.name}: {emsg}", "Fix YAML syntax.", "DESIGN.md spec › Extensions", file_path=ext_file)

    overview_text = _section_text(doc, "overview") or doc["raw"][:2500]
    platforms = detect_platforms_generic(overview_text, platform_arg, ext_data=ext_data)
    apple_targeted = bool(platforms & {"ios", "ipados", "macos"})
    omitted = extract_omitted_set(tokens)

    # 1. YAML parse errors, duplicate keys, top-level schema
    for lno, msg in doc["yaml_errors"]:
        add("DESIGN-SCHEMA-003", "critical", lno, f"YAML parse error: {msg}", "Fix the YAML syntax in frontmatter or embedded YAML block.", "DESIGN.md spec › Token schema")
    for dpath, first_l, dup_l in doc["duplicates"]:
        add("DESIGN-SCHEMA-004", "high", dup_l, f"Duplicate token key `{dpath}` (first defined at line {first_l}).", f"Consolidate `{dpath}` into a single canonical definition across frontmatter and YAML blocks.", "DESIGN.md spec › Token schema")

    if doc["yaml_block_count"] == 0:
        add("DESIGN-SCHEMA-001", "high", 1, "No machine-readable YAML frontmatter or embedded token block found.", "Add YAML frontmatter (`---`) defining `version`, `name`, `description`, `colors`, `typography`, `spacing`, `rounded`, `motion`, and `components`.", "DESIGN.md spec › Token schema")
    else:
        if not tokens.get("name"):
            add("DESIGN-SCHEMA-001", "high", 1, "Missing required top-level `name` field in YAML tokens.", "Add `name: <Design System Name>` to the YAML frontmatter.", "DESIGN.md spec › Official Google token schema")
        if not tokens.get("version"):
            add("DESIGN-SCHEMA-001", "low", 1, "Missing top-level `version` field (Google spec uses `version: alpha`).", "Add `version: alpha` to frontmatter for upstream compatibility.", "DESIGN.md spec › Official Google token schema")
        for top_key in tokens:
            if top_key not in GOOGLE_TOP_LEVEL_FIELDS:
                sev = "high" if detected_profile == "google" else "medium"
                add("DESIGN-SCHEMA-002", sev, tlines.get(top_key, 1), f"Non-standard top-level YAML field `{top_key}` outside Google's canonical schema.", f"Move `{top_key}` into a sibling `design.extensions.yaml` file and reference it from Markdown to preserve Google `DESIGN.md` compatibility.", "DESIGN.md spec › Extended schema")

    # 2. Token reference resolution & value validation
    resolved_flat: dict[str, Any] = {}
    for leaf_path, raw_val in iter_leaf_tokens(tokens):
        lno = tlines.get(leaf_path, 1)
        top = leaf_path.split(".", 1)[0]
        if isinstance(raw_val, str) and TOKEN_REF_RE.match(raw_val.strip()):
            resolved, err_kind, chain = resolve_token_value(tokens, raw_val)
            if err_kind == "missing":
                add("DESIGN-ALIAS-001", "critical", lno, f"Unresolved token reference `{raw_val}` at `{leaf_path}` (missing `{chain[-1]}`).", f"Define `{chain[-1]}` or update `{leaf_path}` to point to an existing token path.", "DESIGN.md spec › Token references")
                continue
            if err_kind == "cycle":
                add("DESIGN-ALIAS-002", "critical", lno, f"Circular token reference at `{leaf_path}`: {' -> '.join(chain)}.", "Break the alias cycle so the token resolves to a concrete primitive value.", "DESIGN.md spec › Token references")
                continue
        else:
            resolved = raw_val
        resolved_flat[leaf_path] = resolved

        if top == "colors" and isinstance(resolved, str) and not is_valid_color_syntax(resolved):
            add("DESIGN-SCHEMA-003", "high", lno, f"Invalid color token value `{resolved}` at `{leaf_path}`.", "Use a valid `#RRGGBB`, `#RRGGBBAA`, `rgb(...)`, `hsl(...)`, or `oklch(...)` color value.", "DESIGN.md spec › colors")
        elif top == "rounded" and isinstance(resolved, str) and not DIMENSION_RE.match(resolved):
            add("DESIGN-SCHEMA-003", "medium", lno, f"Invalid corner radius dimension `{resolved}` at `{leaf_path}`.", "Use a dimension with units such as `0px`, `8px`, `12px`, or `9999px`.", "DESIGN.md spec › rounded")
        elif top == "spacing" and isinstance(resolved, str) and not DIMENSION_RE.match(resolved):
            add("DESIGN-SCHEMA-003", "medium", lno, f"Invalid spacing value `{resolved}` at `{leaf_path}`.", "Use a dimension (`4px`, `1rem`) or numeric count (e.g. `columns: 12`).", "DESIGN.md spec › spacing")
        elif top == "motion" and leaf_path.endswith((".duration", ".delay")) and (not isinstance(resolved, str) or not DURATION_RE.match(resolved)):
            add("DESIGN-SCHEMA-003", "medium", lno, f"Invalid motion duration `{resolved}` at `{leaf_path}`.", "Use millisecond or second units such as `150ms` or `0.2s`.", "DESIGN.md spec › motion")

    # 3. Color contrast (WCAG 2.2 + Apple HIG) & Dark Mode checks
    colors_tree = tokens.get("colors")
    if isinstance(colors_tree, dict):
        has_light = isinstance(colors_tree.get("light"), dict)
        has_dark = isinstance(colors_tree.get("dark"), dict)
        if has_light and not has_dark and "dark" not in omitted and "dark-mode" not in omitted:
            add("DESIGN-COLOR-001", "high", tlines.get("colors", 1), "Defines `colors.light` tokens without a corresponding `colors.dark` theme.", "Add semantic `colors.dark` tokens so the design supports Dark Mode.", "dark-mode.md › Best practices")

        def check_pair(label: str, fg_raw: Any, bg_raw: Any, lno: int, min_ratio: float = 4.5, context_note: str = "normal text", severity_fail: str = "critical") -> None:
            fg_res, fg_err, _ = resolve_token_value(tokens, fg_raw)
            bg_res, bg_err, _ = resolve_token_value(tokens, bg_raw)
            if fg_err or bg_err or not isinstance(fg_res, str) or not isinstance(bg_res, str):
                return
            fg_rgba = parse_color_rgba(fg_res)
            bg_rgba = parse_color_rgba(bg_res)
            if fg_rgba is None or bg_rgba is None:
                return
            ratio = contrast_ratio(fg_rgba, bg_rgba)
            passed = ratio >= min_ratio
            contrast_rows.append({"pair": label, "fg": fg_res, "bg": bg_res, "ratio": round(ratio, 2), "required": min_ratio, "pass": passed, "line": lno})
            if not passed:
                citation = "dark-mode.md › Dark Mode colors" if "dark." in label else "accessibility.md › Vision"
                add("DESIGN-A11Y-001", severity_fail, lno, f"Low color contrast for `{label}` (`{fg_res}` on `{bg_res}`): {ratio:.2f}:1 < {min_ratio:.1f}:1 ({context_note}).", f"Adjust foreground or background luminance so `{label}` reaches at least {min_ratio:.1f}:1 (aim for 7:1 on small text in Dark Mode).", citation)

        for mode in ("light", "dark"):
            theme = colors_tree.get(mode)
            if not isinstance(theme, dict):
                continue
            bg = theme.get("background")
            surf = theme.get("surface")
            fg = theme.get("foreground") or theme.get("text")
            muted = theme.get("muted") or theme.get("secondary") or theme.get("text-secondary")
            prim = theme.get("primary")
            on_prim = theme.get("on-primary") or theme.get("onPrimary")
            if fg and bg:
                check_pair(f"{mode}.foreground / {mode}.background", fg, bg, tlines.get(f"colors.{mode}.foreground", tlines.get(f"colors.{mode}", 1)))
            if fg and surf and surf != bg:
                check_pair(f"{mode}.foreground / {mode}.surface", fg, surf, tlines.get(f"colors.{mode}.foreground", tlines.get(f"colors.{mode}", 1)))
            if muted and bg:
                check_pair(f"{mode}.muted / {mode}.background", muted, bg, tlines.get(f"colors.{mode}.muted", tlines.get(f"colors.{mode}", 1)))
            if muted and surf and surf != bg:
                check_pair(f"{mode}.muted / {mode}.surface", muted, surf, tlines.get(f"colors.{mode}.muted", tlines.get(f"colors.{mode}", 1)))
            if on_prim and prim:
                check_pair(f"{mode}.on-primary / {mode}.primary", on_prim, prim, tlines.get(f"colors.{mode}.on-primary", tlines.get(f"colors.{mode}.primary", 1)))

            theme_status = theme.get("status") if isinstance(theme.get("status"), dict) else None
            shared_status = colors_tree.get("status") if isinstance(colors_tree.get("status"), dict) else None
            status_map = theme_status or shared_status
            status_prefix = f"colors.{mode}.status" if theme_status else "colors.status"
            if isinstance(status_map, dict):
                for s_name, s_val in status_map.items():
                    if isinstance(s_val, str):
                        s_line = tlines.get(f"{status_prefix}.{s_name}", tlines.get(status_prefix, 1))
                        pair_prefix = f"{mode}.status.{s_name}" if theme_status else f"status.{s_name}"
                        if bg:
                            check_pair(f"{pair_prefix} / {mode}.background", s_val, bg, s_line, 4.5, f"status text in {mode} mode", "high" if mode == "dark" else "critical")
                        if surf and surf != bg:
                            check_pair(f"{pair_prefix} / {mode}.surface", s_val, surf, s_line, 4.5, f"status text on {mode} surface", "high" if mode == "dark" else "critical")

        if not has_light and not has_dark:
            bg = colors_tree.get("background") or colors_tree.get("canvas")
            surf = colors_tree.get("surface")
            fg = colors_tree.get("foreground") or colors_tree.get("text")
            muted = colors_tree.get("muted") or colors_tree.get("text-secondary")
            prim = colors_tree.get("primary")
            on_prim = colors_tree.get("on-primary") or colors_tree.get("onPrimary")
            if fg and bg:
                check_pair("colors.foreground / colors.background", fg, bg, tlines.get("colors.foreground", tlines.get("colors", 1)))
            if fg and surf and surf != bg:
                check_pair("colors.foreground / colors.surface", fg, surf, tlines.get("colors.foreground", tlines.get("colors", 1)))
            if muted and bg:
                check_pair("colors.muted / colors.background", muted, bg, tlines.get("colors.muted", tlines.get("colors", 1)))
            if on_prim and prim:
                check_pair("colors.on-primary / colors.primary", on_prim, prim, tlines.get("colors.on-primary", tlines.get("colors.primary", 1)))

        components_tree = tokens.get("components")
        if isinstance(components_tree, dict):
            for comp_name, comp_cfg in components_tree.items():
                if not isinstance(comp_cfg, dict):
                    continue
                comp_line = tlines.get(f"components.{comp_name}", 1)
                if has_dark:
                    hardcoded_light = [(p, v) for p, v in comp_cfg.items() if isinstance(v, str) and "{colors.light." in v]
                    if hardcoded_light:
                        props_str = ", ".join(f"`{p}: {v}`" for p, v in hardcoded_light[:2])
                        add("DESIGN-COLOR-002", "high", tlines.get(f"components.{comp_name}.{hardcoded_light[0][0]}", comp_line), f"Shared component `{comp_name}` hardcodes light-mode token(s) ({props_str}) while `colors.dark` is defined.", "Bind shared components to theme-adaptive semantic roles (or define light/dark component bindings) so dark mode does not render light-mode colors.", "color.md › Best practices")
                bg_c = comp_cfg.get("backgroundColor") or comp_cfg.get("background")
                fg_c = comp_cfg.get("textColor") or comp_cfg.get("color") or comp_cfg.get("foreground")
                if bg_c and fg_c:
                    min_r = 4.5
                    ctx_note = "component text"
                    typo_ref = comp_cfg.get("typography")
                    if typo_ref:
                        t_res, t_err, _ = resolve_token_value(tokens, typo_ref)
                        if not t_err and isinstance(t_res, dict):
                            fs = parse_px_value(t_res.get("fontSize"))
                            fw = parse_font_weight(t_res.get("fontWeight")) or 400
                            if fs is not None and (fs >= 24.0 or (fs >= 18.67 and fw >= 700)):
                                min_r = 3.0
                                ctx_note = "large component text"
                    check_pair(f"components.{comp_name} (text/bg)", fg_c, bg_c, tlines.get(f"components.{comp_name}.textColor", comp_line), min_r, ctx_note)

        colors_sec_text = _section_text(doc, "colors")
        if colors_sec_text and not re.search(r"color alone|icon|label|shape|pattern|text cue", colors_sec_text, re.I):
            add("DESIGN-COLOR-003", "medium", _section_line(doc, "colors"), "`Colors` section does not state that status/meaning must never rely on color alone.", "Add an explicit accessibility rule pairing status colors with icons, text labels, or shape differences.", "color.md › Inclusive color")

    # 4. Typography & Apple HIG legibility / Dynamic Type checks
    typo_tree = tokens.get("typography")
    if isinstance(typo_tree, dict):
        min_floor = 11.0 if (platforms & {"ios", "ipados"}) else (10.0 if "macos" in platforms else 11.0)
        for style_name, style_cfg in typo_tree.items():
            if not isinstance(style_cfg, dict):
                continue
            s_line = tlines.get(f"typography.{style_name}", 1)
            fs_raw = style_cfg.get("fontSize")
            fs_res, fs_err, _ = resolve_token_value(tokens, fs_raw)
            fs_px = parse_px_value(fs_res) if not fs_err else None
            fw = parse_font_weight(style_cfg.get("fontWeight"))
            lh_raw = style_cfg.get("lineHeight")
            fam_raw = str(style_cfg.get("fontFamily", ""))

            if style_name.lower() in ("body", "paragraph", "label", "caption") and fam_raw and is_display_only_font(fam_raw):
                add("DESIGN-TYPO-004", "high", tlines.get(f"typography.{style_name}.fontFamily", s_line), f"Typography token `{style_name}` uses display typeface `{primary_font_name(fam_raw)}` for body/UI text.", "Reserve display serifs for large headings and use a readable text typeface for body/UI copy.", "typography.md › Ensuring legibility")

            if fs_px is not None:
                if fs_px < min_floor:
                    add("DESIGN-TYPO-001", "critical", tlines.get(f"typography.{style_name}.fontSize", s_line), f"Typography token `{style_name}` size `{fs_res}` is below the minimum legible floor ({int(min_floor)} pt/px).", f"Raise `{style_name}.fontSize` to at least `{int(min_floor)}px`/`pt` (Apple HIG floor: 11 pt on iOS/iPadOS, 10 pt on macOS).", "typography.md › Ensuring legibility")
                elif style_name.lower() == "body" and (platforms & {"ios", "ipados"}) and fs_px < 16.0:
                    add("DESIGN-TYPO-001", "medium", tlines.get(f"typography.{style_name}.fontSize", s_line), f"`typography.body.fontSize` is `{fs_res}`, smaller than Apple's 17 pt iOS/iPadOS default body size.", "Document platform-native scaling (17 pt Body on iOS/iPadOS, 13 pt on macOS, 14–16 px on Web) or adopt Dynamic Type text styles in iOS mappings.", "typography.md › Platform considerations")
                if fw is not None and fw <= 300 and fs_px <= 16.0:
                    add("DESIGN-TYPO-002", "high", tlines.get(f"typography.{style_name}.fontWeight", s_line), f"Typography token `{style_name}` uses thin/light weight ({fw}) at small size (`{fs_res}`).", "Use Regular (400), Medium (500), or Semibold (600) weights at small sizes to preserve legibility.", "typography.md › Ensuring legibility")

            if isinstance(lh_raw, (int, float)) and style_name.lower() in ("body", "paragraph", "caption") and lh_raw < 1.25:
                add("DESIGN-TYPO-003", "medium", tlines.get(f"typography.{style_name}.lineHeight", s_line), f"Tight line-height `{lh_raw}` on `{style_name}` can crowd multi-line text.", "Use at least `1.35`–`1.5` line-height for body/caption styles.", "typography.md › Ensuring legibility")

    typo_sec_text = _section_text(doc, "typography")
    if apple_targeted and typo_sec_text and not re.search(r"dynamic type|text scaling|system font|sf pro|larger text|font scaling", typo_sec_text, re.I):
        add("DESIGN-TYPO-003", "high", _section_line(doc, "typography"), "Apple platforms are targeted, but `Typography` does not address Dynamic Type / platform-native text scaling.", "Specify how text styles map to Dynamic Type (`Body`, `Headline`, `Caption`) and scale to at least 200% without clipping.", "typography.md › Supporting Dynamic Type")

    # 5. Motion, Reduced Motion, Components & Layout / Touch Targets
    motion_tree = tokens.get("motion")
    motion_sec_text = _section_text(doc, "motion")
    if isinstance(motion_tree, dict):
        for m_name, m_cfg in motion_tree.items():
            if not isinstance(m_cfg, dict):
                continue
            dur_raw = m_cfg.get("duration")
            ms = parse_ms_value(dur_raw)
            if ms is not None and ms > 500:
                add("DESIGN-MOTION-002", "medium", tlines.get(f"motion.{m_name}.duration", tlines.get("motion", 1)), f"Motion token `{m_name}.duration` is `{dur_raw}` (> 500ms), risking sluggish or blocking UI transitions.", "Keep standard UI micro-interactions brief (100–350ms) and interruptible.", "motion.md › Best practices")

    ext_reduced_motion = bool(ext_data and isinstance(ext_data.get("accessibility"), dict) and ext_data["accessibility"].get("reducedMotion") is True)
    if (motion_tree or motion_sec_text) and not ext_reduced_motion:
        if not re.search(r"reduced[- ]motion|prefers-reduced-motion|reduce motion", doc["raw"], re.I):
            add("DESIGN-MOTION-001", "high", _section_line(doc, "motion") if motion_sec_text else tlines.get("motion", 1), "Motion tokens/guidance defined without a Reduced Motion fallback.", "Add a `### Reduced Motion` subsection (or `accessibility.reducedMotion: true` in `design.extensions.yaml`) specifying cross-fade/instant fallbacks.", "motion.md › Best practices")

    comp_sec_text = _section_text(doc, "components")
    if comp_sec_text:
        missing_states = [st for st in ("hover", "focus", "disabled") if not re.search(rf"\b{st}\b", comp_sec_text, re.I)]
        if missing_states:
            add("DESIGN-COMP-001", "medium", _section_line(doc, "components"), f"`Components` section omits key interaction states: {', '.join(missing_states)}.", "Document Default, Hover, Pressed, Focus-visible, Selected, Disabled, Loading, and Error states for interactive controls.", "accessibility.md › Mobility")

    layout_sec_text = _section_text(doc, "layout")
    if apple_targeted and not re.search(r"44\s*(?:pt|px|×|x)|safe area|touch target|hit target|size class", doc["raw"], re.I):
        add("DESIGN-LAYOUT-001", "medium", _section_line(doc, "layout") if layout_sec_text else 1, "Apple platforms are listed, but document never specifies minimum touch/hit targets (44×44 pt iOS, 28×28 pt macOS) or safe areas.", "Document minimum interactive hit targets (44×44 pt mobile / 28×28 pt desktop), safe-area insets, and size-class adaptation.", "accessibility.md › Mobility")

    # 6. Canonical Section Order, Omissions, Links, Placeholders
    seen_canonical = [(SECTION_INDEX[s["id"]], s["id"], s["line"], s["heading"]) for s in doc["sections"] if s["id"] is not None]
    max_order = -1
    prev_heading = ""
    for order_idx, sid, lno, heading in seen_canonical:
        if order_idx < max_order:
            add("DESIGN-STRUCT-001", "medium", lno, f"Section `## {heading}` is out of Google's canonical section order (appeared after `## {prev_heading}`).", "Reorder H2 sections to match canonical order: Overview → Colors → Typography → Layout → Elevation & Depth → Shapes → Motion & Micro-interaction → Voice & Content Strategy → Components → Do's and Don'ts.", "DESIGN.md spec › Canonical Markdown section order")
        else:
            max_order = order_idx
            prev_heading = heading

    present_sids = {sid for _, sid, _, _ in seen_canonical}
    missing_core = [SECTION_LABEL[sid] for sid in ("overview", "colors", "typography", "layout", "components") if sid not in present_sids and sid not in omitted and SECTION_LABEL[sid].lower() not in omitted]
    if missing_core:
        add("DESIGN-STRUCT-002", "medium", 1, f"Missing core DESIGN.md section(s) not listed in `omitted`: {', '.join(missing_core)}.", "Add the missing H2 section(s) or explicitly record intentional exclusions in the top-level `omitted` array.", "DESIGN.md spec › Canonical Markdown section order")

    for lno, target in doc["external_refs"]:
        clean_target = target.split("#", 1)[0].split("?", 1)[0]
        if clean_target and not (base_dir / clean_target).resolve().exists():
            add("DESIGN-STRUCT-003", "high", lno, f"Broken external file reference `{target}` (not found at `{clean_target}`).", "Create the referenced supporting file or fix the relative link path.", "DESIGN.md spec › External references")

    placeholder_hits = [(idx, line.strip()[:80]) for idx, line in enumerate(doc["lines"], start=1) if any(p.search(line) for p in PLACEHOLDER_PATTERNS)]
    if placeholder_hits:
        add("DESIGN-STRUCT-004", "low", placeholder_hits[0][0], f"Uncustomized template placeholder text detected ({len(placeholder_hits)} occurrence(s), e.g. `{placeholder_hits[0][1]}`).", "Replace generic template instructions with concrete, product-specific design decisions.", "Reviewer judgment (Studio Craft)")

    # 7. Extensions & Designer-MCP Capture Package checks
    if ext_data is not None:
        if not ext_data.get("schemaVersion"):
            add("DESIGN-PKG-002", "low", 1, f"`{ext_file.name}` missing `schemaVersion` (e.g. `design-extensions/v1`).", "Add `schemaVersion: design-extensions/v1` at the top of `design.extensions.yaml`.", "DESIGN.md spec › Extensions", file_path=ext_file)
        a11y_ext = ext_data.get("accessibility")
        if isinstance(a11y_ext, dict):
            tc = a11y_ext.get("textContrast")
            if isinstance(tc, (int, float)) and tc < 4.5:
                add("DESIGN-A11Y-001", "critical", ext_lines.get("accessibility.textContrast", 1), f"`accessibility.textContrast` is set to `{tc}` (< 4.5 WCAG AA / Apple HIG minimum).", "Raise `accessibility.textContrast` to at least `4.5`.", "accessibility.md › Vision", file_path=ext_file)
            lc = a11y_ext.get("largeTextContrast")
            if isinstance(lc, (int, float)) and lc < 3.0:
                add("DESIGN-A11Y-001", "critical", ext_lines.get("accessibility.largeTextContrast", 1), f"`accessibility.largeTextContrast` is set to `{lc}` (< 3.0 WCAG AA / Apple HIG minimum).", "Raise `accessibility.largeTextContrast` to at least `3.0`.", "accessibility.md › Vision", file_path=ext_file)

    if detected_profile == "capture":
        theme_file: Path = ctx["theme_json"]
        evidence_file: Path = ctx["evidence_json"]
        if theme_file.exists():
            try:
                th = json.loads(theme_file.read_text(encoding="utf-8"))
                for row in th.get("tokens", []) if isinstance(th.get("tokens"), list) else []:
                    if not isinstance(row, dict) or not row.get("path"):
                        continue
                    tpath = str(row["path"])
                    tval = str(row.get("value", "")).strip()
                    if tpath in resolved_flat:
                        md_val = str(resolved_flat[tpath]).strip()
                        if md_val.lower() != tval.lower():
                            add("DESIGN-CAPTURE-001", "high", tlines.get(tpath, 1), f"Token drift between `DESIGN.md` (`{tpath}: {md_val}`) and `theme.json` (`{tval}`).", "Sync `theme.json` from `DESIGN.md` and re-run `node scripts/build-design-system.mjs .`.", "Designer-MCP › Reproducible package", file_path=theme_file)
            except json.JSONDecodeError as exc:
                add("DESIGN-CAPTURE-001", "high", 1, f"Invalid JSON in `theme.json`: {exc}.", "Fix JSON syntax in `theme.json`.", "Designer-MCP › Reproducible package", file_path=theme_file)

        if evidence_file.exists():
            try:
                ev = json.loads(evidence_file.read_text(encoding="utf-8"))
                gaps = ev.get("gaps") if isinstance(ev.get("gaps"), list) else []
                if gaps:
                    add("DESIGN-CAPTURE-002", "low", 1, f"`DESIGN.evidence.json` records {len(gaps)} unresolved capture gap(s).", "Review `gaps` in `DESIGN.evidence.json` and fill or document intentional omissions.", "Designer-MCP › Provenance", file_path=evidence_file)
                origins = ev.get("tokenOrigins") if isinstance(ev.get("tokenOrigins"), dict) else {}
                token_paths = [p for p in resolved_flat if p.split(".", 1)[0] in ("colors", "typography", "spacing", "rounded")]
                if origins and token_paths:
                    missing_orig = [p for p in token_paths if p not in origins and ".".join(p.split(".")[:2]) not in origins]
                    if missing_orig:
                        add("DESIGN-CAPTURE-002", "low", 1, f"{len(missing_orig)} token(s) in `DESIGN.md` lack provenance entries in `DESIGN.evidence.json` `tokenOrigins` (e.g. `{missing_orig[0]}`).", "Record each committed token's source (`observation`, `theme`, or `override`) in `tokenOrigins`.", "Designer-MCP › Provenance", file_path=evidence_file)
            except json.JSONDecodeError as exc:
                add("DESIGN-CAPTURE-002", "high", 1, f"Invalid JSON in `DESIGN.evidence.json`: {exc}.", "Fix JSON syntax in `DESIGN.evidence.json`.", "Designer-MCP › Provenance", file_path=evidence_file)

    # 8. Craft / Anti-Template Heuristics
    if isinstance(typo_tree, dict) and isinstance(colors_tree, dict):
        families = {str(v.get("fontFamily", "")).strip().lower() for v in typo_tree.values() if isinstance(v, dict) and v.get("fontFamily")}
        primary_val, _, _ = resolve_token_value(tokens, colors_tree.get("primary"))
        if families and all("inter" in f or "mono" in f for f in families) and isinstance(primary_val, str) and primary_val.strip().lower() in ("#2563eb", "#3b82f6") and re.search(r"application design system|platform-neutral", str(tokens.get("name", "")) + " " + str(tokens.get("description", "")), re.I):
            add("DESIGN-CRAFT-001", "low", tlines.get("colors.primary", 1), "Generic default identity detected (`Inter` + default Tailwind blue `#2563EB` + generic title).", "Ground palette, display voice, and one signature brand element in the actual product subject (keep system controls native).", "Reviewer judgment (Studio Craft › branding.md)")

    findings.sort(key=lambda f: (-SEVERITY_RANK.get(f["severity"], 0), f["file"], f["line"], f["id"]))
    return {
        "file": str(design_path),
        "profile": detected_profile,
        "platforms": sorted(platforms),
        "tokens_summary": {
            "name": tokens.get("name"),
            "version": tokens.get("version"),
            "yaml_blocks": doc["yaml_block_count"],
            "top_level_keys": list(tokens.keys()),
            "sections_found": [s["heading"] for s in doc["sections"]],
            "has_extensions": ext_file.exists(),
        },
        "contrast": contrast_rows,
        "findings": findings,
    }


def _section_text(doc: dict[str, Any], sid: str) -> str:
    for sec in doc["sections"]:
        if sec.get("id") == sid or sec.get("od_id") == sid:
            return "\n".join(txt for _, txt in sec["body_lines"])
    return ""


def _section_line(doc: dict[str, Any], sid: str) -> int:
    for sec in doc["sections"]:
        if sec.get("id") == sid or sec.get("od_id") == sid:
            return int(sec["line"])
    return 1


# ---------------------------------------------------------------------------
# Optional upstream oracle hook (`@google/design.md lint`)
# ---------------------------------------------------------------------------

def run_google_cli_check(design_path: Path) -> dict[str, Any] | None:
    npx = shutil.which("npx")
    if not npx:
        return {"available": False, "note": "`npx` not found on PATH."}
    try:
        proc = subprocess.run([npx, "--no-install", "@google/design.md", "lint", str(design_path)], capture_output=True, text=True, timeout=15)
        return {"available": True, "returncode": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    except Exception as exc:
        return {"available": False, "note": str(exc)}


# ---------------------------------------------------------------------------
# Output formatters & CLI
# ---------------------------------------------------------------------------

def render_compact(report: dict[str, Any], *, max_findings: int | None, show_contrast: bool, google_result: dict[str, Any] | None) -> str:
    all_findings = report["findings"]
    shown = all_findings if max_findings is None else all_findings[:max_findings]
    suppressed = max(0, len(all_findings) - len(shown))
    sev_counts = {s: 0 for s in ("critical", "high", "medium", "low")}
    for f in all_findings:
        sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1

    header = (
        f"DESIGN-MD AUDIT | {report['file']} | profile: {report['profile']} | "
        f"platforms: {', '.join(report['platforms'])} | "
        f"findings: {len(all_findings)} "
        f"(critical={sev_counts['critical']}, high={sev_counts['high']}, "
        f"medium={sev_counts['medium']}, low={sev_counts['low']})"
    )
    out = [header]
    if not shown:
        out.append("No deterministic design-system issues found.")
    else:
        for f in shown:
            out.append(f"[{f['id']}][{f['severity'].upper()}] {f['file']}:{f['line']} — {f['message']}")
            out.append(f"  Fix: {f['fix']}")
            out.append(f"  Ref: {f['citation']}")
        if suppressed:
            out.append(f"... +{suppressed} more finding(s) suppressed (pass --all or raise --max-findings).")

    if show_contrast and report["contrast"]:
        out.append("")
        out.append("CONTRAST MATRIX (WCAG 2.2 relative luminance):")
        for row in report["contrast"]:
            status = "PASS" if row["pass"] else "FAIL"
            out.append(f"  [{status}] {row['pair']}: {row['fg']} on {row['bg']} -> {row['ratio']:.2f}:1 (req >= {row['required']:.1f}:1)")

    if google_result is not None:
        out.append("")
        if google_result.get("available"):
            out.append(f"UPSTREAM @google/design.md lint (exit {google_result['returncode']}):")
            if google_result.get("stdout"):
                out.append(google_result["stdout"])
            if google_result.get("stderr"):
                out.append(google_result["stderr"])
        else:
            out.append(f"UPSTREAM @google/design.md lint: not installed locally ({google_result.get('note', 'unavailable')}).")
    return "\n".join(out)


def render_markdown(report: dict[str, Any], *, max_findings: int | None, show_contrast: bool) -> str:
    all_findings = report["findings"]
    shown = all_findings if max_findings is None else all_findings[:max_findings]
    lines = [
        f"## Design system deterministic audit: {report['tokens_summary'].get('name') or Path(report['file']).name}",
        "",
        f"- **Target:** `{report['file']}`",
        f"- **Profile:** `{report['profile']}` | **Platforms:** {', '.join(report['platforms'])}",
        f"- **Total findings:** {len(all_findings)}",
        "",
    ]
    if shown:
        lines.extend(["### Findings", ""])
        for f in shown:
            lines.append(f"- **[{f['id']}][{f['severity'].upper()}]** `{f['file']}:{f['line']}` — {f['message']}  \n  **Fix:** {f['fix']}  \n  **Ref:** `{f['citation']}`")
    else:
        lines.append("No deterministic issues found.")

    if show_contrast and report["contrast"]:
        lines.extend(["", "### Computed contrast ratios", "", "| Pair | Foreground | Background | Ratio | Required | Status |", "| --- | --- | --- | --- | --- | --- |"])
        for r in report["contrast"]:
            lines.append(f"| `{r['pair']}` | `{r['fg']}` | `{r['bg']}` | {r['ratio']:.2f}:1 | {r['required']:.1f}:1 | {'PASS' if r['pass'] else 'FAIL'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic auditor for Google DESIGN.md, Open Design packages, Claude Design packages, and Designer-MCP capture packages."
    )
    parser.add_argument("target", type=Path, help="Path to DESIGN.md, tokens.json/design-system.json, or a design-system directory.")
    parser.add_argument(
        "--profile",
        choices=["auto", "google", "core", "extended", "opendesign", "package", "claude", "capture"],
        default="auto",
        help="Format/compatibility profile (default: auto-detect from files in target directory).",
    )
    parser.add_argument("--platform", default="auto", help="Target platforms: auto, all, or comma-separated (ios,ipados,macos,web,android).")
    parser.add_argument("--extensions", type=Path, default=None, help="Explicit path to design.extensions.yaml.")
    parser.add_argument("--max-findings", type=int, default=8, help="Max findings in compact/markdown output (default: 8).")
    parser.add_argument("--all", action="store_true", help="Show all findings without capping.")
    parser.add_argument("--show-contrast", action="store_true", help="Print computed WCAG 2.2 contrast ratio table.")
    parser.add_argument("--check-google-cli", action="store_true", help="Also run locally installed `@google/design.md lint` (Google profiles only; never downloads).")
    parser.add_argument("--format", choices=["compact", "markdown", "json"], default="compact")
    parser.add_argument("--fail-on", choices=["none", "critical", "high", "medium", "low"], default="none", help="Exit 1 if any finding meets or exceeds this severity.")
    args = parser.parse_args(argv)

    if not args.target.exists():
        print(f"Path not found: {args.target}", file=sys.stderr)
        return 1

    ctx = detect_package_context(args.target, args.profile)
    prof = ctx["profile"]

    if prof == "opendesign":
        report = audit_opendesign_package(ctx, platform_arg=args.platform)
    elif prof == "claude":
        report = audit_claude_package(ctx, platform_arg=args.platform)
    else:
        if ctx["design_md"] is None:
            print(f"No DESIGN.md found at {args.target}", file=sys.stderr)
            return 1
        report = audit_google_design_md(ctx, platform_arg=args.platform, extensions_override=args.extensions)

    google_res = run_google_cli_check(ctx["design_md"]) if (args.check_google_cli and ctx["design_md"] and prof in ("core", "google", "extended", "capture")) else None

    cap = None if (args.all or args.format == "json") else args.max_findings
    if args.format == "json":
        payload = dict(report)
        if google_res is not None:
            payload["google_cli"] = google_res
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(render_markdown(report, max_findings=cap, show_contrast=args.show_contrast))
    else:
        print(render_compact(report, max_findings=cap, show_contrast=args.show_contrast, google_result=google_res))

    if args.fail_on != "none":
        threshold = SEVERITY_RANK[args.fail_on]
        if any(SEVERITY_RANK.get(f["severity"], 0) >= threshold for f in report["findings"]):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
