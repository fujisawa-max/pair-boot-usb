"""The console must never announce removal while USB storage is in use."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "usb_ready", Path(__file__).resolve().parents[1] / "runtime/announce-usb-ready.py")
ready = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ready)


class UsbReadyTests(unittest.TestCase):
    def setUp(self):
        self.state = {"usb_device": "/dev/sde2", "usb_unmounted": True}
        self.layout = {"blockdevices": [{"name": "sde", "mountpoints": [None],
                                         "children": [{"name": "sde1", "mountpoints": [None]},
                                                      {"name": "sde2", "mountpoints": [None]}]}]}

    def command(self, *args):
        if "PKNAME" in args:
            return "sde"
        if "TRAN" in args:
            return "usb"
        import json
        return json.dumps(self.layout)

    def test_unmounted_usb_may_be_announced(self):
        ready.verify(self.state, self.command)

    def test_initramfs_confirmation_is_required(self):
        self.state["usb_unmounted"] = False
        with self.assertRaisesRegex(ValueError, "did not confirm"):
            ready.verify(self.state, self.command)

    def test_any_partition_mount_blocks_announcement(self):
        self.layout["blockdevices"][0]["children"][0]["mountpoints"] = ["/boot/efi"]
        with self.assertRaisesRegex(ValueError, "still mounted"):
            ready.verify(self.state, self.command)

    def test_non_usb_and_incomplete_inventory_block_announcement(self):
        def non_usb(*args):
            return "sde" if "PKNAME" in args else "sata"
        with self.assertRaisesRegex(ValueError, "not identified as USB"):
            ready.verify(self.state, non_usb)
        self.layout["blockdevices"] = []
        with self.assertRaisesRegex(ValueError, "incomplete"):
            ready.verify(self.state, self.command)


if __name__ == "__main__":
    unittest.main()
