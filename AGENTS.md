# AGENTS.md

Operating manual for AI agents working in this repository, or using it from another project.

## Contents

- [What this repository is](#what-this-repository-is)
- [Running a design review](#running-a-design-review)
- [File map](#file-map)
- [Refreshing the references](#refreshing-the-references)
- [Refreshing the graph and rule registry](#refreshing-the-graph-and-rule-registry)
- [Checks before finishing any change](#checks-before-finishing-any-change)
- [Conventions](#conventions)

## What this repository is

Apple Design Skill: a design-review skill (`SKILL.md`) backed by 123 pages of Apple's Human
Interface Guidelines pulled from developer.apple.com into `references/hig/`, one curated guide,
a generated lookup table, a compiled HIG knowledge graph and quote-verified rule registry in
`rules/`, and the scripts that regenerate and query all of it.

## Running a design review

1. Read `SKILL.md` in full. It defines the reviewer's stance, Apple's eight design principles,
   the five audit lenses, the report format, and the improvement process.
2. Query the compiled 3-tier tools first instead of loading `rules/hig-rules.json` directly (1.5 MB)
   or 30k+ tokens of raw Markdown:
   ```bash
   python3 scripts/hig_lint.py <path-to-swift-file-or-dir> --max-findings 5
   python3 scripts/hig_context.py <path-to-swift-file-or-dir> --budget 850 --check-static
   python3 scripts/hig_context.py --diff --repo <swift-repo> --check-static --max-findings 5
   python3 scripts/hig_context.py --query "destructive button sheet" --severity critical,high
   python3 scripts/hig_context.py --slug accessibility,layout,color --no-fixes --budget 900
   python3 scripts/hig_runtime_audit.py --xcresult <bundle> --max-findings 5
   ```
   Open raw `references/hig/*.md` files only when edge-case nuance or spec tables are needed.
3. For visual/screenshot reviews or cross-platform audits, pick files from `SKILL.md`: the
   always-load set, the "Load by what is on screen" table, and the index of every page by
   Apple's section. `references/hig-lookup.md` adds Apple's one-line summary of each page and
   the list of pages left out; open it to check what a page covers when its name is ambiguous.
4. Load the always-load set (`accessibility.md`, `layout.md`, `typography.md`, `color.md`, plus
   `designing-for-ios.md`, `designing-for-macos.md`, or both, plus `references/cross-platform.md` unless
   the app is SwiftUI, UIKit, or AppKit) and 3 to 6 more files for what is on screen. Never load
   the whole directory.
5. Quote the guideline and cite it as `file.md › Heading`. Use the user's framework vocabulary
   (Flutter, React Native, Tauri, Electron, SwiftUI, UIKit, AppKit).
6. Write the report in the `SKILL.md` format: Summary, Critical, Improvements, Craft notes,
   What works, Platform notes. Each finding has a What, a Why with a citation, and a Fix.

## File map

| Path | Role | Edit by hand? |
| --- | --- | --- |
| `SKILL.md` | The skill: stance, principles, lenses, report format, improvement mode | Yes |
| `AGENTS.md` | This file | Yes |
| `README.md` | Installation and overview for people | Yes |
| `.cursorrules` | Entry point for Cursor | Yes |
| `GEMINI.md` | Entry point for Antigravity | Yes |
| `.github/FUNDING.yml` | GitHub Sponsors button configuration | Yes |
| `references/hig-lookup.md` | Generated table of every page with Apple's summary and change date, plus an omitted-pages list | No, re-run the script |
| `references/hig/*.md` | 123 generated pages in Apple's wording | No, re-run the script |
| `references/hig/liquid-glass.md` | Curated Liquid Glass guide; the script never touches it | Yes |
| `references/cross-platform.md` | Apple's vocabulary and conventions for Flutter, React Native, Tauri, and Electron | Yes |
| `references/code-audit.md` | Code patterns to search for (Apple Pay, Liquid Glass, iPhone Duo), with citations and replacements | Yes |
| `references/heritage.md` | Classic principles from Apple's archived 1980 to 2014 guidelines, summarized in our own words, mapped to today's | Yes |
| `evals/evals.json` | Evaluation scenarios covering review modes, trigger paraphrases, and scope limits | Yes |
| `rules/graph.json` | Generated HIG knowledge graph: 124 nodes, cross-file edges, backlinks, hub scores, and Swift/Apple API index | No, re-run `build_graph.py` |
| `rules/hig-rules.json` | Generated quote-verified 3-tier rule registry (`static`, `semantic`, `visual`) with SwiftUI/UIKit fixes | No, re-run `build_graph.py` or `extract_rules.py` |
| `scripts/pull-hig.mjs` | Pull script, Node 18 or newer, no dependencies | Yes |
| `scripts/hig_swift_catalog.py` | Shared Python 3 stdlib module: HIG parser, verbatim quote verifier, Swift + Flutter/RN/Web API → HIG maps, and curated Tier-1 static rules | Yes |
| `scripts/build_graph.py` | Builds `rules/graph.json` and baseline `rules/hig-rules.json` (preserving up-to-date LLM rules); optional `--obsidian` vault export | Yes |
| `scripts/extract_rules.py` | Offline LLM rule compiler (`openrouter`, `anthropic`, `openai`, `gemini`, `dry-run`) with SHA-256 caching in `.cache/rule-extract/` and quote verification | Yes |
| `scripts/qc_rules.py` | Batched Sonnet QC pass over LLM-compiled rules to fix deprecated/invented SwiftUI/UIKit APIs (cached in `.cache/rule-qc/`) | Yes |
| `scripts/hig_context.py` | Universal UI file scanner & JSON rule search (`.swift`, `.dart`, `.tsx`/`.jsx`, `.vue`/`.css`): supports `--scan` (`--group-by component`), `--diff`/`--staged`, `--query`, `--slug`, `--api`, `--severity`, `--no-fixes`, emits `<900`-token rule cards | Yes |
| `scripts/hig_lint.py` | Dedicated Tier-1 Swift & `Info.plist` linter with `.hig-baseline.json` ratcheting, `--diff`/`--staged`, `--engine auto\|ast\|regex`, Xcode warnings, CI `--fail-on`, and `--export-swiftlint` | Yes |
| `scripts/hig_swift_ast.py` | Optional `tree-sitter-swift` AST backend for `hig_lint.py` (gracefully falls back to zero-dep stdlib regex/brace engine when uninstalled) | Yes |
| `scripts/hig_runtime_audit.py` | Tier-3 Xcode `performAccessibilityAudit()` bridge: parses `.xcresult` bundles (`xcrun xcresulttool`) or `xcodebuild` logs into compact HIG-cited diagnostics, plus `--print-xctest` template | Yes |

## Refreshing the references

```bash
node scripts/pull-hig.mjs                 # pull from developer.apple.com into references/
node scripts/pull-hig.mjs --cache .cache  # also keep the raw JSON for fast re-runs
```

Options: `--out <dir>`, `--lookup <file>`, `--cache <dir>`, `--concurrency <n>`, `--no-prune`,
`--force-prune`.

What the script does:

- Crawls Apple's six HIG sections and their sub-collections (158 pages as of October 2026).
- Keeps every page that applies to iOS, iPadOS, or macOS, omits Apple-only hardware and services
  listed in `OMITTED_PAGES`, and records every omission with a reason in `hig-lookup.md`.
- Renders Apple's JSON to Markdown faithfully: headings, paragraphs, lists, tables, notes, and
  tabbed content as sub-headings. Images and videos are dropped except for check-mark glyphs in
  comparison tables.
- Relabels platform headings by device class (`Phone (iOS)`, `Tablet (iPadOS)`,
  `Mobile (iOS, iPadOS)`, `Desktop (macOS)`, and combinations), drops sections that apply only
  to tvOS, visionOS, or watchOS (sentences that mention them stay), and rewrites cross-references
  as local links whose anchors match the relabeled headings. Links to headings that were dropped
  or that Apple no longer publishes point at the file instead.
- Keeps each page's change log and turns Apple's "Related" links into a "Related guidelines"
  list.
- Prunes generated files for pages Apple removed. Curated files are never pruned, and a run
  that would prune more than a tenth of the files stops and asks for `--force-prune`.

After running it: review `git diff --stat`, spot-check a changed file, run the checks below,
refresh `liquid-glass.md` if a page it links to changed, and, if pages were added or removed,
update the page counts in `README.md` and this file, and add or remove the page in
the "Every page, by Apple's section" table in `SKILL.md`. A new "Designing for" page has
no platform metadata: if its `Platforms covered` line doesn't name a platform, add its slug to
`DESIGNING_FOR_COVERAGE` in the script.

## Refreshing the graph and rule registry

All Python scripts use Python 3 stdlib only (no third-party dependencies):

```bash
python3 scripts/build_graph.py                                     # rebuild graph.json + preserve valid LLM rules
python3 scripts/build_graph.py --obsidian .cache/obsidian-vault    # also export an Obsidian graph view (untracked)
python3 scripts/extract_rules.py --provider dry-run                # check SHA-256 cache & token estimate
python3 scripts/extract_rules.py --provider openrouter --concurrency 8   # re-compile only changed pages (Claude Haiku 4.5)
python3 scripts/qc_rules.py --provider openrouter --mode flagged         # optional Sonnet 4.5 QC pass on flagged Swift fixes
```

Supported `--provider` values for `extract_rules.py`: `dry-run`, `openrouter` (default `anthropic/claude-haiku-4.5`),
`anthropic` (default `claude-haiku-4-5`, honors `ANTHROPIC_BASE_URL`), `openai` (default `gpt-4.1-mini`,
honors `OPENAI_BASE_URL` / `--base-url` for Ollama), and `gemini` (default `gemini-2.5-flash`).
Per-page LLM outputs cache in `.cache/rule-extract/<slug>.json` keyed by source SHA-256 so only
pages modified by `pull-hig.mjs` re-invoke the LLM. Every rule quote is verified verbatim against
its source file in `references/hig/`; unverified rules are dropped automatically.

## Checks before finishing any change

```bash
node --check scripts/pull-hig.mjs
node scripts/pull-hig.mjs --cache .cache && git status --short   # second run must change nothing
python3 -m py_compile scripts/hig_swift_catalog.py scripts/build_graph.py scripts/extract_rules.py scripts/qc_rules.py scripts/hig_context.py scripts/hig_lint.py scripts/hig_swift_ast.py scripts/hig_runtime_audit.py
python3 -c "import json; r = json.load(open('rules/hig-rules.json'))['rules']; assert len(r) == len({x['id'] for x in r})"
grep -rl 'doc://' references/hig                                  # must print nothing
diff -q .cursorrules GEMINI.md                                    # must print nothing
npx skills add . --list                                           # must find the apple-design skill
ls references/hig | while read -r f; do sed -n '/^### Every page/,/^## /p' SKILL.md | grep -q "\`$f\`" || echo "not in the page table: $f"; done   # must print nothing
grep -o '`[a-z0-9-]*\.md`' SKILL.md | tr -d '`' | sort -u | while read -r f; do [ -f "references/hig/$f" ] || echo "no such page: $f"; done   # must print nothing
```

The last two keep `SKILL.md` and `references/hig/` in step in both directions: every page is
in the skill's "Every page, by Apple's section" table, and every page the skill names exists.

## Conventions

- Keep `SKILL.md` under about 450 lines; Anthropic's skill guidance caps the body at 500. It loads
  in full every time the skill triggers, and it names every guideline page so that no reference
  sits more than one level away from it.
- Apple's text in `references/hig/` is quoted, not paraphrased. Cross-platform translation lives
  in `references/cross-platform.md`, `SKILL.md`, and `liquid-glass.md`, and code patterns in
  `references/code-audit.md`, not in the pulled files.
- `references/heritage.md` is the opposite: it summarizes the archived editions in our own words
  and never reproduces them. Don't add the archived PDFs or long excerpts to the repository, and
  keep its "Superseded advice" table in step when a current page replaces an old rule.
- Don't add pages to `references/hig/` by hand. Change `OMITTED_PAGES` or the platform rule in
  the script instead.
- A hand-written file in `references/hig/` must be registered in `CURATED_FILES` and must not
  start with the generated header (a `> Source: <...>` line followed by `> Section:`). Open it
  with `> Curated guide` as `liquid-glass.md` does, so prune can never mistake it for output.
- Don't edit `rules/graph.json` or `rules/hig-rules.json` by hand. Update `scripts/hig_swift_catalog.py`
  and re-run `scripts/build_graph.py` (or `scripts/extract_rules.py`). Every rule quote must pass
  verbatim verification against `references/hig/`.
- Commit generated changes separately from hand-written changes.
- Do not commit `.cache/`, `.omc/`, or other local state (including `.cache/rule-extract/` and
  `.cache/obsidian-vault/`).
