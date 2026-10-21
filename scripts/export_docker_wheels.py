"""Export locked Linux wheels without consulting a package index."""

import re
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
lock = tomllib.loads((ROOT / "uv.lock").read_text())
packages = {package["name"]: package for package in lock["package"]}
names = [
    match.group(1)
    for line in (ROOT / "requirements.txt").read_text().splitlines()
    if (match := re.match(r"^([a-z0-9-]+)==", line))
]


def wheel_for(name: str, arch: str) -> dict:
    wheels = packages[name]["wheels"]
    pure = [
        wheel
        for wheel in wheels
        if urlsplit(wheel["url"]).path.endswith("none-any.whl")
    ]
    if pure:
        return pure[0]
    platform = [
        wheel
        for wheel in wheels
        if "manylinux" in wheel["url"]
        and wheel["url"].endswith(f"_{arch}.whl")
        and (
            "-cp313-cp313-" in wheel["url"]
            or re.search(r"-cp3(?:[6-9]|1[0-3])-abi3-", wheel["url"])
        )
    ]
    if not platform:
        raise ValueError(f"No Python 3.13 Linux {arch} wheel for {name}")
    return platform[0]


for docker_arch, wheel_arch in (("arm64", "aarch64"), ("amd64", "x86_64")):
    lines = [
        "# Generated from uv.lock by scripts/export_docker_wheels.py.",
        "# Direct wheel URLs avoid package-index resolution during Docker builds.",
    ]
    for name in names:
        wheel = wheel_for(name, wheel_arch)
        digest = wheel["hash"].replace(":", "=", 1)
        lines.append(f"{name} @ {wheel['url']}#{digest}")
    target = ROOT / "docker" / f"requirements-{docker_arch}.txt"
    target.parent.mkdir(exist_ok=True)
    target.write_text("\n".join(lines) + "\n")
