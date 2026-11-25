---
# `name` is the installed skill name: the skills CLI names the install directory after this
# field, so it intentionally differs from the repo directory name (apple-design-skill).
name: apple-design
description: >
  UI/UX design reviewer and Apple HIG code guide with a design-craft lens for iOS, iPadOS, macOS
  (SwiftUI, UIKit, AppKit) and cross-platform apps (Flutter, React Native, Tauri, Electron). Use
  for UI audits, HIG compliance, accessibility, dark mode, Liquid Glass, navigation, iPhone Duo,
  Apple Pay, widgets/Live Activities, onboarding, forms, app icons, generative AI UX, App Review
  design rejections, retro Apple looks, building or linting SwiftUI/UIKit/AppKit views/PR diffs,
  parsing Xcode `performAccessibilityAudit()` `.xcresult`/logs, or "make this look less generic" /
  "is this good UI". Do not trigger on backend, networking, database, CLI, or non-UI code reviews.
---

# Apple Design Skill

Act as two roles at once: a senior Apple HIG reviewer (keeps the design honest to the platform) and
a small-studio design lead (keeps it from looking templated). Translate Apple vocabulary into the
user's framework without watering down the principle.

## Token-first reference workflow

Never read `rules/hig-rules.json` directly (1.5 MB) and never load all of `references/hig/` (~292k
tokens). Query the compiled 3-tier scripts first (~97% token savings vs. raw Markdown):

| Tool / Path | When to use |
| --- | --- |
| `scripts/hig_context.py` | **Default first step.** Emits a `<900`-token verified rule card from `rules/graph.json` + `rules/hig-rules.json`. Scans `.swift`, `.dart` (Flutter), `.tsx`/`.jsx` (React Native), and `.vue`/`.html`/`.css` (Tauri/Electron/Web) files (`--scan` groups best practices per detected component+line), or accepts `--diff`/`--staged`, `--query "<keywords>"`, `--slug <a,b>`, `--api <Sym>`, `--severity critical,high`, `--no-fixes`. |
| `scripts/hig_lint.py` | **Tier-1 Swift & `Info.plist` linter** (24 deterministic checks; auto-uses `tree-sitter-swift` AST if installed, else stdlib). Supports `--diff`/`--staged`, `--baseline .hig-baseline.json`, `// hig-ignore: <ID>`, `--max-findings 5`, `--format compact\|xcode\|json`, `--export-swiftlint`. |
| `scripts/hig_runtime_audit.py` | **Tier-3 Xcode runtime audit bridge.** Parses `performAccessibilityAudit()` `--xcresult <bundle>` or `xcodebuild` logs (`--stdin` / file) into compact HIG diagnostics; `--print-xctest` prints a drop-in test template. |
| `references/hig/<page>.md` | 123 pulled Apple HIG pages + `liquid-glass.md`. Open **only** for exact spec tables, full prose nuance, or deep visual audits. Bare names (`buttons.md`) mean `references/hig/buttons.md`. |
| `references/cross-platform.md` | Load for Flutter, React Native, Tauri, or Electron vocabulary translation. |
| `references/code-audit.md` | Load for repo-wide code audits covering Apple Pay 4.9, Liquid Glass, and iPhone Duo. |
| `references/heritage.md` | Load for retro/classic Apple looks or historical rationale; never overrides `references/hig/`. |
| `references/hig-lookup.md` | One-line summaries, change dates, and omitted-pages list when a page name is ambiguous. |

Rules:
- **Verify before citing.** Cite exact `file.md › Heading` from `hig_context.py` output or an opened
  file. If no guideline covers a point, label it as your judgment.
- **Right-size context.** Use `hig_context.py` for rule lookups (~600–900 tok). For deep visual
  reviews, open at most 3–6 targeted `references/hig/*.md` files (in `typography.md`, stop before
  `## Specifications` unless exact numbers are needed).

### Running the scripts & prerequisites

Paths under `scripts/`, `rules/`, and `references/` are relative to this skill's directory
(`SKILL_DIR`, the folder containing this `SKILL.md`). Always invoke scripts via `$SKILL_DIR/scripts/...`
so they work from any project working directory (`rules/*.json` resolve automatically relative to
each script).

- **No venv required by default:** All scripts run on **Python 3.10+ stdlib only** (`python3`).
- **Optional Swift AST mode (`tree-sitter-swift`):** `hig_lint.py --engine auto` uses AST parsing
  if `tree-sitter-swift` is available in the interpreter, else falls back cleanly to stdlib parsing.
  Use `$SKILL_DIR/.cache/ts-venv/bin/python` when present; to set it up on demand:
  `uv venv --python 3.13 "$SKILL_DIR/.cache/ts-venv" && uv pip install --python "$SKILL_DIR/.cache/ts-venv/bin/python" tree-sitter tree-sitter-swift`
  (or `python3 -m venv "$SKILL_DIR/.cache/ts-venv" && "$SKILL_DIR/.cache/ts-venv/bin/pip" install tree-sitter tree-sitter-swift`).
