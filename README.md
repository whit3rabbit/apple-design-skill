# Apple Design Skill

An AI design-review skill grounded in Apple's Human Interface Guidelines. 123 HIG pages pulled
straight from developer.apple.com, a compiled knowledge graph, and 1,412 quote-verified rules
sit behind every finding.

It audits screenshots and UI across SwiftUI, UIKit, AppKit, Flutter, React Native, Tauri, and
Electron. Swift projects also get fast Tier-1/2/3 linting that cites exact rules, so an agent
never needs 30k+ tokens of raw Markdown to say something specific.

## Install

```bash
npx skills add whit3rabbit/apple-design-skill
```

This works for Claude Code, Codex, Cursor, Antigravity, and any other agent the
[skills CLI](https://skills.sh) supports. Useful variations:

```bash
npx skills add whit3rabbit/apple-design-skill -g     # user-wide instead of this project
npx skills add whit3rabbit/apple-design-skill --list # show what would be installed
npx skills update apple-design                       # pull the latest version later
```

### Examples

Prompt your agent:

```text
"Review my Flutter app's home screen against Apple's guidelines"
"Build a macOS Settings view in SwiftUI with a destructive reset button"
"Check my staged SwiftUI changes for Apple HIG accessibility or Liquid Glass issues"
"Audit this repo for anything App Review would reject in our Apple Pay checkout"
"Audit our DESIGN.md for contrast failures, schema issues, and Apple HIG gaps"
"Parse our Xcode performAccessibilityAudit .xcresult bundle and show the top fixes"
"This looks generic. Give it a point of view without breaking iOS patterns"
```

Or run the Swift three-tier tools directly (Python 3 stdlib only, zero dependencies):

```bash
python3 scripts/hig_lint.py path/to/Views/ --max-findings 5
python3 scripts/hig_lint.py --diff --repo path/to/MyApp --baseline .hig-baseline.json
python3 scripts/hig_context.py path/to/SettingsView.swift --scan --budget 850
python3 scripts/hig_context.py path/to/home_screen.dart --scan --budget 850
python3 scripts/hig_context.py --query "destructive button sheet" --severity critical,high
python3 scripts/hig_context.py --slug accessibility,layout,color --no-fixes --budget 900
python3 scripts/hig_runtime_audit.py --xcresult /tmp/HIGAudit.xcresult --max-findings 5
python3 scripts/design_md_audit.py path/to/DESIGN.md --show-contrast --all
```

## How It Works

- **Scrape & sync** (`scripts/pull-hig.mjs`): a zero-dependency Node 18+ crawler that fetches Apple's official HIG JSON across all six sections and keeps iOS/iPadOS/macOS guidance verbatim in `references/hig/*.md` (123 pulled pages plus 1 curated `liquid-glass.md`). It relabels platform headings by device class, rewrites cross-links as local links, prunes retired pages safely, and regenerates `references/hig-lookup.md`.
- **Knowledge graph** (`scripts/build_graph.py` → `rules/graph.json`): compiles all 124 pages into a link graph with 503 typed edges, reverse backlinks, hub centrality scores, and 908 indexed SwiftUI/UIKit/AppKit APIs. `--obsidian .cache/obsidian-vault` exports it as an Obsidian vault.
- **LLM rule compilation** (`scripts/extract_rules.py` → `rules/hig-rules.json`): runs an offline LLM pass over each page (OpenRouter, Anthropic, OpenAI/Ollama, or Gemini; default Claude Haiku 4.5) and emits three-tier rules (`static`, `semantic`, `visual`) with SwiftUI/UIKit fixes. Caching is per page, keyed by source SHA-256 in `.cache/rule-extract/`, so a refresh only recompiles the pages Apple changed. Every quote is verified verbatim against `references/hig/*.md`; unverified rules are dropped.
- **Sonnet QC pass** (`scripts/qc_rules.py`): pre-audits compiled rules for invented, deprecated, or overlong SwiftUI/UIKit fixes, then repairs flagged ones through a batched Claude Sonnet 4.5 pass. Cached in `.cache/rule-qc/`. It never alters a verified HIG quote.
- **Tier-1 linting** (`scripts/hig_lint.py` + `scripts/hig_swift_ast.py`): 24 deterministic checks across `.swift` and `Info.plist` files. Dynamic Type, semantic colors, icon-only VoiceOver labels, Liquid Glass layer misuse, iPhone Duo resizing, Apple Pay guideline 4.9, `TabView` overflow, multi-detent grabbers. Uses the `tree-sitter-swift` AST when installed and falls back to zero-dependency parsing otherwise. Flags cover `--diff`/`--staged`, `.hig-baseline.json` ratcheting, `// hig-ignore: <ID>` suppressions, Xcode warning format, CI `--fail-on`, and SwiftLint export.
- **Tier-2 UI scanner & JSON search** (`scripts/hig_context.py`): scans `.swift`, `.dart` (Flutter), `.tsx`/`.jsx` (React Native), or `.vue`/`.html`/`.css` (Tauri/Electron/Web) files (or `git diff`, `--query`, `--slug`, `--api`), maps detected components via `rules/graph.json` and the cross-platform equivalency table, and emits a `<900`-token rule card (`--scan` groups best practices under each component + line number, auto-omitting Swift snippets on non-Swift files).
- **Tier-3 runtime audit bridge** (`scripts/hig_runtime_audit.py`): parses Xcode `performAccessibilityAudit()` results into compact `[HIG-RT-*]` diagnostics. Reads `.xcresult` bundles (via `xcrun xcresulttool`) or plain `xcodebuild` logs. Covers rendered contrast, hit regions, text clipping, and Dynamic Type ground truth. `--print-xctest` prints a drop-in XCTest template.
- **Design-system auditor** (`scripts/design_md_audit.py` + `references/design-md-audit.md`): auto-detects and audits Google Stitch `DESIGN.md` (`design.extensions.yaml`), Open Design packages (`manifest.json`, `tokens.css`, `design-tokens.json`, `DESIGN.md`), Claude Design packages (`design-system.json`, `tokens.json`, `README.md`), and Designer-MCP capture packages (`theme.json`, `DESIGN.evidence.json`). Computes real WCAG 2.2 contrast ratios, cross-file token drift, alias resolution, Apple HIG floors (Dynamic Type, Dark Mode parity, hit targets, Reduced Motion), and studio craft.
- **Visual review & craft** (`SKILL.md` + `references/`): the skill itself. Apple's 8 design principles, 5 audit lenses, cross-platform vocabulary, code-audit patterns, and archived design heritage. 10 trigger/behavior evals in `evals/evals.json` guard the behavior.

## Refreshing & Maintaining

```bash
node scripts/pull-hig.mjs --cache .cache                         # pull latest HIG from Apple
python3 scripts/extract_rules.py --provider openrouter --concurrency 8  # recompile changed pages only
python3 scripts/qc_rules.py --provider openrouter --mode flagged # optional Sonnet QC on flagged fixes
python3 scripts/build_graph.py                                   # rebuild graph.json & preserve LLM rules
```

## Origin & license

The guideline text belongs to Apple Inc. and is reproduced from the public
[Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/)
for AI-assisted design review, with a source link at the top of every file.

This project is not affiliated with or endorsed by Apple. The skill, the curated guide, and the
scripts are provided as they are; use them at your own discretion.
