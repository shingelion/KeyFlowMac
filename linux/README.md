# KeyFlow for Linux

The Linux counterpart of KeyFlowMac: type a short trigger like `;addr` in any app and it is
replaced by the full snippet. Works on **X11 and Wayland** (GNOME, KDE, Sway, Hyprland, …) because it
sits below the display server, on the kernel input layer.

This is a port of the platform-agnostic core of KeyFlowMac (matching engine, case handling,
`{date}` / `{time}` / `{cursor}` placeholders, settings, JSON/CSV import & export) to Python, with
the macOS-specific parts (CGEventTap, Accessibility, SwiftUI, SwiftData) replaced:

| macOS | Linux |
|---|---|
| `CGEventTap` keystroke monitor | reads `/dev/input/event*` with `python-evdev` (nothing is grabbed or delayed) |
| `keyboardSetUnicodeString` typing | virtual keyboard on `/dev/uinput`, typed with your own layout via `libxkbcommon` |
| Accessibility permission | membership of the `input` group |
| SwiftData + UserDefaults | `~/.config/keyflow/shortcuts.json` + `settings.json` |
| SwiftUI app / menu bar | GTK 4 window (`keyflow gui`) + `keyflow` command + systemd user service |

## Install on Arch Linux (Fedora: see below)

```bash
git clone https://github.com/shingelion/KeyFlowMac.git
cd KeyFlowMac/linux/packaging/arch
makepkg -si
```

This installs the `keyflow` command, a systemd **user** unit, a udev rule that lets the `input`
group open `/dev/uinput`, and a `modules-load.d` entry for `uinput`. Then, once:

```bash
sudo usermod -aG input $USER                    # then log out and back in
sudo modprobe uinput
sudo udevadm control --reload && sudo udevadm trigger /dev/uinput
systemctl --user enable --now keyflow
keyflow doctor                                  # verifies all of the above
```

Dependencies (all in the official repos): `python`, `python-evdev`, `python-gobject`, `gtk4`, `libxkbcommon`. Optional:
`wl-clipboard` / `xclip` (see *Characters that are not on your layout*), `xorg-setxkbmap`.

To run from a checkout without installing: `python -m keyflow <command>` inside `linux/`.

## Install on Fedora

```bash
sudo dnf install rpm-build python3-devel pyproject-rpm-macros systemd-rpm-macros \
    desktop-file-utils libxkbcommon xkeyboard-config python3-evdev python3-gobject gtk4
git clone https://github.com/shingelion/KeyFlowMac.git
cd KeyFlowMac
mkdir -p ~/rpmbuild/SOURCES
git archive --prefix=keyflow-1.1.1/ -o ~/rpmbuild/SOURCES/keyflow-1.1.1.tar.gz HEAD:linux
rpmbuild -ba linux/packaging/fedora/keyflow.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/keyflow-1.1.1-1*.noarch.rpm
```

Then the same one-time setup as on Arch (`input` group, `modprobe uinput`, udev trigger,
`systemctl --user enable --now keyflow`, `keyflow doctor`). `wl-clipboard` and `xclip` are
installed as weak dependencies for the clipboard fallback. Fedora's default GNOME/Wayland session
is supported; the layout is read from GNOME settings.

## Install on Debian / Ubuntu

Tested by building, linting (`lintian`) and installing/removing the package on Ubuntu 24.04; not
yet tried on a real keyboard there. The package is architecture-independent.

```bash
sudo apt install build-essential debhelper dh-python desktop-file-utils python3-evdev python3-gi gir1.2-gtk-4.0 libxkbcommon0 xkb-data git
git clone -b claude/sleepy-lamport-i2ud8e https://github.com/shingelion/KeyFlowMac.git
cd KeyFlowMac/linux
dpkg-buildpackage -us -uc -b          # runs the tests; writes ../keyflow_1.1.1_all.deb
sudo apt install ../keyflow_1.1.1_all.deb
```

Then, once:

```bash
sudo usermod -aG input $USER          # then log out and back in
systemctl --user enable --now keyflow # the package does not enable the service for you
keyflow doctor
```

The package loads `uinput`, installs the udev rule and the systemd user unit. The service is
deliberately **not** enabled automatically: reading keystrokes should be an explicit opt-in per
user. Remove with `sudo apt remove keyflow` (your shortcuts in `~/.config/keyflow` are kept).

## Usage

```bash
keyflow add ';addr' '123 Main St, Springfield'
keyflow add ';sig' - <<'EOF2'          # '-' reads the expansion from stdin (multi-line)
Best regards,
Erwan
EOF2
keyflow add ';dear' 'Dear {cursor},' --folder Work

keyflow list                      # 'x' marks disabled shortcuts
keyflow try 'hi ;addr '           # dry run: shows what would be typed, types nothing
keyflow disable ';sig' / enable / remove
keyflow pause / resume            # stop expanding without stopping the service

keyflow import shortcuts.csv      # or .json; --overwrite, --skipped-out skipped.csv
keyflow export -o backup.json     # same schema as the macOS and Android apps
```

