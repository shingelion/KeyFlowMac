import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from keyflow import keys
from keyflow.core import ExpansionEngine, Shortcut
from keyflow.daemon import Daemon
from keyflow.inject import Injector
from keyflow.processor import KeystrokeProcessor
from keyflow.store import Store
from keyflow.xkb import KeymapNames, XkbKeymap


class FakeDevice:
    def __init__(self, events, name="fake keyboard"):
        self.events = events
        self.name = name

    def read(self):
        events, self.events = self.events, []
        yield from events


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, code, value):
        self.events.append((code, value))

    def sync(self):
        pass


def ev(code, value, type_=1):
    return types.SimpleNamespace(type=type_, code=code, value=value)


class DaemonTests(unittest.TestCase):
    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Daemon(Store(Path(tmp.name)))
        km = XkbKeymap(KeymapNames("us"))
        self.addCleanup(km.close)
        d.engine.update_shortcuts([Shortcut(";a", "X")])
        d._processor = KeystrokeProcessor(d.engine, km.new_state(), d.settings)
        self.rec = Recorder()
        d._injector = Injector(self.rec, km.reverse_map(), delay_seconds=0, sleep=lambda _: None)
        d._keyboards["kbd"] = None
        return d, km

    def test_expansion_waits_for_key_release_then_types(self):
        d, km = self.make()
        rev = km.reverse_map()
        semi, a, space = rev[";"].code, rev["a"].code, keys.KEY_SPACE
        dev = FakeDevice([ev(0, 0, type_=0), ev(semi, 1), ev(semi, 0), ev(a, 1), ev(a, 0), ev(space, 1)])
        d._read("kbd", dev)
        self.assertIsNotNone(d._pending)

        d._flush_pending(now=0.0)               # space still held -> nothing typed yet
        self.assertEqual(self.rec.events, [])
        self.assertIsNotNone(d._pending)

        d._read("kbd", FakeDevice([ev(space, 0)]))
        d._flush_pending(now=0.0)               # released -> backspaces + text + boundary char
        self.assertIsNone(d._pending)
        taps = [c for c, v in self.rec.events if v == 1]
        self.assertEqual(taps[:3], [keys.KEY_BACKSPACE] * 3)     # ";a" + the space
        self.assertEqual(taps[3], rev["X"].code if not rev["X"].shift else keys.KEY_LEFTSHIFT)
        self.assertEqual(taps[-1], space)

    def test_expansion_fires_after_timeout_even_if_key_stuck(self):
        d, km = self.make()
        rev = km.reverse_map()
        d._read("kbd", FakeDevice([ev(rev[";"].code, 1), ev(rev["a"].code, 1), ev(keys.KEY_SPACE, 1)]))
        d._flush_pending(now=d._pending[1] + 0.1)
        self.assertIsNone(d._pending)
        self.assertTrue(self.rec.events)

    def test_pointer_button_clears_buffer_and_pointer_keys_are_not_typing(self):
        d, km = self.make()
        rev = km.reverse_map()
        d._read("kbd", FakeDevice([ev(rev[";"].code, 1), ev(rev["a"].code, 1)]))
        self.assertEqual(d._processor.buffer, ";a")
        d._read("mouse", FakeDevice([ev(keys.BTN_LEFT, 1)]))
        self.assertEqual(d._processor.buffer, "")

    def test_same_key_from_two_devices_is_one_key_press(self):
        """A remapper re-emitting a key through a virtual device must not type it twice."""
        d, km = self.make()
        d._keyboards["kbd2"] = None
        rev = km.reverse_map()
        semi, a = rev[";"].code, rev["a"].code
        t = [100.0]
        with mock.patch("keyflow.daemon.time.monotonic", lambda: t[0]):
            for code in (semi, a):
                d._read("kbd", FakeDevice([ev(code, 1), ev(code, 0)]))
                t[0] += 0.0004
                d._read("kbd2", FakeDevice([ev(code, 1), ev(code, 0)]))   # the forwarded copy
                t[0] += 0.05
        self.assertEqual(d._processor.buffer, ";a")
        self.assertEqual(d.dropped_duplicates, 4)
        # ...but the same key pressed again later, or on one device only, is real
        d._read("kbd", FakeDevice([ev(a, 1), ev(a, 0)]))
        self.assertEqual(d._processor.buffer, ";aa")

    def test_read_error_drops_the_device(self):
        d, _ = self.make()

        class Gone:
            fd = -1
            def read(self):
                raise OSError(19, "No such device")
            def close(self):
                pass

        d._read("kbd", Gone())
        self.assertNotIn("kbd", d._keyboards)


if __name__ == "__main__":
    unittest.main()


class ScanTests(unittest.TestCase):
    def test_unreadable_event_nodes_are_counted_as_denied(self):
        from unittest import mock

        from keyflow.devices import scan_devices

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "event0").touch()
            (Path(d) / "event1").touch()
            (Path(d) / "mouse0").touch()  # not an event node: ignored
            with mock.patch("os.access", return_value=False):
                scan = scan_devices(set(), [], input_dir=d)
        self.assertEqual((scan.denied, scan.keyboards, scan.pointers), (2, {}, {}))

    def test_permission_error_on_open_is_counted_as_denied(self):
        from unittest import mock

        from keyflow.devices import scan_devices

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "event0").touch()
            with mock.patch("evdev.InputDevice", side_effect=PermissionError):
                scan = scan_devices(set(), [], input_dir=d)
        self.assertEqual(scan.denied, 1)
