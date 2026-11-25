"""The background service: watches the keyboards and types expansions."""

from __future__ import annotations

import logging
import os
import selectors
import signal
import sys
import time
from typing import Any, Optional

from . import keys
from .core import AppSettings, ExpansionEngine
from .inject import Clipboard, Injector, UInputBackend, UInputUnavailable
from .layout import detect_layout
from .processor import Expansion, KeystrokeProcessor
from .store import LinuxConfig, Store
from .sync import SyncRunner
from .xkb import XkbError, XkbKeymap

log = logging.getLogger("keyflow")

#: sysexits.h EX_CONFIG: the unit file uses RestartPreventExitStatus=78 so a missing permission
#: does not turn into a restart loop.
EXIT_CONFIG = 78

#: The same physical key press can arrive from two devices when a remapper (input-remapper, keyd,
#: kmonad, ...) re-emits it through a virtual keyboard. Identical events from *different* devices
#: this close together are one key press.
DUPLICATE_WINDOW = 0.008

SCAN_INTERVAL = 3.0
POLL_INTERVAL = 0.5
#: How long to wait for held keys to be released before expanding anyway.
RELEASE_WAIT = 1.0

PERMISSION_HELP = """\
KeyFlow cannot read your keyboard.
  1. Add yourself to the 'input' group:   sudo usermod -aG input $USER
  2. Log out and back in (or reboot), then:  systemctl --user restart keyflow
Run 'keyflow doctor' for a full check. Note that members of 'input' can read all keyboard input;
see the README for what that means."""

UINPUT_HELP = """\
KeyFlow cannot open /dev/uinput (needed to type expansions).
  - Load the module now:        sudo modprobe uinput
  - Make /dev/uinput usable:    install the udev rule shipped with the package (it grants the
                                'input' group access) and re-trigger udev, or reboot.
Run 'keyflow doctor' for a full check."""


class NoKeyboardAccess(Exception):
    pass


