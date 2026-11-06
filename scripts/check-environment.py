#!/usr/bin/env python3
"""Report build prerequisites without changing the host."""
import json
from pathlib import Path
import platform
import shutil
import sys

TOOLS = ("git", "make", "cc", "tar", "xz", "zstd", "dpkg-buildpackage")


def inspect_environment(root, headers_root, which=shutil.which,
                        python_version=sys.version_info, kernel=None):
    report = {
        "kernel": kernel if kernel is not None else platform.release(),
        "python": ".".join(map(str, python_version[:3])),
        "python_314_or_newer": python_version >= (3, 14),
        "tools": {name: which(name) for name in TOOLS},
        "kernel_headers": sorted(str(path) for path in
                                 list(headers_root.glob("linux-headers-*/Makefile")) +
                                 list((root / "build/debian-root/usr/src").glob(
                                     "linux-headers-*/Makefile"))),
        "upstream_present": (root / "vendor/gpu-unlock/Makefile").is_file(),
        "hardware_validation": "requires native USB Linux boot",
        "module_build_validation": "check recorded KDIR, compiler, ABI and build logs separately",
    }
    report["missing"] = [name for name, path in report["tools"].items() if not path]
    if not report["python_314_or_newer"]:
        report["missing"].append("python>=3.14 (upstream documented requirement)")
    if not report["kernel_headers"]:
        report["missing"].append("target kernel headers")
    if not report["upstream_present"]:
        report["missing"].append("upstream checkout")
    report["build_prerequisites_present"] = not report["missing"]
    return report


def main():
    report = inspect_environment(Path(__file__).resolve().parents[1], Path("/usr/src"))
    print(json.dumps(report, indent=2))
    return 0 if report["build_prerequisites_present"] else 1


if __name__ == "__main__":
    sys.exit(main())