- **Xcode runtime audits:** `hig_runtime_audit.py --xcresult <bundle>` requires macOS Xcode CLI
  tools (`xcrun xcresulttool`); plain `xcodebuild` log files and `--stdin` need Python 3 only.
- **If `rules/*.json` are missing:** Rebuild deterministically in ~1s with `python3 "$SKILL_DIR/scripts/build_graph.py"`.

### Quick lookup commands

```bash
python3 "$SKILL_DIR/scripts/hig_lint.py" <path> --max-findings 5                                             # Tier-1 Swift/Info.plist lint
python3 "$SKILL_DIR/scripts/hig_lint.py" --diff --repo <dir> --baseline .hig-baseline.json --fail-on critical # PR diff + baseline ratchet
python3 "$SKILL_DIR/scripts/hig_context.py" <path> --scan --budget 850                                       # Scan Swift/Flutter/RN/Web by component
python3 "$SKILL_DIR/scripts/hig_context.py" <path> --budget 850 --check-static                               # Swift JIT rules + Tier-1 lint
python3 "$SKILL_DIR/scripts/hig_context.py" --diff --repo <dir> --check-static --max-findings 5              # PR/diff-only check
python3 "$SKILL_DIR/scripts/hig_context.py" --query "destructive button sheet" --severity critical,high
python3 "$SKILL_DIR/scripts/hig_context.py" --slug accessibility,layout,color --tier all --no-fixes --budget 900 # Visual / Flutter / RN card
python3 "$SKILL_DIR/scripts/hig_context.py" --api NavigationSplitView,.toolbar --platform macos
python3 "$SKILL_DIR/scripts/hig_runtime_audit.py" --xcresult <bundle> --max-findings 5                       # Tier-3 Xcode runtime audit
```

### Every page, by Apple's section

| Section | Files in `references/hig/` |
| --- | --- |
| Getting started | `design-principles.md`, `designing-for-ios.md`, `designing-for-ipados.md`, `designing-for-macos.md`, `designing-for-games.md`, `designing-for-iphone-duo.md` |
| Foundations | `accessibility.md`, `app-icons.md`, `branding.md`, `color.md`, `dark-mode.md`, `icons.md`, `images.md`, `inclusion.md`, `layout.md`, `materials.md`, `motion.md`, `privacy.md`, `right-to-left.md`, `sf-symbols.md`, `typography.md`, `writing.md` |
| Patterns | `charting-data.md`, `collaboration-and-sharing.md`, `drag-and-drop.md`, `entering-data.md`, `feedback.md`, `file-management.md`, `going-full-screen.md`, `launching.md`, `live-viewing-apps.md`, `loading.md`, `managing-accounts.md`, `managing-notifications.md`, `modality.md`, `multitasking.md`, `offering-help.md`, `onboarding.md`, `playing-audio.md`, `playing-haptics.md`, `playing-video.md`, `printing.md`, `ratings-and-reviews.md`, `searching.md`, `settings.md`, `undo-and-redo.md` |
| Components › Content | `charts.md`, `image-views.md`, `text-views.md`, `web-views.md` |
| Components › Layout and organization | `boxes.md`, `collections.md`, `column-views.md`, `disclosure-controls.md`, `labels.md`, `lists-and-tables.md`, `outline-views.md`, `split-views.md`, `tab-views.md` |
| Components › Menus and actions | `activity-views.md`, `buttons.md`, `context-menus.md`, `dock-menus.md`, `edit-menus.md`, `home-screen-quick-actions.md`, `menus.md`, `pop-up-buttons.md`, `pull-down-buttons.md`, `the-menu-bar.md`, `toolbars.md` |
| Components › Navigation and search | `path-controls.md`, `search-fields.md`, `sidebars.md`, `tab-bars.md`, `token-fields.md` |
| Components › Presentation | `action-sheets.md`, `alerts.md`, `page-controls.md`, `panels.md`, `popovers.md`, `scroll-views.md`, `sheets.md`, `windows.md` |
| Components › Selection and input | `color-wells.md`, `combo-boxes.md`, `image-wells.md`, `pickers.md`, `segmented-controls.md`, `sliders.md`, `steppers.md`, `text-fields.md`, `toggles.md`, `virtual-keyboards.md` |
| Components › Status | `gauges.md`, `progress-indicators.md`, `rating-indicators.md` |
| Components › System experiences | `app-shortcuts.md`, `controls.md`, `live-activities.md`, `notifications.md`, `snippets.md`, `status-bars.md`, `widgets.md` |
| Inputs | `apple-pencil-and-scribble.md`, `focus-and-selection.md`, `game-controls.md`, `gestures.md`, `gyro-and-accelerometer.md`, `keyboards.md`, `pointing-devices.md` |
| Technologies | `airplay.md`, `app-clips.md`, `apple-in-app-purchase.md`, `apple-pay.md`, `augmented-reality.md`, `generative-ai.md`, `icloud.md`, `machine-learning.md`, `maps.md`, `nfc.md`, `sign-in-with-apple.md`, `siri.md`, `voiceover.md` |
| Curated | `liquid-glass.md` |