Changes are picked up by the running service within about half a second. The files are plain JSON
and can be edited by hand.

### Settings

`keyflow settings` lists them; `keyflow settings KEY VALUE` changes one. The first five are
identical to the macOS app:

| Key | Default | Meaning |
|---|---|---|
| `serviceEnabled` | `true` | master switch (`keyflow pause` / `resume`) |
| `caseSensitive` | `false` | match triggers case-sensitively |
| `preserveCase` | `true` | `;Addr` → capitalised expansion, `;ADDR` → upper case |
| `expansionKey` | `boundaryKey` | `boundaryKey`: expand after space/punctuation; `immediate`: as soon as the trigger is typed |
| `triggerPrefix` | `""` | if set, triggers must be preceded by this character |
| `keyboard.layout` / `.variant` / `.model` / `.options` | auto | XKB layout override, e.g. `fr`, `oss` |
| `inputDevices` | `[]` | only watch keyboards whose name/path contains one of these (comma separated) |
| `injectDelayMs` | `2` | pause between synthetic key events; raise it if an app drops characters |

Placeholders are the same as on macOS: `{date}`, `{date:yyyy-MM-dd}`, `{time}`, `{time:HH:mm}`
(ICU/`DateFormatter` patterns, month and day names follow your locale) and `{cursor}`.

## How it works

1. `keyflow daemon` opens every keyboard under `/dev/input` (read-only, no grab) and keeps a small
   in-memory buffer of what you typed, translated to characters through your XKB layout.
2. The buffer is cleared when it can no longer be trusted: arrows/Enter/Tab/Esc/function keys,
   Ctrl/Alt/Super, a mouse click, unplugging a keyboard, or 30 s without typing.
3. On every character it is matched against your shortcuts by the same `ExpansionEngine` logic as
   macOS and Android.
4. On a match it waits until you have released all keys (so a held Shift does not capitalise the
   output), erases the trigger with Backspace, types the expansion through a virtual keyboard, and
   re-types the space/punctuation you ended the trigger with.

Nothing is ever logged or transmitted: there is no networking code, and the log contains device
names and counts only, never typed text.

## The KeyFlow window (GUI)

Open **KeyFlow** from your application menu, or run `keyflow gui`. It mirrors the macOS app:

* **Shortcuts** tab: search, folder chips (with their colours), group chips, an on/off switch per
  shortcut, click a row to edit it, right-click for Edit/Delete. Long-press a row, or use the
  ✓ button, for multi-select: select several, then **move to a folder** or **delete**.
* **+** adds a shortcut (trigger, expansion with `{date}`/`{time}`/`{cursor}`, group, folder,
  enabled); the folder button manages folders: create, rename, recolour, delete (keep or delete
  their shortcuts).
* **Settings** tab: pause/resume, case sensitivity, preserve typed case, expand on
  space-or-punctuation vs. immediately, required prefix, keyboard layout, **Syncthing sync**
  (choose folder, Sync Now, last-synced time, errors), and import / export with the skipped-rows
  viewer.
* A banner tells you when KeyFlow can't work yet (not in the `input` group, `/dev/uinput`
  unavailable, service not running) and offers a **Start service** button or the exact command.
* Everything you change is saved immediately and applied by the running service within about
  half a second; changes made by the CLI, by sync, or by another window show up in the window by
  themselves.

