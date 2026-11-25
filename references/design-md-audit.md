# DESIGN.md & Design System Package Audit

> Hand-written companion to `SKILL.md`. Load it when auditing, reviewing, validating, authoring, or
> checking code drift against a **Google Stitch `DESIGN.md`** (`design.extensions.yaml`), an
> **Open Design package** (`manifest.json`, `tokens.css`, `DESIGN.md`, `design-tokens.json`), a
> **Claude Design package** (`design-system.json`, `tokens.json`, `README.md`, `components/`), or
> a **Designer-MCP capture package** (`DESIGN.md`, `DESIGN.evidence.json`, `theme.json`, `styles.css`).

A flawed design system silently propagates contrast failures, broken token aliases, cross-file
drift, hardcoded light-mode values, or display-serif body copy into every screen an agent builds.

## Contents

- [Supported formats & compatibility profiles](#supported-formats--compatibility-profiles)
- [How to run an audit](#how-to-run-an-audit)
- [Format-specific checks](#format-specific-checks)
- [Core audit rubric](#core-audit-rubric)
- [Checking code-to-spec drift](#checking-code-to-spec-drift)
- [Report format](#report-format)

## Supported formats & compatibility profiles

`scripts/design_md_audit.py` auto-detects all four formats from the target directory (or accepts an
explicit `--profile`):

| Profile | Format | Primary files | Token source | Guidance source |
| --- | --- | --- | --- | --- |
| **`core` / `google`** | Google Stitch `DESIGN.md` (alpha) | `DESIGN.md` | YAML frontmatter (`---`) + embedded ` ```yaml ` blocks (`colors`, `typography`, `spacing`, `rounded`, `motion`, `components`) | 10 canonical H2 sections (`Overview` → `Do's and Don'ts`) |
| **`extended`** | Google Stitch + extensions | `DESIGN.md`, `design.extensions.yaml` | Google YAML + structured extensions (`themes`, `breakpoints`, `elevation`, `layers`, `icons`, `accessibility`, `density`, `platforms`) | 10 canonical H2 sections + H3 subsections |
| **`opendesign`** (alias: `package`) | Open Design (`od-design-system-project/v1`) | `manifest.json`, `tokens.css`, `DESIGN.md`, optional `design-tokens.json`, `tailwind-v4.css`, `USAGE.md`, `components.html` | `:root` custom properties in `tokens.css` (`--bg`, `--surface`, `--fg`, `--fg-2`, `--muted`, `--meta`, `--accent`, `--accent-on`, `--success`, `--warn`, `--danger`, `--font-*`, `--text-*`, `--motion-*`) + `design-tokens.json` (`od-design-tokens/v1`) | 9-section numbered prose `DESIGN.md` (`## 1. Visual Theme & Atmosphere` … `## 9. Anti-patterns`, no YAML frontmatter) |
| **`claude`** | Claude Design System (`v: 3`) | `design-system.json`, `tokens.json`, `README.md`, optional `components/*` | `tokens.json` (`color.themes`, `color.tokens[]`, `type.families`, `type.groups[].styles[]`, `spacing`, `radius`, `shadow`, `typeMetrics`) + `## Motion` markdown table in `README.md` (Claude `tokens.json` has no native motion family) | `README.md` (prose + `## Motion` table + `## Not synced from Open Design`) |
| **`capture`** | Designer-MCP reproducible package | `DESIGN.md`, `DESIGN.evidence.json`, `theme.json`, `styles.css`, `_ds_manifest.json` | Google YAML in `DESIGN.md` mirrored to `theme.json` (`tokens: [{path, value, reference}]`) | `DESIGN.md` + provenance (`tokenOrigins`, `gaps`) in `DESIGN.evidence.json` |

## How to run an audit

1. **Run the deterministic auditor first** (Python 3 stdlib only, real WCAG 2.2 luminance math):
   ```bash
   python3 "$SKILL_DIR/scripts/design_md_audit.py" <path-to-DESIGN.md-or-package-dir> --show-contrast --all
   ```
   Useful flags:
   - `--profile auto|google|core|extended|opendesign|claude|capture`
   - `--platform auto|ios,macos|web|all` (Open Design and Claude packages default to `web` unless Apple platforms are declared; pass `--platform ios,macos` when evaluating them for native Apple apps!)
   - `--format compact|markdown|json`, `--fail-on critical|high`
   - `--check-google-cli` (runs locally installed `@google/design.md lint` on Google profiles without downloading)
2. **Auditing catalog systems from `designer-mcp` (when installed):**
   - **Open Design catalog system:** Call `design_get(identifier: "designer:opendesign:<id>")`, then read `DESIGN.md`, `tokens.css`, `design-tokens.json`, and `manifest.json` via `design_package_file`.
   - **Claude Design catalog system:** Call `design_claude_package` for `design-system.json`, `tokens.json`, and `README.md`.
   - Write the fetched files into a temporary directory (or scratch dir) and run `scripts/design_md_audit.py` on that directory so contrast ratios and cross-file drift are computed deterministically—never guessed!
   - When verifying a rendered web/PDF/PPTX build against `DESIGN.md`, pair with `design_markdown_validate` (if available).
3. **Pull verified Apple HIG rules** for the target platforms:
   ```bash
   python3 "$SKILL_DIR/scripts/hig_context.py" --slug accessibility,color,dark-mode,typography,layout,motion,branding,writing --tier all --no-fixes --budget 900
   ```
   - **Apple platforms in scope (`iOS`, `iPadOS`, `macOS`):** Enforce full HIG platform rules (Dynamic Type scaling, 11 pt/10 pt floors, 17 pt iOS body default, 44×44 pt / 28×28 pt hit targets, safe areas, Light+Dark mode parity, Liquid Glass layer restraint).
   - **Web or Android-only design systems:** State the scope boundary upfront. Apply format schema validity, WCAG 2.2 contrast, Apple's universal principles, foundations (accessibility, color, typography, layout, motion, writing), and studio craft—not Apple-native platform chrome.
4. **Read the source files directly** to catch qualitative prose contradictions, vague anti-patterns, and generic template aesthetics that static rules cannot fully judge.

## Format-specific checks

### Google Stitch `DESIGN.md` (`core` / `extended`)
- Consolidate YAML frontmatter and embedded ` ```yaml ` blocks; flag duplicate keys (`DESIGN-SCHEMA-004`), unknown top-level keys (`DESIGN-SCHEMA-002` — move to `design.extensions.yaml`), unresolved/circular `{token.path}` aliases (`DESIGN-ALIAS-001/002`), out-of-order H2 sections (`DESIGN-STRUCT-001`), missing core sections not in `omitted`, and broken relative links.
- Catch shared `components.*` hardcoding `{colors.light.*}` while `colors.dark` exists (`DESIGN-COLOR-002`).
- Catch shared `colors.status.*` tokens that pass in Light Mode but fail contrast on `colors.dark.background`/`surface`.

### Open Design packages (`opendesign`)
- Validate `manifest.json` (`od-design-system-project/v1`) and verify every declared file exists (`DESIGN.md`, `tokens.css`, `design-tokens.json`, `tailwind-v4.css`, `USAGE.md`, `components.manifest.json`).
- Parse all `:root` blocks in `tokens.css` and flag conflicting later overrides (`DESIGN-OD-003` — e.g. `--font-body: Inter` silently overridden later by `Playfair Display`).
- Check cross-file drift between `DESIGN.md` `## 2. Color` bullets and `tokens.css` (`DESIGN-OD-001`), and between `tokens.css` and `design-tokens.json` (`DESIGN-OD-002`).
- Compute WCAG 2.2 contrast across all normalized slots: `--fg`, `--fg-2`, `--muted`, `--meta`, `--success`, `--warn`, `--danger` on `--bg` and `--surface`, plus `--accent-on` on `--accent`/`--accent-hover`/`--accent-active`.
- Check `--focus-ring`, `--font-body` display-serif misuse (`DESIGN-TYPO-004`), `--motion-*` durations, Reduced Motion guidance, and single-theme vs adaptive Light/Dark support.

### Claude Design packages (`claude`)
- Validate `design-system.json` (`v`, `title`, `namespace`), `tokens.json`, and `README.md`.
- Audit `tokens.json` `color.themes` (single vs multi-theme; supports both single `value` and per-theme `values`) and run the full WCAG 2.2 contrast matrix across semantic pairs (surfacing any `usage` note that documents an intentional brand contrast exception while still reporting the exact WCAG/HIG ratio).
- Audit `type.families` (display-only font in `body`, empty `type.fonts: []` with non-system hosted faces as a portability note `DESIGN-CLAUDE-002`) and `type.groups[].styles[]` (`fontSize`, `lineHeight`, `fontWeight`).
- Parse `README.md`'s `## Motion` markdown table (since Claude `tokens.json` has no motion token family) for `>500ms` durations and Reduced Motion fallbacks, check `shadow.tokens` for `focus-ring`, and check cross-file color drift between `README.md` and `tokens.json` (`DESIGN-CLAUDE-001`).

### Designer-MCP capture packages (`capture`)
- Run all Google Stitch `DESIGN.md` checks, plus:
- Compare every `{path, value}` row in `theme.json` against resolved `DESIGN.md` tokens (`DESIGN-CAPTURE-001`).
- Inspect `DESIGN.evidence.json` for unresolved `gaps[]` and committed tokens missing `tokenOrigins` provenance (`DESIGN-CAPTURE-002`).

## Core audit rubric

1. **Schema, aliases & cross-file sync (Critical/High):** Zero parse errors, dangling/cyclic aliases, or silent divergence between Markdown prose, CSS/JSON tokens, and compiled exports.
2. **Color contrast & Dark Mode semantics (Critical/High):** >= 4.5:1 normal text, >= 3:1 large text (>= 18 pt or bold) / essential control boundaries in every supported theme (`accessibility.md › Vision`, `dark-mode.md › Dark Mode colors`). Never rely on color alone (`color.md › Inclusive color`).
3. **Typography & Dynamic Type (Critical/High):** Respect 11 pt iOS/iPadOS & 10 pt macOS floors, avoid thin/light weights at small sizes, avoid high-contrast display serifs for body copy (`typography.md › Ensuring legibility`), and document Dynamic Type scaling when Apple platforms are in scope (`typography.md › Supporting Dynamic Type`).
4. **Layout, hit targets, motion & states (High/Medium):** 44×44 pt mobile / 28×28 pt desktop hit targets & safe areas on Apple platforms (`accessibility.md › Mobility`, `layout.md › Guides and safe areas`), `focus-ring` / focus-visible states, brief interruptible motion (`<= 500ms`), and Reduced Motion fallback (`motion.md › Best practices`). Apply `liquid-glass.md` if glass/blur is specified.
5. **Platform adaptation (Medium):** Keep shared brand identity separate from platform-native controls (SwiftUI `pt` vs Web `px` vs Android `dp`; load `references/cross-platform.md` for Flutter/RN/Tauri/Electron).
6. **Studio craft & brand POV (Judgment):** Reject uncustomized template placeholders and generic AI defaults (`Inter` + default blue `#2563EB`, warm cream + serif + terracotta, near-black + acid-green). Label all craft notes explicitly as **Reviewer judgment**.

## Checking code-to-spec drift

1. Audit the design system package first (`scripts/design_md_audit.py`) so you don't enforce broken tokens.
2. Scan the app's UI files with `scripts/hig_lint.py` / `scripts/hig_context.py --scan`.
3. Compare resolved color/type/spacing/radius/motion tokens against literal values in SwiftUI/CSS/Flutter/RN code and cite `path:line` drift beside HIG citations.

## Report format

- **Quick lint / CI mode:** Output compact `[DESIGN-ID][SEVERITY] path:line` diagnostics directly from `scripts/design_md_audit.py`.
- **Full design-system audit:**
  ```text
  ## Design system audit: <name>
  ### Summary         (2–3 sentences, rating: Excellent / Good / Needs work / Critical issues, detected format/profile & platforms)
  ### Contrast audit  (compact table of failing or borderline pairs from `design_md_audit.py --show-contrast`)
  ### Critical        (- **What** (`path:line`), **Why** (spec or `file.md › Heading` + quote), **Fix** with concrete YAML/JSON/CSS/Markdown)
  ### Improvements    (same format, tagged High / Medium / Low)
  ### Craft notes     (brand POV, template/font-pairing check, cross-file sync — labeled as reviewer judgment)
  ### What works      (specific strengths to keep)
  ### Platform notes  (Apple HIG vs Web/Android adaptations, SwiftUI/Open Design/Claude Design interoperability notes)
  ```
