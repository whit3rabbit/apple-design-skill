When reviewing, building, or auditing UI/UX designs, follow `SKILL.md`:

1. Read `SKILL.md` in full. Never read `rules/hig-rules.json` directly (1.5 MB) and never load the
   whole `references/hig/` directory.
2. Query the compiled 3-tier scripts first to save ~97% context tokens:
   - Tier-1 lint (Swift/`Info.plist`): `python3 scripts/hig_lint.py <path> --max-findings 5`
   - Tier-2 JIT rules (Swift, diff, or any UI topic): `python3 scripts/hig_context.py <path> --budget 850`
     (supports `--diff`/`--staged`, `--query "<keywords>"`, `--slug <a,b>`, `--api <Sym>`,
     `--severity critical,high`, `--no-fixes` for Flutter/RN/visual reviews)
   - Tier-3 runtime audit: `python3 scripts/hig_runtime_audit.py --xcresult <bundle>`
   - `DESIGN.md` / package audit: `python3 scripts/design_md_audit.py <path-or-dir> --show-contrast --all`
     (auto-detects Google `DESIGN.md`, Open Design, Claude Design, and capture packages)
   Open raw `references/hig/*.md` files only for spec tables, deep nuance, or full visual audits.
3. For Swift quick checks or PR lints, use the compact `[HIG-ID][SEVERITY] path:line` format
   (capped at 3–5 findings; skip Craft notes unless asked). For full visual reviews, use the
   `SKILL.md` report format (Summary, Critical, Improvements, Craft notes, What works, Platform notes).
4. Translate Apple terms to the user's framework (load `references/cross-platform.md` for Flutter,
   React Native, Tauri, or Electron). Cite every guideline as `file.md › Heading` with a verified
   quote, or label non-guideline points as judgment.
5. For repo-wide code audits or Apple Pay, follow `references/code-audit.md` and cite `path:line`.
   For `DESIGN.md`, Open Design, or Claude Design packages, follow `references/design-md-audit.md`.
   For retro Apple looks or historical rationale, load `references/heritage.md`.

Trigger on: UI/UX review, accessibility audit, layout, dark mode, Liquid Glass, iPhone Duo, app
icons, widgets/Live Activities, `DESIGN.md` audits/specs, building or linting SwiftUI/UIKit/AppKit
views, Apple Pay checkout, Xcode `performAccessibilityAudit()` results, retro Apple looks, or "make
this look less generic".
Do not trigger on backend, networking, persistence, CLI, or non-UI code reviews.