**Quick add from anywhere.** The launcher entry has right-click actions *Add Shortcut…* and *Add
Shortcut from Selection* (the counterpart of the Mac's "Add as KeyFlow Shortcut" Services item).
For a keyboard shortcut, bind `keyflow gui --quick-add --from-selection` in your desktop's
settings (GNOME: Settings → Keyboard → Custom Shortcuts; KDE: System Settings → Shortcuts): select
text in any app, press the key, type a trigger, Save.

There is no tray / menu-bar icon: GNOME has none by default and tray support is inconsistent
elsewhere. The window's main menu (☰) has the pause toggle, quick add and *Sync now*.

The window needs GTK 4 and PyGObject (installed with the packages). Without them
`keyflow gui` prints the install command for your distro; everything else keeps working.

## Sync with Syncthing (Mac, Android, other Linux machines)

KeyFlow speaks the same sync protocol as the macOS and Android apps ([SYNC_PROTOCOL.md](../SYNC_PROTOCOL.md)):
one file, `keyflow-sync.json`, in a folder that Syncthing mirrors between your devices. No server,
no account.

```bash
# 1. In Syncthing, share one folder with your other devices - the SAME folder the Mac/Android
#    apps use. (Install it with your package manager: pacman -S syncthing / dnf install syncthing /
#    apt install syncthing.)
# 2. Point KeyFlow at it:
keyflow sync setup ~/Sync/KeyFlow
```

That runs a first sync and turns it on. From then on the running `keyflow daemon` syncs by itself:
about 2 s after you change a shortcut, and within about 5 s of the file changing on disk. Other
commands:

```bash
keyflow sync now       # force a pass
keyflow sync status    # folder, last sync, last error, how many deletions are remembered
keyflow sync off       # stop syncing (shortcuts and the sync file are left alone)
```

What to expect:

* **Nothing is deleted by joining.** On first contact, shortcuts that exist on both sides with the
  same trigger are recognised as the same shortcut and merged, not duplicated. Run
  `keyflow export -o backup.json` first if you want a safety net.
* **Newest edit wins, per shortcut.** Deleting a shortcut is synced as an explicit deletion, never
  inferred from it being absent. Hand-editing `shortcuts.json` works too: edits and removals are
  detected and synced.
* **Edit times:** Linux keeps no per-shortcut timestamps, so an edit made while the daemon was
  off is dated with the modification time of `shortcuts.json`, not with the moment it later syncs.
  That keeps an offline edit from beating a newer edit made on another device.
* **Folders** sync too: creating, renaming, recolouring and deleting a folder (also an empty one)
  on any device shows up on the others, and a shortcut's folder follows it. A folder deleted on
  another device moves its shortcuts to Uncategorized here.
* If the sync file looks half-written (Syncthing still transferring it) it is left alone and
  retried; KeyFlow never writes without first merging what is on disk.
* Extra state lives in `~/.config/keyflow/sync-state.json` (what each shortcut looked like at the
  last sync, plus deletions kept for 30 days). It is safe to delete: it is rebuilt, you only lose
  the memory of recent deletions. `shortcuts.json` now carries a `syncId` per shortcut; exports to
  JSON/CSV do not include it.

## Characters that are not on your layout

Expansions are typed with the keys of *your* layout, so `é`, `€` or `@` work on AZERTY, QWERTZ,
Dvorak, … Accents missing from the layout are composed with its dead keys (`ô` = circumflex +
`o`). Anything else (emoji, `—`, other scripts) cannot be typed and is **pasted through the
clipboard** instead, if `wl-clipboard` (Wayland) or `xclip` (X11) is installed; the previous
clipboard content is restored afterwards. Without one of them those characters are skipped and
a warning is logged. The service needs `WAYLAND_DISPLAY`/`DISPLAY` in its environment for this; if
your compositor does not export them to systemd, run once per session:
`systemctl --user import-environment WAYLAND_DISPLAY DISPLAY`.

Keyboard layout detection uses (in order) `settings.json`, `XKB_DEFAULT_*`, GNOME settings, KDE
(`kxkbrc`), `setxkbmap`, `localectl`. Run `keyflow doctor` to see what it found. Only the **first**
layout of a multi-layout setup is used, and the layout is read at start-up: after changing it,
`systemctl --user restart keyflow`.

## Security

Be aware of what the `input` group means: any process running as a member can read **all** keyboard
input on the machine, not just KeyFlow. That is inherent to every Linux text expander that works
on Wayland (Espanso and KMonad need the same), as there is no sanctioned system-wide keystroke API.
If you do not accept that trade-off, do not install this.

Related limits compared to macOS:

* **No password-field detection.** macOS can ask whether the focused field is secure; at the
  kernel level that information does not exist. KeyFlow only compares the buffer with your own
  triggers and never stores it, but a password that happens to contain one of your triggers followed
  by a space/punctuation would be expanded. Use distinctive triggers (e.g. a `;` prefix) and
  `keyflow pause` when typing secrets in apps where that matters.
* **No focus tracking.** Wayland offers no portable way to ask which window has focus, so the
  buffer is cleared on clicks, shortcuts and idle time instead of on window changes.
* Config files are written `0600` in a `0700` directory, since snippets often contain personal data.

## Not ported (yet)

* A tray / menu-bar icon (see the GUI section) and the macOS Services menu (approximated by the
  quick-add-from-selection action).
* Rich-text expansions (the flag round-trips through JSON but expansions are plain text, as on macOS).

## Development

```bash
cd linux
python -m unittest discover -s tests -t .
```

The tests cover the ported core and importers (cases mirror `Tests/KeyFlowCoreTests`), the
libxkbcommon layer for US/French/German layouts, and the full key-event → expansion → typed-text
path against a simulated text field (including AltGr, dead keys and the cursor placeholder). They
need `libxkbcommon` and its layout data, but no input devices or root.
