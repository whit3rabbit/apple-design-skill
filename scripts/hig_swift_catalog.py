#!/usr/bin/env python3
"""
Shared catalog and utilities for Apple HIG graph building, rule extraction,
and Swift JIT context routing. Zero external dependencies (Python 3 stdlib only).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HIG_DIR = REPO_ROOT / "references" / "hig"
RULES_DIR = REPO_ROOT / "rules"
DEFAULT_GRAPH_PATH = RULES_DIR / "graph.json"
DEFAULT_RULES_PATH = RULES_DIR / "hig-rules.json"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "rule-extract"

# ---------------------------------------------------------------------------
# Curated SwiftUI / UIKit / AppKit API -> HIG slug routing map
# Supplements the 600+ Apple doc links auto-extracted from references/hig/*.md
# ---------------------------------------------------------------------------

SWIFT_API_TO_SLUGS: dict[str, list[str]] = {
    # Navigation & structure
    "TabView": ["tab-bars", "tab-views", "designing-for-ios"],
    "Tab": ["tab-bars"],
    ".tabItem": ["tab-bars"],
    ".tabViewStyle": ["tab-bars", "tab-views"],
    ".tabBarMinimizeBehavior": ["tab-bars"],
    "TabViewCustomization": ["tab-bars"],
    "UITabBarController": ["tab-bars"],
    "UITabBar": ["tab-bars"],
    "NSTabView": ["tab-views"],
    "NavigationSplitView": ["split-views", "sidebars", "designing-for-iphone-duo"],
    "UISplitViewController": ["split-views", "sidebars", "designing-for-iphone-duo"],
    "NSSplitViewController": ["split-views", "sidebars"],
    "NavigationStack": ["designing-for-ios", "toolbars", "layout"],
    "NavigationLink": ["lists-and-tables", "designing-for-ios"],
    "UINavigationController": ["designing-for-ios", "toolbars"],
    "SidebarListStyle": ["sidebars"],
    ".toolbar": ["toolbars", "designing-for-iphone-duo"],
    "ToolbarItem": ["toolbars", "buttons", "designing-for-iphone-duo"],
    "ToolbarItemGroup": ["toolbars", "designing-for-iphone-duo"],
    "ToolbarOverflowMenu": ["toolbars", "designing-for-iphone-duo"],
    "ToolbarItemVisibilityPriority": ["toolbars", "designing-for-iphone-duo"],
    ".toolbarBackground": ["toolbars", "liquid-glass", "materials"],
    "UIToolbar": ["toolbars"],
    "UIBarButtonItem": ["toolbars", "buttons"],
    "UIBarAppearance": ["toolbars", "liquid-glass"],
    "NSToolbar": ["toolbars", "windows"],
    "ArrangementView": ["designing-for-iphone-duo", "layout"],
    "UIArrangementViewController": ["designing-for-iphone-duo", "layout"],
    "ReservedRegion": ["designing-for-iphone-duo", "layout"],
    # Search
    ".searchable": ["searching", "search-fields"],
    "UISearchController": ["searching", "search-fields"],
    "UISearchBar": ["search-fields"],
    "NSSearchField": ["search-fields"],
    # Presentation & modality
    ".sheet": ["sheets", "modality"],
    ".fullScreenCover": ["modality", "sheets", "going-full-screen"],
    ".presentationDetents": ["sheets"],
    ".presentationDragIndicator": ["sheets"],
    ".interactiveDismissDisabled": ["sheets", "modality"],
    "UISheetPresentationController": ["sheets"],
    ".popover": ["popovers", "modality"],
    "UIPopoverPresentationController": ["popovers"],
    "NSPopover": ["popovers"],
    ".alert": ["alerts", "modality", "feedback"],
    "UIAlertController": ["alerts", "action-sheets"],
    "NSAlert": ["alerts"],
    ".confirmationDialog": ["action-sheets", "alerts", "modality"],
    "WindowGroup": ["windows", "designing-for-macos"],
    "Window": ["windows", "designing-for-macos"],
    "DocumentGroup": ["file-management", "windows"],
    "Settings": ["settings", "designing-for-macos"],
    "NSPanel": ["panels", "windows"],
    "NSWindow": ["windows", "designing-for-macos"],
    # Menus & actions
    "Button": ["buttons", "accessibility"],
    ".buttonStyle": ["buttons", "liquid-glass"],
    ".buttonRole": ["buttons"],
    "ButtonRole": ["buttons"],
    "UIButton": ["buttons"],
    "NSButton": ["buttons"],
    "Menu": ["menus", "pull-down-buttons"],
    "UIMenu": ["menus"],
    "NSMenu": ["menus", "the-menu-bar"],
    "CommandMenu": ["the-menu-bar", "keyboards"],
    "CommandGroup": ["the-menu-bar", "keyboards"],
    ".commands": ["the-menu-bar", "keyboards"],
    ".keyboardShortcut": ["keyboards", "the-menu-bar"],
    ".contextMenu": ["context-menus", "menus"],
    "UIContextMenuInteraction": ["context-menus"],
    "ShareLink": ["activity-views", "collaboration-and-sharing"],
    "UIActivityViewController": ["activity-views"],
    ".swipeActions": ["lists-and-tables", "gestures"],
    "EditButton": ["lists-and-tables", "edit-menus"],
    # Selection & forms
    "Form": ["entering-data", "settings", "lists-and-tables"],
    "TextField": ["text-fields", "entering-data", "virtual-keyboards"],
    "SecureField": ["text-fields", "entering-data", "managing-accounts"],
    "TextEditor": ["text-views", "entering-data"],
    "UITextField": ["text-fields", "entering-data"],
    "UITextView": ["text-views"],
    "NSTextField": ["text-fields"],
    "Picker": ["pickers", "pop-up-buttons", "segmented-controls"],
    "DatePicker": ["pickers", "entering-data"],
    "ColorPicker": ["color-wells", "color"],
    "Toggle": ["toggles"],
    "UISwitch": ["toggles"],
    "Slider": ["sliders"],
    "UISlider": ["sliders"],
    "Stepper": ["steppers"],
    "UIStepper": ["steppers"],
    "SegmentedPickerStyle": ["segmented-controls"],
    "UISegmentedControl": ["segmented-controls"],
    "NSSegmentedControl": ["segmented-controls"],
    ".keyboardType": ["virtual-keyboards", "entering-data"],
    ".textContentType": ["entering-data", "text-fields"],
    ".submitLabel": ["virtual-keyboards", "text-fields"],
    # Lists, layout & content
    "List": ["lists-and-tables", "scroll-views"],
    "Table": ["lists-and-tables", "outline-views"],
    "UITableView": ["lists-and-tables"],
    "UICollectionView": ["collections"],
    "LazyVGrid": ["collections", "layout"],
    "LazyHGrid": ["collections", "layout"],
    "ScrollView": ["scroll-views", "layout"],
    "DisclosureGroup": ["disclosure-controls", "outline-views"],
    "GroupBox": ["boxes"],
    "NSBox": ["boxes"],
    "Label": ["labels", "sf-symbols", "accessibility"],
    "Image": ["images", "sf-symbols", "icons"],
    "AsyncImage": ["images", "loading"],
    "WKWebView": ["web-views"],
    "SFSafariViewController": ["web-views"],
    "Chart": ["charts", "charting-data"],
    # Status & feedback
    "ProgressView": ["progress-indicators", "loading"],
    "UIActivityIndicatorView": ["progress-indicators", "loading"],
    "Gauge": ["gauges"],
    ".redacted": ["loading"],
    ".sensoryFeedback": ["playing-haptics", "feedback"],
    "UIImpactFeedbackGenerator": ["playing-haptics"],
    "UINotificationFeedbackGenerator": ["playing-haptics", "feedback"],
    "ContentUnavailableView": ["feedback", "searching", "writing"],
    # Liquid Glass, materials, color, typography, dark mode
    ".glassEffect": ["liquid-glass", "materials", "color"],
    "GlassEffectContainer": ["liquid-glass", "materials"],
    "UIGlassEffect": ["liquid-glass", "materials"],
    "UIGlassContainerEffect": ["liquid-glass", "materials"],
    "NSGlassEffectView": ["liquid-glass", "materials"],
    ".material": ["materials"],
    ".ultraThinMaterial": ["materials"],
    ".thinMaterial": ["materials"],
    ".regularMaterial": ["materials"],
    ".thickMaterial": ["materials"],
    "UIVisualEffectView": ["materials", "liquid-glass"],
    "NSVisualEffectView": ["materials", "liquid-glass"],
    ".font": ["typography", "accessibility"],
    "Font": ["typography"],
    "UIFont": ["typography"],
    "ScaledMetric": ["typography", "accessibility"],
    ".dynamicTypeSize": ["typography", "accessibility"],
    "Color": ["color", "dark-mode", "accessibility"],
    "UIColor": ["color", "dark-mode"],
    "NSColor": ["color", "dark-mode"],
    ".foregroundStyle": ["color", "dark-mode"],
    ".preferredColorScheme": ["dark-mode"],
    ".colorScheme": ["dark-mode"],
    # Layout, size classes, screen dependencies
    "horizontalSizeClass": ["layout", "designing-for-iphone-duo"],
    "verticalSizeClass": ["layout", "designing-for-iphone-duo"],
    "GeometryReader": ["layout", "designing-for-iphone-duo"],
    "ViewThatFits": ["layout", "typography"],
    "UIScreen.main": ["layout", "designing-for-iphone-duo"],
    "userInterfaceIdiom": ["layout", "designing-for-iphone-duo"],
    ".safeAreaInset": ["layout"],
    ".ignoresSafeArea": ["layout", "designing-for-iphone-duo"],
    # Accessibility & motion
    "accessibilityReduceMotion": ["accessibility", "motion"],
    "accessibilityReduceTransparency": ["accessibility", "liquid-glass", "materials"],
    "colorSchemeContrast": ["accessibility", "color"],
    ".accessibilityLabel": ["accessibility", "voiceover"],
    ".accessibilityHint": ["accessibility", "voiceover"],
    ".accessibilityElement": ["accessibility", "voiceover"],
    ".accessibilityHidden": ["accessibility", "voiceover"],
    ".animation": ["motion", "accessibility"],
    "withAnimation": ["motion", "accessibility"],
    ".focusable": ["focus-and-selection", "keyboards"],
    "FocusState": ["focus-and-selection", "keyboards"],
    # System experiences & technologies
    "PayWithApplePayButton": ["apple-pay", "buttons"],
    "PKPaymentButton": ["apple-pay", "buttons"],
    "PKPaymentAuthorizationController": ["apple-pay"],
    "SignInWithAppleButton": ["sign-in-with-apple", "managing-accounts"],
    "ASAuthorizationAppleIDButton": ["sign-in-with-apple"],
    "Widget": ["widgets"],
    "WidgetConfiguration": ["widgets"],
    "ControlWidget": ["controls"],
    "ActivityAttributes": ["live-activities"],
    "ActivityConfiguration": ["live-activities"],
    "DynamicIsland": ["live-activities"],
    "UNUserNotificationCenter": ["notifications", "managing-notifications"],
    "AppIntent": ["app-shortcuts", "siri", "snippets"],
    "AppShortcutsProvider": ["app-shortcuts"],
    "StoreKit": ["apple-in-app-purchase"],
    "ProductView": ["apple-in-app-purchase"],
    "SubscriptionStoreView": ["apple-in-app-purchase"],
    "Map": ["maps"],
    "MKMapView": ["maps"],
    "AVPlayer": ["playing-video", "playing-audio"],
    "VideoPlayer": ["playing-video"],
    "AVRoutePickerView": ["airplay"],
    "UndoManager": ["undo-and-redo"],
    ".draggable": ["drag-and-drop"],
    ".dropDestination": ["drag-and-drop"],
    "PencilInteraction": ["apple-pencil-and-scribble"],
    "SKStoreReviewController": ["ratings-and-reviews"],
    "requestReview": ["ratings-and-reviews"],
    "FoundationModels": ["generative-ai"],
}

# ---------------------------------------------------------------------------
# Cross-platform UI API -> HIG slug equivalency map
# Derived from references/cross-platform.md, code-audit.md, and liquid-glass.md.
# Routes Flutter (.dart), React Native (.tsx/.jsx), and Tauri/Electron/Web files
# directly into the same 1,412 quote-verified HIG rules without duplicating docs.
# ---------------------------------------------------------------------------

CROSS_PLATFORM_API_TO_SLUGS: dict[str, dict[str, list[str]]] = {
    "flutter": {
        # Navigation & structure
        "BottomNavigationBar": ["tab-bars", "designing-for-iphone-duo"],
        "NavigationBar": ["tab-bars", "designing-for-iphone-duo"],
        "CupertinoTabBar": ["tab-bars", "designing-for-iphone-duo"],
        "NavigationRail": ["sidebars", "split-views"],
        "NavigationDrawer": ["sidebars"],
        "AppBar": ["toolbars", "designing-for-ios"],
        "CupertinoNavigationBar": ["toolbars", "designing-for-ios"],
        "SliverAppBar": ["toolbars", "scroll-views"],
        "TabBar": ["tab-views", "tab-bars"],
        # Presentation & modality
        "showModalBottomSheet": ["sheets", "modality"],
        "showCupertinoModalPopup": ["action-sheets", "sheets", "modality"],
        "AlertDialog": ["alerts", "modality"],
        "CupertinoAlertDialog": ["alerts", "modality"],
        "SimpleDialog": ["modality", "alerts"],
        "showDialog": ["modality", "alerts"],
        # Glass & materials
        "BackdropFilter": ["liquid-glass", "materials"],
        "ImageFilter.blur": ["liquid-glass", "materials"],
        # Buttons & menus
        "ElevatedButton": ["buttons"],
        "FilledButton": ["buttons"],
        "TextButton": ["buttons"],
        "OutlinedButton": ["buttons"],
        "CupertinoButton": ["buttons"],
        "IconButton": ["buttons", "accessibility", "icons"],
        "FloatingActionButton": ["buttons", "layout"],
        "PopupMenuButton": ["menus", "pull-down-buttons"],
        "DropdownButton": ["pop-up-buttons", "pickers"],
        "GestureDetector": ["gestures", "accessibility"],
        "InkWell": ["buttons", "accessibility"],
        # Forms, input & selection
        "TextField": ["text-fields", "entering-data", "virtual-keyboards"],
        "TextFormField": ["text-fields", "entering-data", "virtual-keyboards"],
        "CupertinoTextField": ["text-fields", "entering-data"],
        "Switch": ["toggles"],
        "CupertinoSwitch": ["toggles"],
        "Slider": ["sliders"],
        "SegmentedButton": ["segmented-controls"],
        "CupertinoSegmentedControl": ["segmented-controls"],
        "SearchBar": ["search-fields", "searching"],
        "SearchAnchor": ["search-fields", "searching"],
        "showDatePicker": ["pickers", "entering-data"],
        "CupertinoDatePicker": ["pickers", "entering-data"],
        # Lists, status & feedback
        "ListView": ["lists-and-tables", "scroll-views"],
        "GridView": ["collections", "layout"],
        "CustomScrollView": ["scroll-views"],
        "CircularProgressIndicator": ["progress-indicators", "loading"],
        "LinearProgressIndicator": ["progress-indicators", "loading"],
        "SnackBar": ["feedback"],
        "Tooltip": ["offering-help", "pointing-devices"],
        # Layout, theming, accessibility & payments
        "SafeArea": ["layout", "designing-for-iphone-duo"],
        "LayoutBuilder": ["layout", "designing-for-iphone-duo"],
        "MediaQuery.of": ["layout", "designing-for-iphone-duo", "accessibility"],
        "MediaQuery.sizeOf": ["layout", "designing-for-iphone-duo"],
        "ThemeData": ["color", "dark-mode", "typography"],
        "Semantics": ["accessibility", "voiceover"],
        "ApplePayButton": ["apple-pay", "buttons"],
        "SignInWithAppleButton": ["sign-in-with-apple"],
        "webview_flutter": ["web-views"],
    },
    "react-native": {
        # Navigation & structure
        "createBottomTabNavigator": ["tab-bars", "designing-for-iphone-duo"],
        "BottomTabBar": ["tab-bars", "designing-for-iphone-duo"],
        "createDrawerNavigator": ["sidebars"],
        # Presentation & modality
        "Modal": ["sheets", "modality"],
        "BottomSheet": ["sheets", "modality"],
        "ActionSheetIOS": ["action-sheets", "modality"],
        "Alert.alert": ["alerts", "modality"],
        # Glass & materials
        "BlurView": ["liquid-glass", "materials"],
        # Buttons, inputs & lists
        "Pressable": ["buttons", "accessibility"],
        "TouchableOpacity": ["buttons", "accessibility"],
        "TouchableHighlight": ["buttons", "accessibility"],
        "TextInput": ["text-fields", "entering-data", "virtual-keyboards"],
        "Switch": ["toggles"],
        "FlatList": ["lists-and-tables", "scroll-views"],
        "SectionList": ["lists-and-tables", "scroll-views"],
        "ScrollView": ["scroll-views"],
        "ActivityIndicator": ["progress-indicators", "loading"],
        # Layout, accessibility, dark mode & payments
        "SafeAreaView": ["layout", "designing-for-iphone-duo"],
        "useSafeAreaInsets": ["layout", "designing-for-iphone-duo"],
        "useWindowDimensions": ["layout", "designing-for-iphone-duo"],
        "Dimensions.get": ["layout", "designing-for-iphone-duo"],
        "useColorScheme": ["dark-mode", "color"],
        "AccessibilityInfo": ["accessibility", "motion", "liquid-glass"],
        "accessibilityLabel": ["accessibility", "voiceover"],
        "accessibilityRole": ["accessibility", "voiceover"],
        "KeyboardAvoidingView": ["virtual-keyboards", "layout"],
        "StatusBar": ["status-bars"],
        "PlatformPayButton": ["apple-pay", "buttons"],
        "AppleButton": ["sign-in-with-apple"],
    },
    "web-desktop": {
        # Materials & accessibility media queries
        "backdrop-filter": ["liquid-glass", "materials"],
        "-webkit-backdrop-filter": ["liquid-glass", "materials"],
        "prefers-color-scheme": ["dark-mode", "color"],
        "prefers-reduced-motion": ["motion", "accessibility"],
        "prefers-contrast": ["accessibility", "color"],
        "prefers-reduced-transparency": ["liquid-glass", "accessibility", "materials"],
        "@container": ["layout"],
        "focus-visible": ["focus-and-selection", "keyboards"],
        # Tauri & Electron native desktop APIs
        "window-vibrancy": ["liquid-glass", "materials", "windows"],
        "vibrancy": ["liquid-glass", "materials", "windows"],
        "backgroundMaterial": ["materials", "windows"],
        "BrowserWindow": ["windows", "designing-for-macos"],
        "Menu.buildFromTemplate": ["the-menu-bar", "menus"],
        "Tray": ["the-menu-bar", "dock-menus"],
        # Semantic HTML / ARIA / Apple Pay on Web
        "<dialog": ["sheets", "modality", "alerts"],
        "showModal": ["modality", "sheets"],
        "aria-label": ["accessibility", "voiceover"],
        'role="dialog"': ["modality", "sheets"],
        'role="tablist"': ["tab-views", "tab-bars"],
        'role="menu"': ["menus", "context-menus"],
        "apple-pay-button": ["apple-pay", "buttons"],
        "-apple-pay-button": ["apple-pay", "buttons"],
    },
}

# ---------------------------------------------------------------------------
# Curated Tier-1 deterministic Swift rules (grounded in verbatim HIG quotes)
# Used both in rules/hig-rules.json and for optional quick static checks.
# ---------------------------------------------------------------------------

CURATED_STATIC_RULES: list[dict[str, Any]] = [
    {
        "id": "HIG-TYPO-001",
        "slug": "typography",
        "heading": "Using system fonts",
        "tier": "static",
        "severity": "critical",
        "platforms": ["ios", "ipados"],
        "swiftui_apis": [".font", "Font", "ScaledMetric"],
        "uikit_apis": ["UIFont"],
        "appkit_apis": [],
        "rule": "Use Dynamic Type text styles or scaled custom fonts instead of fixed point sizes.",
        "anti_pattern": ".font(.system(size: 14)) or Font.custom(\"Name\", fixedSize: 14) without Dynamic Type scaling",
        "fix_swiftui": "Use semantic text styles like `.font(.body)` / `.font(.subheadline)`, or `Font.custom(\"Name\", size: 14, relativeTo: .body)` / `@ScaledMetric`.",
        "fix_uikit": "Use `UIFont.preferredFont(forTextStyle:)` or `UIFontMetrics.default.scaledFont(for:)` with `adjustsFontForContentSizeCategory = true`.",
        "quote": "Using text styles with the system fonts also ensures support for Dynamic Type and larger accessibility type sizes (where available), which let people choose the text size that works for them.",
        "pattern": r"\.font\(\s*(?:Font)?\.?(?:system\(\s*size\s*:|custom\([^)]*fixedSize\s*:)",
    },
    {
        "id": "HIG-TYPO-002",
        "slug": "typography",
        "heading": "Ensuring legibility",
        "tier": "static",
        "severity": "medium",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".font", ".fontWeight", "Font"],
        "uikit_apis": ["UIFont"],
        "appkit_apis": ["NSFont"],
        "rule": "Avoid Ultralight, Thin, and Light font weights, especially for small text.",
        "anti_pattern": ".fontWeight(.ultraLight), .fontWeight(.thin), or .fontWeight(.light)",
        "fix_swiftui": "Prefer `.regular`, `.medium`, `.semibold`, or `.bold` font weights for legible UI text.",
        "fix_uikit": "Use `.regular`, `.medium`, `.semibold`, or `.bold` weight.",
        "quote": "For example, if you’re using system-provided fonts, prefer Regular, Medium, Semibold, or Bold font weights, and avoid Ultralight, Thin, and Light font weights, which can be difficult to see, especially when text is small.",
        "pattern": r"(?:\.fontWeight\(\s*\.(?:ultraLight|thin|light)\s*\)|weight\s*:\s*\.(?:ultraLight|thin|light)\b)",
    },
    {
        "id": "HIG-COLOR-001",
        "slug": "dark-mode",
        "heading": "Best practices",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".preferredColorScheme"],
        "uikit_apis": ["overrideUserInterfaceStyle"],
        "appkit_apis": ["appearance"],
        "rule": "Respect the systemwide appearance setting instead of forcing light/dark mode or adding an app-specific appearance toggle.",
        "anti_pattern": ".preferredColorScheme(.light) / .preferredColorScheme(.dark) or overrideUserInterfaceStyle locking the app",
        "fix_swiftui": "Remove `.preferredColorScheme(...)` so the app adapts to system Light/Dark/Auto modes (only keep dark-only for immersive media viewing).",
        "fix_uikit": "Remove `overrideUserInterfaceStyle` unless presenting immersive media.",
        "quote": "Avoid offering an app-specific appearance setting.",
        "pattern": r"(?:\.preferredColorScheme\s*\(\s*\.(?:light|dark)\s*\)|overrideUserInterfaceStyle\s*=\s*\.(?:light|dark)\b)",
    },
    {
        "id": "HIG-COLOR-002",
        "slug": "dark-mode",
        "heading": "Dark Mode colors",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": ["Color", ".foregroundStyle", ".background"],
        "uikit_apis": ["UIColor"],
        "appkit_apis": ["NSColor"],
        "rule": "Use adaptive semantic system colors or Asset Catalog Color Sets instead of hardcoded RGB/hex color values.",
        "anti_pattern": "Color(red: ..., green: ..., blue: ...) or UIColor(red: ...) hardcoded in views",
        "fix_swiftui": "Use semantic colors (`.primary`, `.secondary`, `Color(.systemBackground)`) or an Asset Catalog Color Set with Light, Dark, and High Contrast variants.",
        "fix_uikit": "Use dynamic `UIColor.label`, `UIColor.systemBackground`, or a named Color Set in Assets.xcassets.",
        "quote": "Avoid using hard-coded color values or colors that don’t adapt.",
        "pattern": r"\b(?:Color|UIColor|NSColor)\(\s*(?:red\s*:|white\s*:|hex\s*:)",
    },
    {
        "id": "HIG-A11Y-001",
        "slug": "accessibility",
        "heading": "Vision",
        "tier": "static",
        "severity": "critical",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": ["Button", "Image", "Label", ".accessibilityLabel"],
        "uikit_apis": ["UIButton", "UIBarButtonItem"],
        "appkit_apis": ["NSButton"],
        "rule": "Every icon-only button or toolbar item needs a descriptive accessible label for VoiceOver and Voice Control.",
        "anti_pattern": "Button { ... } label: { Image(systemName: \"...\") } without Label or .accessibilityLabel",
        "fix_swiftui": "Use `Label(\"Action\", systemImage: \"...\").labelStyle(.iconOnly)` or attach `.accessibilityLabel(\"Action\")`.",
        "fix_uikit": "Set `button.accessibilityLabel` or provide a `title` on `UIBarButtonItem`.",
        "quote": "Describe your app’s interface and content for VoiceOver.",
        "pattern": r"(?:Button|ToolbarItem)[^{]*\{[\s\S]{0,180}?Image\(\s*systemName\s*:\s*\"[^\"]+\"\s*\)",
    },
    {
        "id": "HIG-A11Y-002",
        "slug": "accessibility",
        "heading": "Mobility",
        "tier": "static",
        "severity": "critical",
        "platforms": ["ios", "ipados"],
        "swiftui_apis": ["Button", ".frame"],
        "uikit_apis": ["UIButton"],
        "appkit_apis": [],
        "rule": "Interactive controls on iOS/iPadOS should have a 44x44 pt hit target (minimum 28x28 pt).",
        "anti_pattern": "Explicit small frame (< 28 pt) on a tappable control without expanded contentShape/padding",
        "fix_swiftui": "Use `.frame(minWidth: 44, minHeight: 44)` and `.contentShape(Rectangle())` around compact icons.",
        "fix_uikit": "Keep interactive control frames >= 44x44 pt or override `point(inside:with:)`.",
        "quote": "Offer sufficiently sized controls.",
        "pattern": r"\.frame\([^)]*(?:width|height)\s*:\s*(?:1\d|2[0-7])(?:\.0)?\b",
    },
    {
        "id": "HIG-DUO-001",
        "slug": "designing-for-iphone-duo",
        "heading": "Best practices",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados"],
        "swiftui_apis": ["UIScreen.main", "GeometryReader", "horizontalSizeClass"],
        "uikit_apis": ["UIScreen.main"],
        "appkit_apis": [],
        "rule": "Avoid UIScreen.main.bounds and fixed display widths; build layouts with size classes, safe areas, and window bounds.",
        "anti_pattern": "UIScreen.main.bounds used to size views or branch layout",
        "fix_swiftui": "Use `@Environment(\\.horizontalSizeClass)`, `ViewThatFits`, `NavigationSplitView`, or `GeometryReader` / containerRelativeFrame.",
        "fix_uikit": "Use `traitCollection.horizontalSizeClass`, `view.bounds`, and `layoutMarginsGuide`.",
        "quote": "Use size classes, layout margins, and safe area insets to lay out controls and content. Avoid fixed widths and display-specific dependencies.",
        "pattern": r"\bUIScreen\.main\.bounds\b",
    },
    {
        "id": "HIG-DUO-002",
        "slug": "designing-for-iphone-duo",
        "heading": "Device poses",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados"],
        "swiftui_apis": ["userInterfaceIdiom", "horizontalSizeClass"],
        "uikit_apis": ["userInterfaceIdiom", "UIDevice"],
        "appkit_apis": [],
        "rule": "Branch layouts on size classes rather than device idiom (`userInterfaceIdiom == .phone`).",
        "anti_pattern": "UIDevice.current.userInterfaceIdiom == .phone deciding single vs. split layout",
        "fix_swiftui": "Read `@Environment(\\.horizontalSizeClass)` (`.compact` vs `.regular`) so iPad multitasking and iPhone Duo inner displays adapt automatically.",
        "fix_uikit": "Check `traitCollection.horizontalSizeClass` instead of `UIDevice.current.userInterfaceIdiom`.",
        "quote": "Supporting the device’s various poses doesn’t mean designing a custom layout for each one: instead, use size classes so your app adapts naturally as it changes size.",
        "pattern": r"\buserInterfaceIdiom\s*[!=]=\s*\.(?:phone|pad)\b",
    },
    {
        "id": "HIG-DUO-003",
        "slug": "designing-for-iphone-duo",
        "heading": "Best practices",
        "tier": "static",
        "severity": "medium",
        "platforms": ["ios", "ipados"],
        "swiftui_apis": [".frame"],
        "uikit_apis": [],
        "appkit_apis": [],
        "rule": "Avoid hardcoding phone screen widths (e.g. `.frame(width: 390)`) that break resizing across devices and poses.",
        "anti_pattern": "Fixed frame width matching an iPhone viewport (320–440 pt)",
        "fix_swiftui": "Use `.frame(maxWidth: .infinity)` or `.frame(maxWidth: 600)` with flexible padding and layout margins.",
        "fix_uikit": "Pin constraints to `safeAreaLayoutGuide` or `readableContentGuide` instead of fixed width constants.",
        "quote": "Avoid fixed widths and display-specific dependencies.",
        "pattern": r"\.frame\([^)]*\bwidth\s*:\s*(?:3[2-9]\d|4[0-4]\d)(?:\.\d+)?\b",
    },
    {
        "id": "HIG-LG-001",
        "slug": "liquid-glass",
        "heading": "Where the material belongs",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".glassEffect", "GlassEffectContainer"],
        "uikit_apis": ["UIGlassEffect"],
        "appkit_apis": ["NSGlassEffectView"],
        "rule": "Apply Liquid Glass only to floating controls/navigation, never to content-layer rows, cards, or backgrounds; handle Reduce Transparency.",
        "anti_pattern": ".glassEffect(...) applied to list cells/cards or without checking accessibilityReduceTransparency on custom glass",
        "fix_swiftui": "Keep `.glassEffect(...)` on floating controls grouped with `GlassEffectContainer`, and use standard materials or opaque fills in the content layer.",
        "fix_uikit": "Use `UIGlassEffect` / `UIGlassContainerEffect` only for floating functional controls.",
        "quote": "Don't use Liquid Glass in the content layer.",
        "pattern": r"\.(?:glassEffect)\s*\(\s*\.clear\b",
    },
    {
        "id": "HIG-LG-002",
        "slug": "liquid-glass",
        "heading": "Review checklist",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".toolbarBackground"],
        "uikit_apis": ["UIBarAppearance"],
        "appkit_apis": [],
        "rule": "Avoid painting an opaque background beneath a toolbar or tab bar; let the system material and scroll edge effect separate controls from content.",
        "anti_pattern": ".toolbarBackground(Color..., for: .navigationBar / .tabBar) with opaque color",
        "fix_swiftui": "Remove custom opaque `.toolbarBackground(...)` so system Liquid Glass and scroll edge effects work automatically.",
        "fix_uikit": "Avoid replacing `UIBarAppearance.backgroundColor` with an opaque fill unless required by media chrome.",
        "quote": "Content fades and blurs where it meets a bar instead of colliding with it, and no solid or semi-opaque background color is painted beneath the bar in place of the material",
        "pattern": r"\.toolbarBackground\([^)]*(?:Color\.|\.visible)",
    },
    {
        "id": "HIG-LG-003",
        "slug": "liquid-glass",
        "heading": "SwiftUI, UIKit, and AppKit",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": ["UIDesignRequiresCompatibility"],
        "uikit_apis": ["UIDesignRequiresCompatibility"],
        "appkit_apis": ["UIDesignRequiresCompatibility"],
        "rule": "Remove temporary `UIDesignRequiresCompatibility` compatibility opt-out keys when targeting current SDKs.",
        "anti_pattern": "UIDesignRequiresCompatibility set to true/YES in Info.plist",
        "fix_swiftui": "Adopt standard system controls and remove `UIDesignRequiresCompatibility` from `Info.plist`.",
        "fix_uikit": "Audit custom bar appearances and remove `UIDesignRequiresCompatibility` from `Info.plist`.",
        "quote": "Apple documents it as temporary, and the system ignores it when you build for iOS, iPadOS, Mac Catalyst, or macOS 27 or later.",
        "pattern": r"UIDesignRequiresCompatibility",
    },
    {
        "id": "HIG-PAY-001",
        "slug": "apple-pay",
        "heading": "Referring to Apple Pay",
        "tier": "static",
        "severity": "critical",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": ["PayWithApplePayButton"],
        "uikit_apis": ["PKPaymentButton"],
        "appkit_apis": ["PKPaymentButton"],
        "rule": "Never use the Apple logo glyph (U+F8FF / ) in text or to hand-draw an Apple Pay button (App Review 4.9 rejection).",
        "anti_pattern": "\\u{F8FF} or literal  glyph next to Pay",
        "fix_swiftui": "Use `PayWithApplePayButton` from PassKit/SwiftUI and write 'Apple Pay' in plain text.",
        "fix_uikit": "Use `PKPaymentButton(paymentButtonType:paymentButtonStyle:)`.",
        "quote": "Never use the Apple logo to represent the name Apple in text.",
        "pattern": r"(?:\uf8ff|\\u\{?[fF]8[fF]{2}\}?|&#x[fF]8[fF]{2};)",
    },
    {
        "id": "HIG-BTN-001",
        "slug": "buttons",
        "heading": "Role",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": ["Button", "ButtonRole", ".keyboardShortcut"],
        "uikit_apis": ["UIButton", "UIAlertAction"],
        "appkit_apis": ["NSButton"],
        "rule": "Never assign the primary/default action role to a destructive button.",
        "anti_pattern": "Button(role: .destructive) paired with .keyboardShortcut(.defaultAction) or .buttonStyle(.borderedProminent)",
        "fix_swiftui": "Keep destructive buttons distinct (`.destructive` role) and assign `.defaultAction` / prominent style only to non-destructive primary actions.",
        "fix_uikit": "Do not set `preferredAction` on a `UIAlertAction` whose style is `.destructive`.",
        "quote": "Don’t assign the primary role to a button that performs a destructive action, even if that action is the most likely choice.",
        "pattern": r"role\s*:\s*\.destructive[\s\S]{0,160}(?:defaultAction|borderedProminent|glassProminent)",
    },
    {
        "id": "HIG-MOTION-001",
        "slug": "accessibility",
        "heading": "Cognitive",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".animation", "withAnimation", "accessibilityReduceMotion"],
        "uikit_apis": ["UIView.animate", "isReduceMotionEnabled"],
        "appkit_apis": ["accessibilityDisplayShouldReduceMotion"],
        "rule": "Respect Reduce Motion when using repeating or prominent custom animations.",
        "anti_pattern": ".repeatForever(...) animation without checking @Environment(\\.accessibilityReduceMotion)",
        "fix_swiftui": "Read `@Environment(\\.accessibilityReduceMotion) private var reduceMotion` and disable or replace repeating/zoom motion with subtle fades when true.",
        "fix_uikit": "Check `UIAccessibility.isReduceMotionEnabled` before starting repeating or spring animations.",
        "quote": "When this setting is active, ensure your app or game responds by reducing automatic and repetitive animations, including zooming, scaling, and peripheral motion.",
        "pattern": r"\.repeatForever\s*\(",
    },
    {
        "id": "HIG-COLOR-003",
        "slug": "dark-mode",
        "heading": "Mobile (iOS, iPadOS)",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".background", "Color"],
        "uikit_apis": ["UIColor"],
        "appkit_apis": ["NSColor"],
        "rule": "Use adaptive system background colors instead of hardcoded `.white` or `.black` view backgrounds.",
        "anti_pattern": ".background(Color.white) or .background(.white) / .background(Color.black)",
        "fix_swiftui": "Use `Color(.systemBackground)` / `Color(.secondarySystemBackground)` on iOS or `.background(.background)` so Dark Mode and elevated surfaces adapt automatically.",
        "fix_uikit": "Use `UIColor.systemBackground` or `UIColor.secondarySystemGroupedBackground`.",
        "quote": "Prefer the system background colors.",
        "pattern": r"\.background\(\s*(?:Color\.|\.)(?:white|black)\s*\)",
    },
    {
        "id": "HIG-PAY-002",
        "slug": "apple-pay",
        "heading": "Offering Apple Pay",
        "tier": "static",
        "severity": "critical",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": ["Button", "PayWithApplePayButton"],
        "uikit_apis": ["UIButton", "PKPaymentButton"],
        "appkit_apis": ["NSButton", "PKPaymentButton"],
        "rule": "Never build a custom button displaying 'Apple Pay'; use Apple's system Apple Pay button API (App Review 4.9).",
        "anti_pattern": "Button(\"Apple Pay\") or Button(\"Pay with Apple Pay\") instead of PayWithApplePayButton",
        "fix_swiftui": "Use `PayWithApplePayButton` from PassKit/SwiftUI instead of a custom `Button`.",
        "fix_uikit": "Use `PKPaymentButton(paymentButtonType:paymentButtonStyle:)`.",
        "quote": "If you use a custom button to start the Apple Pay payment process, make sure your custom button doesn’t display “Apple Pay” or the Apple Pay logo.",
        "pattern": r"\bButton\s*\(\s*\"[^\"]*Apple\s*Pay[^\"]*\"",
    },
    {
        "id": "HIG-SIWA-001",
        "slug": "sign-in-with-apple",
        "heading": "Using the system-provided buttons",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": ["Button", "SignInWithAppleButton"],
        "uikit_apis": ["UIButton", "ASAuthorizationAppleIDButton"],
        "appkit_apis": ["NSButton", "ASAuthorizationAppleIDButton"],
        "rule": "Use `SignInWithAppleButton` / `ASAuthorizationAppleIDButton` instead of hand-building a 'Sign in with Apple' button.",
        "anti_pattern": "Button(\"Sign in with Apple\") instead of SignInWithAppleButton",
        "fix_swiftui": "Use `SignInWithAppleButton(.signIn) { ... } onCompletion: { ... }` from `AuthenticationServices`.",
        "fix_uikit": "Use `ASAuthorizationAppleIDButton(authorizationButtonType:authorizationButtonStyle:)`.",
        "quote": "When you use the system-provided APIs to create a Sign in with Apple button, you get the following advantages.",
        "pattern": r"\bButton\s*\(\s*\"[^\"]*Sign\s+in\s+with\s+Apple[^\"]*\"",
    },
    {
        "id": "HIG-ALERT-001",
        "slug": "alerts",
        "heading": "Best practices",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".alert", ".onAppear"],
        "uikit_apis": ["UIAlertController"],
        "appkit_apis": ["NSAlert"],
        "rule": "Avoid showing an alert immediately on app launch or view appearance.",
        "anti_pattern": "Triggering an alert flag directly inside .onAppear { showAlert = true }",
        "fix_swiftui": "Show inline status, placeholder/cached content, or `ContentUnavailableView` instead of popping an alert on appear.",
        "fix_uikit": "Display an inline banner or `UIContentUnavailableConfiguration` instead of presenting `UIAlertController` in `viewDidAppear`.",
        "quote": "Avoid showing an alert when your app starts.",
        "pattern": r"\.onAppear\s*\{[^}\n]{0,120}(?:Alert|alert)\s*=\s*true",
    },
    {
        "id": "HIG-ALERT-002",
        "slug": "alerts",
        "heading": "Buttons",
        "tier": "static",
        "severity": "medium",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".alert", "Button"],
        "uikit_apis": ["UIAlertAction"],
        "appkit_apis": ["NSAlert"],
        "rule": "Use specific action-verb titles (e.g., 'Delete', 'Save') instead of vague 'OK' or 'Submit' button titles in alerts.",
        "anti_pattern": "Button(\"OK\") or Button(\"Submit\") inside an action alert",
        "fix_swiftui": "Replace `Button(\"OK\")` / `Button(\"Submit\")` with a specific verb naming the outcome (e.g., `Button(\"Delete\", role: .destructive)`).",
        "fix_uikit": "Give `UIAlertAction` a specific verb title unless the alert is purely informational.",
        "quote": "Avoid using OK as the default button title unless the alert is purely informational.",
        "pattern": r"\.alert\([^)]*\)\s*\{[\s\S]{0,200}?Button\(\s*\"(?:OK|Submit|Yes|No)\"",
    },
    {
        "id": "HIG-DUO-004",
        "slug": "designing-for-iphone-duo",
        "heading": "Vertical controls",
        "tier": "static",
        "severity": "medium",
        "platforms": ["ios", "ipados"],
        "swiftui_apis": [".toolbar", "ToolbarItemGroup", "Spacer"],
        "uikit_apis": ["UIBarButtonItemGroup"],
        "appkit_apis": [],
        "rule": "Group toolbar items with `ToolbarItemGroup` instead of inserting manual `Spacer()` views in `.toolbar`.",
        "anti_pattern": "Spacer() placed inside .toolbar { ... } or ToolbarItemGroup { ... }",
        "fix_swiftui": "Group related actions with `ToolbarItemGroup` and let the system manage spacing across horizontal and iPhone Duo vertical bars.",
        "fix_uikit": "Use `UIBarButtonItemGroup` instead of manual `.fixedSpace` spacers.",
        "quote": "Group related toolbar items instead of spacing them manually.",
        "pattern": None,
        "structural": "toolbar_spacer",
    },
    {
        "id": "HIG-LG-005",
        "slug": "liquid-glass",
        "heading": "Where the material belongs",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados", "macos"],
        "swiftui_apis": [".glassEffect", "List", "ForEach"],
        "uikit_apis": ["UIGlassEffect", "UITableViewCell"],
        "appkit_apis": ["NSGlassEffectView"],
        "rule": "Never apply `.glassEffect` to rows, cards, or items inside `List`, `ForEach`, or `LazyVStack` (content layer).",
        "anti_pattern": ".glassEffect(...) called inside List { ... } or ForEach { ... }",
        "fix_swiftui": "Remove `.glassEffect(...)` from list rows/cards; use standard materials or opaque grouped fills in the content layer.",
        "fix_uikit": "Remove `UIGlassEffect` from table/collection cells; reserve it for floating bars and controls.",
        "quote": "Don't use Liquid Glass in the content layer.",
        "pattern": None,
        "structural": "glass_in_list",
    },
    {
        "id": "HIG-TAB-001",
        "slug": "tab-bars",
        "heading": "Best practices",
        "tier": "static",
        "severity": "high",
        "platforms": ["ios", "ipados"],
        "swiftui_apis": ["TabView", "Tab", ".tabItem"],
        "uikit_apis": ["UITabBarController"],
        "appkit_apis": [],
        "rule": "Keep top-level tab bars to 5 or fewer tabs to avoid a hidden 'More' overflow tab on compact screens.",
        "anti_pattern": "TabView containing 6+ tabs without sidebar adaptation",
        "fix_swiftui": "Reduce top-level tabs to <= 5, or adopt `.tabViewStyle(.sidebarAdaptable)` / `NavigationSplitView` for deeper hierarchies.",
        "fix_uikit": "Keep `UITabBarController.viewControllers` to <= 5 items or use `UISplitViewController`.",
        "quote": "Avoid overflow tabs.",
        "pattern": None,
        "structural": "too_many_tabs",
    },
    {
        "id": "HIG-SHEET-001",
        "slug": "sheets",
        "heading": "Mobile (iOS, iPadOS)",
        "tier": "static",
        "severity": "medium",
        "platforms": ["ios", "ipados"],
        "swiftui_apis": [".sheet", ".presentationDetents", ".presentationDragIndicator"],
        "uikit_apis": ["UISheetPresentationController"],
        "appkit_apis": [],
        "rule": "Include a visible grabber on resizable sheets with multiple detents (aids both discoverability and VoiceOver).",
        "anti_pattern": ".presentationDetents([.medium, .large]) combined with .presentationDragIndicator(.hidden)",
        "fix_swiftui": "Set `.presentationDragIndicator(.visible)` (or omit `.hidden`) on multi-detent sheets.",
        "fix_uikit": "Set `sheetPresentationController.prefersGrabberVisible = true` when multiple detents are configured.",
        "quote": "Include a grabber in a resizable sheet.",
        "pattern": None,
        "structural": "sheet_hidden_grabber",
    },
]


def github_slug(text: str) -> str:
    """GitHub heading-to-anchor slug (matches scripts/pull-hig.mjs)."""
    cleaned = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", "-", cleaned.strip())


def normalize_text(text: str) -> str:
    """Normalize whitespace, markdown links/emphasis, dashes, and smart quotes for quote verification."""
    # Strip markdown links [label](url) -> label
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Strip markdown emphasis/code delimiters
    s = re.sub(r"[*_`]", "", s)
    # Normalize curly quotes, dashes, and non-breaking spaces
    s = (
        s.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("--", "-")
        .replace("\u00a0", " ")
    )
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_quote_in_source(quote: str, source_text: str) -> bool:
    """Return True iff quote (or all ellipsis-separated segments) is a verified substring of source_text."""
    if not quote or len(quote.strip()) < 10:
        return False
    norm_src = normalize_text(source_text)
    norm_q = normalize_text(quote)
    if norm_q in norm_src:
        return True
    # Allow ellipsis-joined verbatim excerpts if every substantial segment is verbatim in source
    segments = [seg.strip(" .") for seg in re.split(r"\.\.\.|…", norm_q) if len(seg.strip(" .")) >= 15]
    if len(segments) >= 2 and all(seg in norm_src for seg in segments):
        return True
    # Fallback: if the LLM combined 2 verbatim sentences from the same paragraph, verify each sentence
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", norm_q) if len(s.strip()) >= 15]
    if len(sentences) >= 2 and all(s in norm_src for s in sentences):
        return True
    return False


def estimate_tokens(text: str) -> int:
    """Fast ~4 chars/token estimate for budget enforcement."""
    return max(1, (len(text) + 3) // 4)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_hig_markdown(path: Path) -> dict[str, Any]:
    """Parse a pulled or curated references/hig/<slug>.md file into structured metadata."""
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    slug = path.stem

    title = slug.replace("-", " ").title()
    source_url = ""
    section = "Curated"
    platforms_raw = ""
    last_changed = ""
    is_curated = False
    abstract_lines: list[str] = []

    idx = 0
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        idx = 1

    # Read blockquote header lines
    while idx < len(lines) and (lines[idx].strip() == "" or lines[idx].startswith(">")):
        line = lines[idx].strip()
        if line.startswith("> Curated guide"):
            is_curated = True
        elif line.startswith("> Source:"):
            m = re.search(r"<([^>]+)>", line)
            if m:
                source_url = m.group(1)
        elif line.startswith("> Section:"):
            section = line.split(":", 1)[1].strip()
        elif line.startswith("> Platforms covered:"):
            platforms_raw = line.split(":", 1)[1].strip()
        elif line.startswith("> Last change on Apple's site:"):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
            if m:
                last_changed = m.group(1)
        idx += 1

    # Read abstract until first --- or ##
    while idx < len(lines):
        line = lines[idx].strip()
        if line == "---" or line.startswith("## "):
            break
        if line:
            abstract_lines.append(line)
        idx += 1
    abstract = " ".join(abstract_lines)

    # Infer normalized platform list
    platforms: list[str] = []
    pl_lower = platforms_raw.lower()
    if "all apple platforms" in pl_lower or not platforms_raw:
        platforms = ["ios", "ipados", "macos"]
    else:
        # Only inspect the part before "(guidance specific to ... omitted)"
        kept_part = pl_lower.split("(guidance", 1)[0]
        if "ios" in kept_part:
            platforms.append("ios")
        if "ipados" in kept_part:
            platforms.append("ipados")
        if "macos" in kept_part:
            platforms.append("macos")
        if not platforms:
            platforms = ["ios", "ipados", "macos"]

    # Walk headings, links, Apple doc links, and bold lead-in rules
    headings: list[dict[str, Any]] = []
    inline_links: set[str] = set()
    related_links: set[str] = set()
    apple_docs: dict[str, set[str]] = {}
    bold_rules: list[dict[str, Any]] = []

    in_fence = False
    current_h2 = "Overview"
    current_h3 = ""
    current_heading = "Overview"
    in_related = False
    in_changelog = False
    in_contents = False
    in_specs = False

    for lineno, line in enumerate(lines, start=1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        hm = re.match(r"^(#{2,4})\s+(.+)$", line)
        if hm:
            level = len(hm.group(1))
            htext = hm.group(2).strip()
            anchor = github_slug(htext)
            headings.append({"level": level, "text": htext, "anchor": anchor, "line": lineno})
            if level == 2:
                current_h2 = htext
                current_h3 = ""
                current_heading = htext
                in_related = htext.lower() == "related guidelines"
                in_changelog = htext.lower() == "change log"
                in_contents = htext.lower() == "contents"
                in_specs = htext.lower() == "specifications"
            elif level == 3:
                current_h3 = htext
                current_heading = htext
            else:
                current_heading = htext
            continue

        if in_contents or in_changelog:
            continue

        # Cross-file HIG links
        for lm in re.finditer(r"\[[^\]]+\]\(([a-z0-9-]+)\.md(?:#[^)]+)?\)", line):
            target = lm.group(1)
            if target != slug:
                if in_related:
                    related_links.add(target)
                else:
                    inline_links.add(target)

        # Apple Developer Documentation links -> extract framework + symbol name
        for dm in re.finditer(
            r"\[([^\]]+)\]\(https://developer\.apple\.com/documentation/([^/)]+)(/[^)]*)?\)",
            line,
        ):
            fw = dm.group(2).lower()
            label = dm.group(1).strip().strip("`")
            subpath = (dm.group(3) or "").strip("/")
            apple_docs.setdefault(fw, set()).add(label)
            # Also capture camelCase / PascalCase trailing path segment if informative
            if subpath:
                last_seg = subpath.split("/")[-1].split("#")[0]
                if last_seg and len(last_seg) > 2:
                    apple_docs[fw].add(label)

        # Bold lead-in imperative rules (Apple's standard HIG rule format)
        if not in_related and not in_specs:
            bm = re.match(r"^(?:-\s+)?\*\*([^*\n]{12,220}[.!?])\*\*(?:\s+(.*))?$", line.strip())
            if bm:
                lead = bm.group(1).strip()
                rest = (bm.group(2) or "").strip()
                # Skip glossary/anatomy definitions that aren't actionable rules
                if not re.match(r"^(?:San Francisco|New York|Note:|Important:|Tip:)", lead):
                    scope_platforms = _infer_heading_platforms(current_h2, current_h3, platforms)
                    bold_rules.append(
                        {
                            "heading": current_heading,
                            "h2": current_h2,
                            "anchor": github_slug(current_heading),
                            "line": lineno,
                            "lead": lead,
                            "detail": rest[:280],
                            "platforms": scope_platforms,
                        }
                    )

    return {
        "slug": slug,
        "title": title,
        "file": f"references/hig/{path.name}",
        "source_url": source_url,
        "section": section,
        "platforms": platforms,
        "platforms_raw": platforms_raw,
        "last_changed": last_changed,
        "is_curated": is_curated,
        "abstract": abstract,
        "sha256": file_sha256(path),
        "line_count": len(lines),
        "bytes": len(raw.encode("utf-8")),
        "est_tokens": estimate_tokens(raw),
        "headings": headings,
        "inline_links": sorted(inline_links),
        "related_links": sorted(related_links),
        "apple_docs": {k: sorted(v) for k, v in sorted(apple_docs.items())},
        "bold_rules": bold_rules,
    }


def _infer_heading_platforms(h2: str, h3: str, page_platforms: list[str]) -> list[str]:
    combo = f"{h2} {h3}".lower()
    if "phone (ios)" in combo:
        return ["ios"]
    if "tablet (ipados)" in combo:
        return ["ipados"]
    if "mobile (ios, ipados)" in combo:
        return ["ios", "ipados"]
    if "desktop (macos)" in combo:
        return ["macos"]
    if "tablet and desktop" in combo:
        return ["ipados", "macos"]
    return list(page_platforms)
