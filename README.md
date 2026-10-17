# KeyFlowMac

The macOS counterpart to the KeyFlow Android app — a system-wide text expander in the spirit of
aText/Espanso. Type a short trigger like `;addr` in any Mac app and KeyFlowMac replaces it with
the full snippet you defined. Built with Swift, SwiftUI, and SwiftData.

This port deliberately **excludes the aText-export importer** (JSON and CSV import/export are
still here) — everything else carries over from the Android app: the matching engine, case
handling, `{date}`/`{time}`/`{cursor}` placeholders, and the settings.

## How it works

macOS has no direct equivalent of Android's `AccessibilityNodeInfo` (which lets you read a text
field's full content and caret position). So `KeystrokeMonitor`
([App/Service/KeystrokeMonitor.swift](App/Service/KeystrokeMonitor.swift)) uses the same
lower-level mechanism aText/Espanso/Raycast use on Mac:

- A `CGEventTap` observes keyDown events system-wide (requires the Accessibility permission —
  the same permission class Android's AccessibilityService needs, just macOS's version of it).
- It maintains its own in-memory buffer of recently typed characters (there's no "read the
  field's text" API to fall back on), clearing it on arrow keys, clicks, or when the frontmost
  app changes — anything that could move the caret out from under it.
- On every character, the buffer is checked against `ExpansionEngine` (from the shared
  `KeyFlowCore` package — the exact same matching/case-adaptation/placeholder logic as Android,
  unit tested the same way).
- On a match, the trigger is deleted with synthetic backspace keystrokes and the expansion is
  typed back in as synthetic Unicode keystrokes (`CGEvent.keyboardSetUnicodeString`) — never
  touching the clipboard. Synthetic events are tagged so the tap's own callback ignores them
  instead of re-processing its own output.
- The system-wide focused UI element's role is checked via the Accessibility API before
  buffering anything, and secure text fields (passwords) are skipped entirely — the closest
  macOS equivalent of Android's `node.isPassword` check.

## Project structure

```
KeyFlowMac/
├── Package.swift              SPM manifest for the testable core logic
├── Sources/KeyFlowCore/       Shared, platform-agnostic logic (no AppKit/SwiftUI/SwiftData)
│   ├── Shortcut.swift              Plain value-type model
│   ├── AppSettings.swift
│   ├── ExpansionEngine.swift       Trigger matching (ported 1:1 from Android)
│   ├── CaseAdapter.swift
│   ├── PlaceholderProcessor.swift  {date}/{time}/{cursor}
│   └── Importer/                   JSON + CSV (no aText importer)
├── Tests/KeyFlowCoreTests/    swift-testing tests — open in Xcode and Cmd+U
├── Sources/SmokeTests/        Dependency-free mirror of the same tests, runnable as
│                               `swift run SmokeTests` on any toolchain (see Verification below)
└── App/                       The actual menu bar app (SwiftUI + AppKit + SwiftData)
    ├── KeyFlowMacApp.swift         @main entry: main window, quick-add window, menu bar extra
    ├── AppState.swift              Wires the stores to the keystroke monitor
    ├── AppDelegate.swift           Registers the Services-menu provider
    ├── Persistence/
    │   ├── ShortcutRecord.swift        SwiftData @Model (maps to/from the plain Shortcut struct)
    │   ├── ShortcutStore.swift         CRUD + the single source of truth both UI and the
    │   │                                keystroke monitor observe
    │   └── SettingsStore.swift         UserDefaults-backed settings
    ├── Service/
    │   ├── KeystrokeMonitor.swift       See "How it works" above
    │   ├── AccessibilityPermission.swift
    │   └── ServicesProvider.swift       Backs "Add as KeyFlowMac Shortcut" in the Services menu
    └── UI/                          SwiftUI screens (list, add/edit, settings, quick-add, menu bar)
```

## Building

**You need full Xcode, not just the Command Line Tools.** Recent Swift/macOS SDKs implement
`@State`, SwiftData's `@Model`/`@Attribute`, and `#Predicate` as compiler macros, and the plugin
binaries that implement them (`SwiftUIMacros`, `SwiftDataMacros`) ship inside Xcode.app, not the
standalone Command Line Tools package.

An `.xcodeproj` is generated from [project.yml](project.yml) via
[XcodeGen](https://github.com/yonaskolb/XcodeGen) (`brew install xcodegen`, then
`xcodegen generate`). Open `KeyFlowMac.xcodeproj` and build/run the `KeyFlowMac` scheme, or just
open the folder directly in Xcode. If you edit `project.yml` (e.g. to change the bundle ID or add
the `NSServices` entry differently), re-run `xcodegen generate` to regenerate the project — it's
gitignored since it's a build artifact, not source.

### Verification note

This was fully built and run, twice, in two different environments:

1. With only Xcode Command Line Tools installed (no full Xcode), the SwiftUI/SwiftData app target
   couldn't be compiled directly — `@State`/`@Model`'s macro plugins aren't in the standalone CLT
   package. What *was* verified there, with the plain `swift` toolchain:
   - `Sources/KeyFlowCore` (all matching, placeholder, and import/export logic) compiles cleanly.
   - `swift run SmokeTests` — a dependency-free mirror of `Tests/KeyFlowCoreTests` for toolchains
     without the `swift-testing` macro plugin — passed all 28 checks, covering the same cases as
     the Android app's `ExpansionEngineTest`, `PlaceholderProcessorTest`, and `ImporterTest`. This
     is what caught a real regex bug during porting (`PlaceholderProcessor`'s `{date}`/`{time}`
     patterns need both braces escaped — `NSRegularExpression` is ICU-backed like Android's regex
     engine and rejects a dangling `}`).
   - `App/Service/*.swift` and `AppDelegate.swift` (the CGEventTap keystroke monitor, Accessibility
     permission handling, and Services-menu provider) were separately type-checked against the
     compiled `KeyFlowCore` module and were clean.
2. After switching to full Xcode (`xcode-select -s /Applications/Xcode.app/Contents/Developer`),
   **the whole thing built successfully**: `swift test` passed all 17 tests via `swift-testing`,
   and `xcodebuild -scheme KeyFlowMac build` succeeded with zero errors. The built `KeyFlowMac.app`
   was launched and confirmed running (no crash, no errors in the unified log) — though full
   end-to-end expansion wasn't exercised, since that requires granting the Accessibility
   permission, which is a step left to you rather than automated.

`./build_app.sh` also works now (direct-swiftc build, no Xcode project needed) if you want a
faster iteration loop than a full Xcode build — it compiles `KeyFlowCore` as a real separate
module first, then links the App layer against it.

## Enabling the Accessibility permission

KeyFlowMac can't expand anything until you grant it Accessibility access — the app shows a
banner with a button that opens System Settings > Privacy & Security > Accessibility for you.

macOS treats this permission seriously because it lets an app read and inject keystrokes
system-wide. Specifically:

- KeyFlowMac only buffers characters to compare against your own shortcut list — it does not
  read screen content, take screenshots, or log anything to disk.
- The focused field's role is checked before buffering, and secure (password) fields are skipped
  entirely.
- Nothing typed, matched or not, is ever transmitted anywhere — there is no networking code in
  this app.
- You can revoke the permission at any time in System Settings, or pause expansion instantly from
  the menu bar without revoking it.

## Adding shortcuts

- **In-app**: the **+** button on the Shortcuts tab.
- **Select text → right-click → Services → "Add as KeyFlowMac Shortcut"**: works in any app;
  pre-fills the expansion field with your selection, mirroring the Android app's "Process text"
  share action.
- **Menu bar → Add Shortcut…**: same quick-add form, one click away from anywhere.

## Placeholders

Identical to the Android app:

| Placeholder | Result |
|---|---|
| `{date}` | Today's date, e.g. `Mar 5, 2026` |
| `{date:yyyy-MM-dd}` | Date with a custom pattern |
| `{time}` | Current time, e.g. `2:30 PM` |
| `{time:HH:mm}` | Time with a custom pattern |
| `{cursor}` | Where the text cursor lands after the expansion is inserted |

## Settings

Same semantics as Android: case sensitivity, "preserve typed case" (auto-capitalize to match what
you typed), expand-on-immediate-match vs. expand-on-boundary-key, and an optional required prefix
character.

## Import & export

Same JSON/CSV schema as the Android app (see its README for the full JSON example), so a JSON or
CSV export from one platform imports cleanly on the other. **No aText importer** — use the JSON or
CSV export from wherever your shortcuts currently live, or add them by hand.
