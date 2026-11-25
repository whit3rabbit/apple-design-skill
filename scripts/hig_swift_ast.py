#!/usr/bin/env python3
"""
hig_swift_ast.py

Optional `tree-sitter-swift` AST backend for `scripts/hig_lint.py`.
Provides true Swift 6 AST precision for SwiftUI view-modifier chains, per-View
struct scoping, container nesting (List/ForEach/LazyVStack, .toolbar, .sheet,
TabView), and exact receiver checks — while falling back cleanly to the
zero-dependency regex engine in `hig_lint.py` when `tree-sitter-swift` is absent.

Install optional dependency in a venv if desired:
  uv pip install tree-sitter tree-sitter-swift
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import tree_sitter  # type: ignore
    import tree_sitter_swift  # type: ignore

    _SWIFT_LANG = tree_sitter.Language(tree_sitter_swift.language())
    HAS_TREE_SITTER = True
except Exception:
    tree_sitter = None  # type: ignore
    tree_sitter_swift = None  # type: ignore
    _SWIFT_LANG = None
    HAS_TREE_SITTER = False


def _node_text(node: Any, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _call_name(call_node: Any, src: bytes) -> tuple[str | None, Any | None]:
    """
    For a `call_expression`, return (callee_name, receiver_node_or_None).
    - `Button(...)` -> ("Button", None)
    - `view.glassEffect(.clear)` -> (".glassEffect", view_node)
    - `UIScreen.main` is not a call; `.system(size: 14)` -> (".system", None)
    """
    if not call_node.children:
        return None, None
    first = call_node.children[0]
    if first.type == "simple_identifier":
        return _node_text(first, src), None
    if first.type == "prefix_expression":
        txt = _node_text(first, src).strip()
        return (txt if txt.startswith(".") else None), None
    if first.type == "navigation_expression":
        named = [c for c in first.children if c.is_named]
        if len(named) >= 2 and named[-1].type == "navigation_suffix":
            suffix_id = next((c for c in named[-1].children if c.type == "simple_identifier"), None)
            if suffix_id is not None:
                return "." + _node_text(suffix_id, src), named[0]
    return None, None


def _call_suffix(call_node: Any) -> Any | None:
    return next((c for c in call_node.children if c.type == "call_suffix"), None)


def _call_lambdas(call_node: Any) -> list[Any]:
    suffix = _call_suffix(call_node)
    if suffix is None:
        return []
    return [c for c in suffix.children if c.type == "lambda_literal"]


def _call_args_text(call_node: Any, src: bytes) -> str:
    suffix = _call_suffix(call_node)
    if suffix is None:
        return ""
    va = next((c for c in suffix.children if c.type == "value_arguments"), None)
    return _node_text(va, src) if va is not None else ""


def _outer_modifiers_of(node: Any, src: bytes) -> list[tuple[str, Any]]:
    """
    Walk upward along a SwiftUI modifier chain attached to `node`.
    Returns [(modifier_name, modifier_call_node), ...].
    """
    mods: list[tuple[str, Any]] = []
    cur = node
    while cur.parent is not None:
        parent = cur.parent
        if parent.type == "navigation_expression" and parent.children and parent.children[0] == cur:
            gp = parent.parent
            if gp is not None and gp.type == "call_expression":
                mname, _ = _call_name(gp, src)
                if mname and mname.startswith("."):
                    mods.append((mname, gp))
                    cur = gp
                    continue
        break
    return mods


def _chain_root_callee(node: Any, src: bytes) -> str | None:
    """Walk down receivers of a modifier chain to find the root view/call name."""
    cur = node
    while cur is not None and cur.type == "call_expression":
        cname, recv = _call_name(cur, src)
        if recv is None:
            return cname
        cur = recv
    return None


def _walk_descendants(node: Any):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        for child in reversed(cur.children):
            if child.is_named:
                stack.append(child)


def _is_ignored(rid: str, lineno: int, ignores: dict[int, set[str]]) -> bool:
    s = ignores.get(lineno, set())
    return rid in s or "ALL" in s


def _make_finding(rule: dict[str, Any], path: Path, lineno: int, lines: list[str], message: str | None = None) -> dict[str, Any]:
    snippet = lines[lineno - 1].strip()[:120] if 1 <= lineno <= len(lines) else ""
    return {
        "id": rule["id"],
        "severity": rule["severity"],
        "file": str(path),
        "line": lineno,
        "snippet": snippet,
        "message": message or rule["rule"],
        "fix": rule.get("fix_swiftui") or rule.get("fix_uikit") or "",
        "citation": rule.get("citation") or f"{rule['slug']}.md › {rule['heading']}",
        "quote": rule.get("quote", ""),
    }


def _platform_ok(rule: dict[str, Any], platforms: list[str] | None) -> bool:
    r_plats = rule.get("platforms", [])
    return not platforms or not r_plats or any(p in r_plats for p in platforms)


def lint_swift_ast(
    raw_source: str,
    path: Path,
    rules_by_id: dict[str, dict[str, Any]],
    ignores: dict[int, set[str]],
    changed_lines: set[int] | None,
    platforms: list[str] | None,
) -> list[dict[str, Any]]:
    """
    Run AST-accurate Tier-1 Swift checks using tree-sitter-swift.
    Returns findings for all AST-handled rules (plus caller runs remaining pure lexical checks).
    """
    if not HAS_TREE_SITTER or _SWIFT_LANG is None:
        raise RuntimeError("tree-sitter-swift is not installed")

    src = raw_source.encode("utf-8")
    lines = raw_source.splitlines()
    parser = tree_sitter.Parser(_SWIFT_LANG)
    tree = parser.parse(src)
    root = tree.root_node

    findings: list[dict[str, Any]] = []
    has_reduce_motion = "accessibilityReduceMotion" in raw_source
    has_reduce_transparency = "accessibilityReduceTransparency" in raw_source

    def emit(rid: str, lineno: int, message: str | None = None) -> None:
        rule = rules_by_id.get(rid)
        if not rule or not _platform_ok(rule, platforms):
            return
        if changed_lines is not None and lineno not in changed_lines:
            return
        if _is_ignored(rid, lineno, ignores):
            return
        findings.append(_make_finding(rule, path, lineno, lines, message=message))

    # Track ancestor container lambdas during recursive DFS
    def mod_line_of(call_node: Any) -> int:
        first = call_node.children[0] if call_node.children else call_node
        if first.type == "navigation_expression":
            return first.end_point[0] + 1
        return call_node.start_point[0] + 1

    def visit(node: Any, containers: tuple[str, ...]) -> None:
        if node.type == "call_expression":
            cname, recv = _call_name(node, src)
            args_txt = _call_args_text(node, src)
            lineno = mod_line_of(node)
            outer_mods = _outer_modifiers_of(node, src)
            outer_mod_names = {m[0] for m in outer_mods}

            # 1. Fixed font size: .font(.system(size: ...)) or .font(.custom(..., fixedSize: ...))
            if cname == ".font":
                if re.search(r"(?:Font)?\.?(?:system\(\s*size\s*:|custom\([^)]*fixedSize\s*:)", args_txt):
                    emit("HIG-TYPO-001", lineno)
                if re.search(r"weight\s*:\s*\.(?:ultraLight|thin|light)\b", args_txt):
                    emit("HIG-TYPO-002", lineno)

            # 2. Light/thin font weight
            elif cname == ".fontWeight" and re.search(r"\.(?:ultraLight|thin|light)\b", args_txt):
                emit("HIG-TYPO-002", lineno)

            # 3. Forced light/dark mode
            elif cname == ".preferredColorScheme" and re.search(r"\.(?:light|dark)\b", args_txt):
                emit("HIG-COLOR-001", lineno)

            # 4. Hardcoded RGB/hex Color / UIColor / NSColor
            elif cname in ("Color", "UIColor", "NSColor") and re.search(r"\b(?:red|white|hex)\s*:", args_txt):
                emit("HIG-COLOR-002", lineno)

            # 5. Hardcoded .background(.white / .black / Color.white / Color.black)
            elif cname == ".background" and re.search(r"^\(\s*(?:Color\.|\.)(?:white|black)\s*\)$", args_txt.strip()):
                emit("HIG-COLOR-003", lineno)

            # 6. Button checks: icon-only a11y, destructive+primary, custom Apple Pay / SIWA
            elif cname == "Button":
                # Custom Apple Pay / Sign in with Apple buttons
                if re.search(r"\"[^\"]*Apple\s*Pay[^\"]*\"", args_txt):
                    emit("HIG-PAY-002", lineno)
                if re.search(r"\"[^\"]*Sign\s+in\s+with\s+Apple[^\"]*\"", args_txt):
                    emit("HIG-SIWA-001", lineno)

                # Destructive button combined with defaultAction or prominent style on its own chain
                if re.search(r"role\s*:\s*\.destructive\b", args_txt):
                    for mname, mnode in outer_mods:
                        margs = _call_args_text(mnode, src)
                        if (mname == ".keyboardShortcut" and ".defaultAction" in margs) or (
                            mname == ".buttonStyle" and re.search(r"\.(?:borderedProminent|glassProminent)\b", margs)
                        ):
                            emit("HIG-BTN-001", mod_line_of(mnode))
                            break

                # Icon-only Button without Label or .accessibilityLabel on its chain or label closure
                lambdas = _call_lambdas(node)
                if lambdas and ".accessibilityLabel" not in outer_mod_names:
                    label_lambda = lambdas[-1]
                    label_txt = _node_text(label_lambda, src)
                    if (
                        re.search(r"\bImage\s*\(\s*systemName\s*:", label_txt)
                        and "Label(" not in label_txt
                        and "Text(" not in label_txt
                        and ".accessibilityLabel(" not in label_txt
                    ):
                        emit("HIG-A11Y-001", lineno)

            # 7. Icon-only ToolbarItem
            elif cname == "ToolbarItem":
                lambdas = _call_lambdas(node)
                if lambdas and ".accessibilityLabel" not in outer_mod_names:
                    body_txt = _node_text(lambdas[-1], src)
                    if (
                        re.search(r"\bImage\s*\(\s*systemName\s*:", body_txt)
                        and "Button" not in body_txt  # Button inside ToolbarItem is checked by rule 6
                        and "Label(" not in body_txt
                        and "Text(" not in body_txt
                        and ".accessibilityLabel(" not in body_txt
                    ):
                        emit("HIG-A11Y-001", lineno)

            # 8. Small frame (< 28 pt) on interactive controls ONLY (exact chain receiver check)
            #    and fixed phone viewport width (320-440 pt)
            elif cname == ".frame":
                if re.search(r"\bwidth\s*:\s*(?:3[2-9]\d|4[0-4]\d)(?:\.\d+)?\b", args_txt):
                    emit("HIG-DUO-003", lineno)
                if re.search(r"\b(?:width|height)\s*:\s*(?:1\d|2[0-7])(?:\.0)?\b", args_txt):
                    root_view = _chain_root_callee(node, src)
                    is_interactive = root_view in ("Button", "Toggle", "Link", "Stepper", "Picker") or (
                        ".onTapGesture" in outer_mod_names
                    )
                    if is_interactive:
                        emit("HIG-A11Y-002", lineno)

            # 9. Liquid Glass checks (.glassEffect)
            elif cname == ".glassEffect":
                mod_line = node.children[0].end_point[0] + 1 if node.children else lineno
                if re.search(r"\.clear\b", args_txt):
                    emit("HIG-LG-001", mod_line)
                if any(c in ("List", "ForEach", "LazyVStack") for c in containers):
                    emit("HIG-LG-005", mod_line)
                if not has_reduce_transparency and not _is_ignored("HIG-LG-004", mod_line, ignores):
                    if changed_lines is None or mod_line in changed_lines:
                        findings.append(
                            {
                                "id": "HIG-LG-004",
                                "severity": "high",
                                "file": str(path),
                                "line": mod_line,
                                "snippet": lines[mod_line - 1].strip()[:120] if 1 <= mod_line <= len(lines) else "",
                                "message": "Custom `.glassEffect` used without `@Environment(\\.accessibilityReduceTransparency)` fallback.",
                                "fix": "Read `@Environment(\\.accessibilityReduceTransparency)` and provide an opaque surface when enabled.",
                                "citation": "liquid-glass.md › Review checklist",
                                "quote": "Reduce Transparency gets an opaque fallback",
                            }
                        )

            # 10. Opaque .toolbarBackground
            elif cname == ".toolbarBackground" and re.search(r"(?:Color\.|\.visible\b)", args_txt):
                mod_line = node.children[0].end_point[0] + 1 if node.children else lineno
                emit("HIG-LG-002", mod_line)

            # 11. Repeating animations without accessibilityReduceMotion
            elif cname == ".repeatForever" and not has_reduce_motion:
                mod_line = node.children[0].end_point[0] + 1 if node.children else lineno
                emit("HIG-MOTION-001", mod_line)

            # 12. Spacer inside .toolbar or ToolbarItemGroup
            elif cname == "Spacer" and any(c in (".toolbar", "ToolbarItemGroup") for c in containers):
                emit("HIG-DUO-004", lineno)

            # 13. Alert triggered on .onAppear
            elif cname == ".onAppear":
                lambdas = _call_lambdas(node)
                if lambdas and re.search(r"(?:Alert|alert)\s*=\s*true", _node_text(lambdas[-1], src)):
                    mod_line = node.children[0].end_point[0] + 1 if node.children else lineno
                    emit("HIG-ALERT-001", mod_line)

            # 14. Generic "OK" / "Submit" buttons inside .alert
            elif cname == ".alert":
                lambdas = _call_lambdas(node)
                for lam in lambdas:
                    if re.search(r"\bButton\s*\(\s*\"(?:OK|Submit|Yes|No)\"", _node_text(lam, src)):
                        mod_line = node.children[0].end_point[0] + 1 if node.children else lineno
                        emit("HIG-ALERT-002", mod_line)
                        break

            # 15. TabView with > 5 tabs (unless .tabViewStyle(.sidebarAdaptable) is attached to THIS TabView)
            elif cname == "TabView":
                has_sidebar_adaptable = any(
                    mname == ".tabViewStyle" and ".sidebarAdaptable" in _call_args_text(mnode, src)
                    for mname, mnode in outer_mods
                )
                lambdas = _call_lambdas(node)
                if lambdas and not has_sidebar_adaptable:
                    tab_calls = 0
                    tab_items = 0
                    for d in _walk_descendants(lambdas[0]):
                        if d.type == "call_expression":
                            dn, _ = _call_name(d, src)
                            if dn == "Tab":
                                tab_calls += 1
                            elif dn == ".tabItem":
                                tab_items += 1
                    count = max(tab_calls, tab_items)
                    if count > 5:
                        emit(
                            "HIG-TAB-001",
                            lineno,
                            message=f"TabView defines {count} tabs (> 5 causes a hidden More overflow tab on compact widths).",
                        )

            # 16. Multi-detent .sheet hiding its drag indicator
            elif cname == ".sheet":
                lambdas = _call_lambdas(node)
                if lambdas:
                    has_multi_detent = False
                    hidden_line: int | None = None
                    for d in _walk_descendants(lambdas[-1]):
                        if d.type == "call_expression":
                            dn, _ = _call_name(d, src)
                            dargs = _call_args_text(d, src)
                            if dn == ".presentationDetents" and "," in dargs:
                                has_multi_detent = True
                            elif dn == ".presentationDragIndicator" and ".hidden" in dargs:
                                hidden_line = d.children[0].end_point[0] + 1 if d.children else d.start_point[0] + 1
                    if has_multi_detent and hidden_line is not None:
                        emit("HIG-SHEET-001", hidden_line)

            # Recurse into children: ONLY push container context inside this call's own lambda_literal(s),
            # NOT on chained outer receivers (so `List { ... }.toolbar { ... }` does NOT treat `.toolbar` as inside `List`!)
            next_containers = containers + (cname,) if cname in ("List", "ForEach", "LazyVStack", ".toolbar", "ToolbarItemGroup") else containers
            suffix = _call_suffix(node)
            for child in node.children:
                if not child.is_named:
                    continue
                if child == suffix and next_containers != containers:
                    visit(child, next_containers)
                else:
                    visit(child, containers)
            return

        # Per-View struct check: > 2 prominent buttons inside a single View declaration
        if node.type == "class_declaration":
            prominent_lines: list[int] = []
            for d in _walk_descendants(node):
                if d.type == "call_expression":
                    dn, _ = _call_name(d, src)
                    if dn == ".buttonStyle" and re.search(r"\.(?:borderedProminent|glassProminent)\b", _call_args_text(d, src)):
                        ml = d.children[0].end_point[0] + 1 if d.children else d.start_point[0] + 1
                        if changed_lines is None or ml in changed_lines:
                            prominent_lines.append(ml)
            if len(prominent_lines) > 2:
                ml = prominent_lines[2]
                if not _is_ignored("HIG-BTN-002", ml, ignores):
                    findings.append(
                        {
                            "id": "HIG-BTN-002",
                            "severity": "high",
                            "file": str(path),
                            "line": ml,
                            "snippet": lines[ml - 1].strip()[:120] if 1 <= ml <= len(lines) else "",
                            "message": f"Found {len(prominent_lines)} prominent buttons in one View (HIG recommends 1–2 max).",
                            "fix": "Keep prominent style on only 1–2 primary actions; use `.bordered` or `.glass` for secondary actions.",
                            "citation": "buttons.md › Style",
                            "quote": "Keep the number of prominent buttons to one or two per view.",
                        }
                    )

        for child in node.children:
            if child.is_named:
                visit(child, containers)

    visit(root, ())

    # Pure lexical expressions outside call_expression (UIScreen.main.bounds, userInterfaceIdiom, U+F8FF glyph)
    for lineno, text in enumerate(lines, start=1):
        if changed_lines is not None and lineno not in changed_lines:
            continue
        if re.search(r"\bUIScreen\.main\.bounds\b", text):
            emit("HIG-DUO-001", lineno)
        if re.search(r"\buserInterfaceIdiom\s*[!=]=\s*\.(?:phone|pad)\b", text):
            emit("HIG-DUO-002", lineno)
        if re.search(r"(?:\uf8ff|\\u\{?[fF]8[fF]{2}\}?|&#x[fF]8[fF]{2};)", text):
            emit("HIG-PAY-001", lineno)

    return findings
