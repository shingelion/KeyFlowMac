# Install KeyFlow on Fedora

KeyFlow replaces short triggers like `;addr` with full text in any app (Wayland and X11), and
syncs your shortcuts with the Mac and Android versions through Syncthing. This guide takes about
five minutes. You need a normal Fedora user with `sudo`, and an internet connection for the first
install.

## 1. Get the code

```bash
sudo dnf install -y git
git clone -b claude/sleepy-lamport-i2ud8e https://github.com/shingelion/KeyFlowMac.git
cd KeyFlowMac/linux
```

Already have a copy? Update it instead:

```bash
cd ~/KeyFlowMac
git fetch origin claude/sleepy-lamport-i2ud8e
git checkout claude/sleepy-lamport-i2ud8e
git pull
cd linux
```

> If `git pull` complains about local changes in `linux/debian`, they are leftovers from an old
> build: run `git checkout -- linux/debian` and pull again.

## 2. Run the installer

```bash
./install-fedora.sh
```

It asks for your `sudo` password and takes a minute or two. It will:

1. install what it needs with `dnf` (build tools, GTK 4, Python bindings, libxkbcommon),
2. build the RPM from this folder (running the test-suite as part of the build),
3. install it with `dnf` (so `sudo dnf remove keyflow` removes it cleanly),
4. add you to the `input` group and load the `uinput` module. KeyFlow needs both: one to see your
   keystrokes, the other to type the expansion back,
5. enable the background service for your user.

Run it as your normal user, not with `sudo`. To only build the package without installing:
`./install-fedora.sh --build-only`. To see what it would do first: `./install-fedora.sh --dry-run`.

## 3. Log out and back in

This is not optional. Group membership only applies to a **new login session**, so until you log
out and back in (or reboot), KeyFlow cannot read your keyboard. The service starts by itself at
login.

## 4. Check that everything works

```bash
keyflow doctor
```

You want `[ok]` on every line. A healthy run looks like:

```text
[info] session type: wayland (both X11 and Wayland are supported)
[ok  ] python-evdev
[ok  ] libxkbcommon + keyboard layout: us (from GNOME settings); 97 typeable characters
[ok  ] keyboards readable: AT Translated Set 2 keyboard
[ok  ] /dev/uinput writable
[info] service: enabled
```

If the detected layout is not yours (for example it says `us` but you type on AZERTY), set it:
`keyflow settings keyboard.layout fr` and then `systemctl --user restart keyflow`.

## 5. Add a shortcut and try it

Open **KeyFlow** from the applications menu (or run `keyflow gui`), click **+**, set the trigger to
`;hello` and the expansion to `Hello from KeyFlow!`, then **Save**.

Click into any text field (a text editor, a browser, a terminal) and type `;hello` followed by a
**space**. The text appears. By default KeyFlow expands after you type a space or punctuation; in
**Settings** you can switch to expanding the instant the trigger is typed.

From the command line the same thing is:

```bash
keyflow add ';hello' 'Hello from KeyFlow!'
keyflow try 'hi ;hello '        # dry run: shows what would be typed, types nothing
```

Handy placeholders in an expansion: `{date}`, `{date:yyyy-MM-dd}`, `{time}`, `{time:HH:mm}`, and
`{cursor}` (where the text cursor lands after the expansion).

## 6. Sync with your Mac and Android (Syncthing)

KeyFlow syncs through one file, `keyflow-sync.json`, in a folder that Syncthing mirrors. No server
or account.

```bash
sudo dnf install -y syncthing
systemctl --user enable --now syncthing
```

Open <http://127.0.0.1:8384>, and share the **same folder** your Mac or Android phone uses with this
computer (the Syncthing documentation covers pairing devices). Then, either in KeyFlow
**Settings → Sync → Choose Folder…**, or:

```bash
keyflow sync setup ~/Sync/KeyFlow        # use your Syncthing folder
keyflow sync status
```

Shortcuts you already have on the other devices appear here, and vice versa; nothing is
duplicated or deleted just by connecting. Back up first if you like: `keyflow export -o backup.json`.

## 7. Optional: add a shortcut from selected text

Bind a key to `keyflow gui --quick-add --from-selection` (GNOME: **Settings → Keyboard → View and
Customize Shortcuts → Custom Shortcuts**; KDE: **System Settings → Shortcuts**). Select text in any
app, press the key, type a trigger, Save.

## Updating

```bash
cd ~/KeyFlowMac && git pull
cd linux && ./install-fedora.sh
systemctl --user restart keyflow
```

## Uninstalling

```bash
cd ~/KeyFlowMac/linux && ./install-fedora.sh --uninstall
```

Your shortcuts and settings stay in `~/.config/keyflow` (delete the folder to remove them). To
leave the `input` group as well: `sudo gpasswd -d "$USER" input`.

## Troubleshooting

First, always: `keyflow doctor`, and `journalctl --user -u keyflow -n 30 --no-pager`.

| Symptom | Likely cause and fix |
|---|---|
| `doctor`: `keyboards readable` fails, or "not in the input group" | You have not logged out and in since the installer added you. Log out/in or reboot. Check with `id -nG \| tr ' ' '\n' \| grep -x input`. |
| `doctor`: `/dev/uinput` missing or not writable | `sudo modprobe uinput && sudo udevadm control --reload && sudo udevadm trigger /dev/uinput`, then `ls -l /dev/uinput` should show group `input`, mode `crw-rw----`. |
| Service not running | `systemctl --user enable --now keyflow`. The window's banner also has a **Start service** button. |
| Nothing expands, `doctor` all `[ok]` | Did you type a space after the trigger? Is expansion paused? (`keyflow settings serviceEnabled` should print `True`; `keyflow resume`). Then run `keyflow monitor` (stop the service first: `systemctl --user stop keyflow`) and type a trigger: it shows every key KeyFlow sees and whether it matched. Restart the service afterwards: `systemctl --user start keyflow`. |
| Expansion is garbled / wrong characters | Wrong keyboard layout detected: `keyflow settings keyboard.layout <your layout>` (for example `fr`, `de`, `us`) and restart the service. |
| You use a key remapper (input-remapper, keyd, kmonad) | KeyFlow ignores identical key events that arrive from two devices within 8 ms. If it still misbehaves, restrict it to one keyboard: `keyflow settings inputDevices "Your Keyboard Name"` and restart the service; `keyflow monitor` shows the device names. |
| Emoji or unusual symbols are skipped | They are not on your layout, so KeyFlow pastes them through the clipboard when `wl-clipboard` (Wayland) or `xclip` (X11) is installed: `sudo dnf install wl-clipboard`. |
| Window does not open | `sudo dnf install python3-gobject gtk4`. |
| Still nothing and you suspect SELinux | `sudo ausearch -m avc -ts recent` lists denials. Send the output with your report. |

**Security note.** Membership of the `input` group lets any program you run read *all* keyboard
input on the machine. That is inherent to text expanders on Wayland, and it is why KeyFlow never
stores or logs what you type. KeyFlow cannot tell a password field from any other, so use
distinctive triggers (for example a `;` prefix) and `keyflow pause` when typing secrets where it
matters.