class Daemon:
    def __init__(self, store: Optional[Store] = None, monitor: bool = False) -> None:
        #: Print every key event to the terminal (``keyflow monitor``). Never used by the service.
        self.monitor = monitor
        self._last_event: dict[tuple[int, int], tuple[str, float]] = {}
        self.event_counts: dict[str, int] = {}
        self.dropped_duplicates = 0
        self.store = store or Store()
        self.engine = ExpansionEngine()
        self.settings = AppSettings()
        self.linux = LinuxConfig()
        self._fingerprint: Any = None
        self._running = True
        self._reload_requested = False
        self._sel = selectors.DefaultSelector()
        self._keyboards: dict[str, Any] = {}
        self._pointers: dict[str, Any] = {}
        self._processor: Optional[KeystrokeProcessor] = None
        self._injector: Optional[Injector] = None
        self._backend: Optional[UInputBackend] = None
        self._pending: Optional[tuple[Expansion, float]] = None
        self._sync = SyncRunner(self.store)

    # -- setup ------------------------------------------------------------------------------

    def _build_keyboard(self) -> None:
        names, source = detect_layout(self.linux)
        log.info("keyboard layout: %s (from %s)", names.describe(), source)
        if source.startswith("default"):
            log.warning("could not detect your keyboard layout, assuming 'us'; set "
                        "keyboard.layout in %s if expansions come out wrong", self.store.settings_path)
        keymap = XkbKeymap(names)
        reverse = keymap.reverse_map()

        old_held = self._processor.held if self._processor else set()
        self._processor = KeystrokeProcessor(self.engine, keymap.new_state(), self.settings)
        self._processor.held = old_held

        if self._backend is None:
            self._backend = UInputBackend(Injector.required_key_codes(reverse, keymap.dead_keys()))
            time.sleep(0.3)  # let the compositor notice the new device before the first use
        self._injector = Injector(
            self._backend, reverse, keymap.dead_keys(), delay_seconds=self.linux.inject_delay_ms / 1000.0, clipboard=Clipboard()
        )

    def _reload(self) -> None:
        fingerprint = self.store.fingerprint()
        try:
            shortcuts = self.store.load_shortcuts()
            self.engine.update_shortcuts(shortcuts)
            log.info("loaded %d shortcut(s)", len(shortcuts))
        except (ValueError, OSError) as e:
            log.error("keeping previous shortcuts, could not load: %s", e)
        try:
            self.settings, new_linux = self.store.load_settings()
        except (ValueError, OSError) as e:
            log.error("keeping previous settings, could not load: %s", e)
            new_linux = self.linux
        if self._processor is not None:
            self._processor.settings = self.settings
            keyboard_changed = new_linux.keyboard_key() != self.linux.keyboard_key()
            self.linux = new_linux
            if keyboard_changed:
                try:
                    self._build_keyboard()
                except XkbError as e:
                    log.error("keeping previous keyboard layout: %s", e)
        else:
            self.linux = new_linux
        self._sync.configure(self.linux.sync_enabled, self.linux.sync_folder)
        self._fingerprint = fingerprint

    def _scan(self) -> None:
        from .devices import scan_devices

        found = scan_devices(set(self._keyboards) | set(self._pointers), self.linux.input_devices)
        for table, new in ((self._keyboards, found.keyboards), (self._pointers, found.pointers)):
            for path, dev in new.items():
                table[path] = dev
                self._sel.register(dev.fd, selectors.EVENT_READ, (path, dev))
                log.info("watching %s (%s)", dev.name, path)
        if found.denied and not self._keyboards:
            raise NoKeyboardAccess(PERMISSION_HELP)

    def _drop(self, path: str, dev: Any) -> None:
        log.info("device removed: %s", path)
        try:
            self._sel.unregister(dev.fd)
        except (KeyError, ValueError, OSError):
            pass
        self._keyboards.pop(path, None)
        self._pointers.pop(path, None)
        try:
            dev.close()
        except OSError:
            pass
        if self._processor is not None:
            self._processor.held.clear()
            self._processor.clear()

    # -- main loop --------------------------------------------------------------------------

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "_running", False))
        signal.signal(signal.SIGHUP, lambda *_: setattr(self, "_reload_requested", True))
        try:
            import locale

            locale.setlocale(locale.LC_TIME, "")  # month / day names for {date:MMMM}
        except Exception:
            pass

        try:
            self._reload()
            self._build_keyboard()
            self._scan()
        except NoKeyboardAccess as e:
            print(e, file=sys.stderr)
            return EXIT_CONFIG
        except UInputUnavailable as e:
            print(f"{UINPUT_HELP}\n({e})", file=sys.stderr)
            return EXIT_CONFIG
        except XkbError as e:
            print(f"keyflow: {e}", file=sys.stderr)
            return EXIT_CONFIG

        if not self._keyboards:
            log.warning("no keyboard found yet; waiting for one to appear")
        log.info("ready")

        last_scan = time.monotonic()
        try:
            while self._running:
                timeout = 0.02 if self._pending else POLL_INTERVAL
                for key, _ in self._sel.select(timeout):
                    path, dev = key.data
                    self._read(path, dev)

                now = time.monotonic()
                self._flush_pending(now)
                try:
                    self._sync.tick()
                except Exception:  # sync must never take the expander down
                    log.exception("sync failed unexpectedly")

                if self._reload_requested or self.store.fingerprint() != self._fingerprint:
                    self._reload_requested = False
                    self._reload()
                if now - last_scan >= SCAN_INTERVAL:
                    last_scan = now
                    try:
                        self._scan()
                    except NoKeyboardAccess:
                        pass
        finally:
            self._shutdown()
        return 0

    def _flush_pending(self, now: float) -> None:
        """Type a queued expansion once every key has been released (or the wait ran out).

        Typing while the user still holds a modifier would apply that modifier to our synthetic
        keystrokes (e.g. Shift turning the expansion into capitals).
        """
        if self._pending and (not self._processor.held or now >= self._pending[1]):
            expansion, _ = self._pending
            self._pending = None
            self._injector.perform(expansion)
            self._processor.clear()

    def _is_duplicate(self, path: str, code: int, value: int, now: float) -> bool:
        previous = self._last_event.get((code, value))
        self._last_event[(code, value)] = (path, now)
        return previous is not None and previous[0] != path and 0 <= now - previous[1] < DUPLICATE_WINDOW

    @staticmethod
    def _key_name(code: int) -> str:
        """Human name of a key/button code. evdev returns a str, or a list/tuple for aliased codes."""
        try:
            from evdev import ecodes

            name = ecodes.KEY.get(code) or ecodes.BTN.get(code) or str(code)
            if isinstance(name, (list, tuple)):
                name = name[0] if name else str(code)
            return str(name)
        except Exception:  # a diagnostic must never take the daemon down
            return str(code)

    def _print_event(self, dev: Any, ev: Any, note: str) -> None:
        action = {0: "up", 1: "down", 2: "repeat"}.get(ev.value, str(ev.value))
        buf = self._processor.buffer if self._processor else ""
        name = str(getattr(dev, "name", "?"))[:36]
        print(f"{name:36} {self._key_name(ev.code):<14} {action:<6} buffer={buf!r}{note}", flush=True)

    def _read(self, path: str, dev: Any) -> None:
        try:
            for ev in dev.read():
                if ev.type != 1:  # EV_KEY
                    continue
                self.event_counts[dev.name] = self.event_counts.get(dev.name, 0) + 1
                if self._is_duplicate(path, ev.code, ev.value, time.monotonic()):
                    self.dropped_duplicates += 1
                    if self.monitor:
                        self._print_event(dev, ev, "   (duplicate from another device: ignored)")
                    continue
                note = ""
                if ev.code in keys.POINTER_BUTTONS:
                    if ev.value == 1:
                        self._processor.on_pointer()
                elif path in self._keyboards:
                    expansion = self._processor.on_key(ev.code, ev.value)
                    if expansion is not None and self._pending is None:
                        log.debug("expanding (%d char(s) typed)", expansion.delete_count)
                        self._pending = (expansion, time.monotonic() + RELEASE_WAIT)
                        note = f"   -> MATCH: will erase {expansion.delete_count} char(s) and type {expansion.text!r}"
                if self.monitor:
                    self._print_event(dev, ev, note)
        except BlockingIOError:
            pass
        except OSError:
            self._drop(path, dev)

    def _shutdown(self) -> None:
        for path, dev in list(self._keyboards.items()) + list(self._pointers.items()):
            try:
                dev.close()
            except OSError:
                pass
        if self._backend is not None:
            self._backend.close()
        log.info("stopped")


def monitor_main() -> int:
    """``keyflow monitor``: the daemon, loudly. For diagnosing why expansion does not fire."""
    from . import status

    if status.service_is_active():
        print("The KeyFlow service is running; stop it first so expansions don't happen twice:\n"
              "  systemctl --user stop keyflow\n(start it again afterwards with: systemctl --user start keyflow)",
              file=sys.stderr)
        return 2
    print("Monitoring. Type in any window; every key KeyFlow sees is printed here (including what you type!).\n"
          "Expansions are really performed. Press Ctrl+C to stop.\n", flush=True)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    daemon = Daemon(monitor=True)
    code = daemon.run()
    print("\nKey events seen per device:")
    for name, n in sorted(daemon.event_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6d}  {name}")
    if not daemon.event_counts:
        print("  (none: KeyFlow received no key events from any device)")
    if daemon.dropped_duplicates:
        print(f"Ignored {daemon.dropped_duplicates} duplicate event(s) re-emitted by another device.")
    return code


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("KEYFLOW_DEBUG") else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    return Daemon().run()