## Apple's design principles (`design-principles.md`)

Check these first—breaking a principle outweighs any single component rule:

| Principle | Test question |
| --- | --- |
| **Purpose** | What is this screen's single job, and does the design serve it? |
| **Agency** | Can people explore, skip, and recover from mistakes? |
| **Responsibility** | Are permissions, data use, and intent transparent? |
| **Familiarity** | Do patterns match the platform and stay consistent? |
| **Flexibility** | Does it adapt across sizes, inputs, text sizes, and abilities? |
| **Simplicity** | Has every element earned its place? |
| **Craft** | Are spacing, alignment, copy, and motion finished? |
| **Delight** | Is there human feeling without mistaking decoration for delight? |

For older, sharper principle tests (consistency questions, acceptable modes), load `references/heritage.md`.

## Review process

### 1. Establish context & scope

Pin down **platform & framework**, **app category**, **artifact limits**, **design thesis** (one
sentence: screen's job + defining trait), and **goal**. Infer when obvious; ask only if it changes
the verdict.
- **Web or Android-only apps:** Apply principles, foundations (accessibility, color, typography,
  layout, writing), and craft—not Apple-native platform conventions. State what applies.
- **Screenshots:** Support layout, hierarchy, and copy. Contrast/sizes need real values; mark
  sampled estimates clearly. A missing measurement is a limit, not a finding.

### 2. Audit through five lenses

1. **Accessibility (Critical):** Dynamic Type scaling; type sizes (mobile 17 pt default / 11 pt min,
   desktop 13 pt / 10 pt, avoid thin/light small text); contrast (4.5:1 up to 17 pt; 3:1 at 18 pt+
   or bold—compute from real hex values when available); controls (mobile 44×44 default / 28×28 min,
   desktop 28×28 / 20×20) with adequate spacing; never color alone; VoiceOver labels on icon-only
   controls; keyboard-only desktop navigation; Reduced Motion, Reduced Transparency, and Increased
   Contrast answers.
2. **Platform conventions (High):**
   - *Mobile:* Tab bar for top-level navigation (not actions, <= 5 tabs, never hidden/disabled,
     single-word labels, filled symbols); toolbar for view actions (1–2 prominent max, monochrome
     over colorful content); primary search position; one sheet at a time with grabber, swipe-dismiss,
     and non-Done exit; safe areas, one-handed reach, iPhone Duo system side-bar order.
   - *Desktop:* Every toolbar command in the menu bar; standard shortcuts; fluid windows, no
     critical info in bottom bar; <= 2-level hideable sidebar; fixed-toolbar Settings under app
     menu; hover/pointer feedback.
   - *Both:* Light/Dark with no app appearance switch, semantic colors, Liquid Glass only on the
     floating functional layer (`liquid-glass.md`).
3. **Visual design & craft (High/Medium):** One color = one meaning; restrained type scale;
   alignment/grouping/progressive disclosure; size-class/space adaptability; matching vector icons;
   brief cancellable motion. *Craft check:* Does it have a point of view? Reject generic generated
   templates (warm cream + serif + terracotta; near-black + acid-green/vermilion; hairline-rule
   zero-radius broadsheet; big-number gradient hero; non-sequential `01/02/03` markers). Spend
   boldness in one signature element (`branding.md`) and name one accessory to remove (or state it
   is already lean).
4. **Interaction (Medium):** Immediate loading feedback & determinate progress; inline feedback over
   alerts (never on launch, never for undoable actions, never default "OK" unless informational);
   destructive confirmation + Cancel; Undo for the rest; short single-task modals; dynamic form
   validation and system data entry.
5. **Writing (Medium):** Specific verb labels ("Save Changes", not "Submit"); consistent action
   naming; consistent platform capitalization (Apple Title Case vs. Material sentence case);
   actionable, non-apologetic errors; empty states invite the next action; no jargon.

### 3. Report format

- **Swift quick check / lint / runtime audit:** Output compact diagnostics directly (skip the full
  visual report and Craft notes unless requested), capped at 3–5 highest-severity items (+ count of
  suppressed items):
  ```text
  [HIG-ID][CRITICAL|HIGH|MEDIUM|LOW] path:line — concise issue
    Fix: concrete SwiftUI/UIKit change
    Ref: file.md › Heading
  ```
- **Full design review** (include only sections with content; Summary always appears; strong designs
  get a short review):
  ```text
  ## Design review: <name>
  ### Summary         (2–3 sentences, rating: Excellent / Good / Needs work / Critical issues, thesis)
  ### Critical        (- **What**, **Why** (`file.md › Heading` + quote), **Fix** in user's framework)
  ### Improvements    (same format, tagged High / Medium / Low)
  ### Craft notes     (POV, type, signature element, restraint—tagged findings or short paragraph)
  ### What works      (specific patterns to keep)
  ### Platform notes  (mobile vs. desktop, iPhone Duo, or framework notes)
  ```
  Rating rule: any Critical → **Critical issues**; several High → **Needs work**; no Critical & <= 2
  High → **Good**; nothing above Medium + clear POV → **Excellent**.

## Specialized modes

- **Swift quick check, lint & runtime audit:** Run `hig_lint.py` (Tier-1 static/AST), `hig_context.py`
  (Tier-2 JIT rules, `--diff`/`--staged`/`--query`/`--slug`/`--api`), and `hig_runtime_audit.py`
  (Tier-3 `.xcresult`/log). Use compact diagnostic format.
- **Code audit:** Follow `references/code-audit.md` (Apple Pay, Liquid Glass, iPhone Duo) and cite
  `path:line` beside each guideline.
- **Accessibility audit:** Run `hig_context.py --slug accessibility,voiceover,typography,color,motion --tier all --no-fixes`
  plus Lens 1, VoiceOver order, keyboard paths, Switch Control, captions, and haptic/visual doubles.
  For native builds with UI tests, run `hig_runtime_audit.py --xcresult <bundle>` (or `--print-xctest`).
- **Apple Pay:** `apple-pay.md`, `buttons.md`, `references/code-audit.md`. Any non-Apple button,
  altered mark, Apple logo (`U+F8FF`) in text, or text-only Apple Pay beside logos is **Critical**
  (App Review 4.9), including web checkouts opened in-app.
- **Liquid Glass:** `liquid-glass.md` checklist + `references/code-audit.md`. Trigger on glass,
  blur, frosted, or translucent bars.
- **iPhone Duo:** `designing-for-iphone-duo.md` + `references/code-audit.md`. Check outer/inner/fold
  poses, size classes over fixed widths, system side bars, reserved regions, and custom Flutter/JS bars.
- **Dark mode:** `--slug dark-mode,color,materials`. Semantic colors, softened whites, no app toggle.
- **App icon:** `--slug app-icons,icons`. Layered, centered, no UI replicas, dark/tinted variants.
- **Navigation / Onboarding / Forms / AI / Widgets & Live Activities:** Query `--slug` for the
  relevant pages (e.g. `tab-bars,sidebars,split-views,toolbars`; `onboarding,launching,privacy`;
  `entering-data,text-fields,pickers,virtual-keyboards`; `generative-ai,machine-learning`;
  `widgets,live-activities` including 28 pt Dynamic Island circle & removable StandBy backgrounds).
- **Retro / heritage:** Load `references/heritage.md` + `branding.md`. Keep era in the brand layer;
  navigation, controls, and accessibility stay current; flag accidental superseded patterns.

## Design improvement mode

Review first, then deliver a concrete plan (edit files only if asked):
1. **Ground in subject:** Product, audience, screen's single job, subject-derived visual world.
2. **Compact token system:** 4–6 named colors (light/dark + contrast ratios), display/body/utility
   type scale, 1-sentence + ASCII compact/regular wireframe, 1 signature element, <= 1 motion moment.
3. **Self-critique plan:** If it fits a different product unchanged, it's generic—revise and explain
   why while keeping system navigation/controls.
4. **Concrete framework fixes:** Exact hex/contrast, type styles, system component replacements.
5. **Sequence:** Accessibility → conventions → craft → polish.
6. **Final pass:** Remove one accessory (or note it's lean) and verify responsive/focus/reduced-motion/XXXL floor.

## Working rules

- **Numbers, not adjectives** ("12 px `#AAAAAA` on white, 2.3:1 < 4.5:1", not "hard to read").
- **Cite it or label as judgment**—never invent a guideline.
- **Speak the user's framework** (`BottomNavigationBar` for Flutter, not `UITabBarController`).
- **Name trade-offs** when business goals collide with HIG, then recommend.
- **Review the flow, not just one screen; don't over-critique strong work; don't flatten personality.**
