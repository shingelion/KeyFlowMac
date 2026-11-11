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
| SwiftUI app / menu bar | `keyflow` command + systemd user service |

## Install on Arch Linux

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

Dependencies (all in the official repos): `python`, `python-evdev`, `libxkbcommon`. Optional:
`wl-clipboard` / `xclip` (see *Characters that are not on your layout*), `xorg-setxkbmap`.

To run from a checkout without installing: `python -m keyflow <command>` inside `linux/`.

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

* The GUI (shortcut list, folder colours, menu bar, "Add as shortcut" Services entry). Folders are
  kept as a plain name on each shortcut (`--folder`, and the `folder` column in JSON/CSV).
* Sync (`SYNC_PROTOCOL.md`).
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
