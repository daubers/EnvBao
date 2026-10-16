"""Build a ``.deb`` from the PyInstaller one-dir output, in pure Python.

No dpkg tooling is required, so this works on any runner (and on macOS). Layout:

* ``/opt/envbao/``                 the one-dir application
* ``/usr/bin/envbao``              symlink to ``/opt/envbao/envbao``
* ``/usr/share/doc/envbao/copyright``

Usage: ``python scripts/packaging/build_deb.py VERSION ARCH DIST_DIR OUT_DIR``
(``VERSION`` is a bare SemVer such as ``1.2.3`` or ``1.2.3-rc.1``; ``ARCH`` is ``amd64``
or ``arm64``).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import sys
import tarfile
import time
from pathlib import Path

ARCHES = ("amd64", "arm64")
INSTALL_DIR = "opt/envbao"
MAINTAINER = "Matt Daubney <mattcdaubney@gmail.com>"
HOMEPAGE = "https://git.daubney.dev/matt/EnvBao"
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")


def deb_version(version: str) -> str:
    """Map SemVer to a Debian version: ``1.2.3-rc.1+b`` -> ``1.2.3~rc.1``.

    ``~`` sorts a pre-release *before* the final release, as SemVer requires. Build
    metadata is dropped.
    """
    if not _SEMVER.match(version):
        raise ValueError(f"not a SemVer version: {version!r}")
    core, _, rest = version.partition("-")
    if "+" in version and not rest:
        core = core.split("+", 1)[0]
    pre = rest.split("+", 1)[0]
    return f"{core}~{pre}" if pre else core


def control_text(version: str, arch: str, installed_kib: int) -> str:
    """Contents of ``DEBIAN/control``."""
    if arch not in ARCHES:
        raise ValueError(f"unsupported architecture {arch!r}")
    return (
        "Package: envbao\n"
        f"Version: {deb_version(version)}\n"
        "Section: utils\n"
        "Priority: optional\n"
        f"Architecture: {arch}\n"
        "Depends: libc6 (>= 2.31)\n"
        f"Installed-Size: {installed_kib}\n"
        f"Maintainer: {MAINTAINER}\n"
        f"Homepage: {HOMEPAGE}\n"
        "Description: Inject OpenBao secrets into your shell\n"
        " EnvBao reads queries from ~/.envbao/config.toml and prints shell export\n"
        " statements for secrets stored in an OpenBao server, or runs a command with\n"
        " selected secrets in its environment (envbao exec). It refuses to run unless\n"
        " its configuration is readable by the owner only.\n"
    )


def _ar_member(name: str, data: bytes, mtime: int) -> bytes:
    header = f"{name:<16}{mtime:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
    assert len(header) == 60  # noqa: S101
    return header + data + (b"\n" if len(data) % 2 else b"")


def _tar_gz(entries: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for info, data in entries:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def _info(
    name: str, mtime: int, *, mode: int, kind: bytes, size: int = 0, link: str = ""
) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode, info.mtime, info.type, info.size = mode, mtime, kind, size
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.linkname = link
    return info


def build(version: str, arch: str, dist_dir: Path, out_dir: Path) -> Path:
    """Create the package and return its path."""
    app = dist_dir / "envbao"
    if not (app / "envbao").is_file():
        raise FileNotFoundError(f"{app / 'envbao'} not found; run PyInstaller first")
    mtime = int(os.environ.get("SOURCE_DATE_EPOCH", time.time()))

    data: list[tuple[tarfile.TarInfo, bytes | None]] = []
    md5s: list[str] = []
    size_bytes = 0
    seen_dirs: set[str] = set()

    def add_dir(path: str) -> None:
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d not in seen_dirs:
                seen_dirs.add(d)
                data.append((_info(f"./{d}/", mtime, mode=0o755, kind=tarfile.DIRTYPE), None))

    add_dir(INSTALL_DIR)
    for root, dirs, files in os.walk(app):
        dirs.sort()
        rel_root = Path(root).relative_to(app)
        add_dir(f"{INSTALL_DIR}/{rel_root}" if str(rel_root) != "." else INSTALL_DIR)
        for name in sorted(files):
            src = Path(root) / name
            rel = f"{INSTALL_DIR}/{(rel_root / name).as_posix()}".replace("/./", "/")
            if src.is_symlink():
                data.append(
                    (
                        _info(
                            f"./{rel}",
                            mtime,
                            mode=0o777,
                            kind=tarfile.SYMTYPE,
                            link=os.readlink(src),
                        ),
                        None,
                    )
                )
                continue
            content = src.read_bytes()
            executable = os.access(src, os.X_OK)
            data.append(
                (
                    _info(
                        f"./{rel}",
                        mtime,
                        mode=0o755 if executable else 0o644,
                        kind=tarfile.REGTYPE,
                        size=len(content),
                    ),
                    content,
                )
            )
            md5s.append(f"{hashlib.md5(content, usedforsecurity=False).hexdigest()}  {rel}")
            size_bytes += len(content)

    add_dir("usr/bin")
    data.append(
        (
            _info(
                "./usr/bin/envbao",
                mtime,
                mode=0o777,
                kind=tarfile.SYMTYPE,
                link="/opt/envbao/envbao",
            ),
            None,
        )
    )
    add_dir("usr/share/doc/envbao")
    licence = (Path(__file__).resolve().parents[2] / "LICENSE").read_bytes()
    data.append(
        (
            _info(
                "./usr/share/doc/envbao/copyright",
                mtime,
                mode=0o644,
                kind=tarfile.REGTYPE,
                size=len(licence),
            ),
            licence,
        )
    )
    md5s.append(
        f"{hashlib.md5(licence, usedforsecurity=False).hexdigest()}  usr/share/doc/envbao/copyright"
    )

    control = control_text(version, arch, (size_bytes + 1023) // 1024).encode()
    md5_file = ("\n".join(md5s) + "\n").encode()
    control_tar = _tar_gz(
        [
            (_info("./", mtime, mode=0o755, kind=tarfile.DIRTYPE), None),
            (
                _info("./control", mtime, mode=0o644, kind=tarfile.REGTYPE, size=len(control)),
                control,
            ),
            (
                _info("./md5sums", mtime, mode=0o644, kind=tarfile.REGTYPE, size=len(md5_file)),
                md5_file,
            ),
        ]
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"envbao_{deb_version(version)}_{arch}.deb"
    target.write_bytes(
        b"!<arch>\n"
        + _ar_member("debian-binary", b"2.0\n", mtime)
        + _ar_member("control.tar.gz", control_tar, mtime)
        + _ar_member("data.tar.gz", _tar_gz(data), mtime)
    )
    return target


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        sys.stderr.write(__doc__ or "")
        return 2
    try:
        path = build(argv[1], argv[2], Path(argv[3]), Path(argv[4]))
    except (ValueError, FileNotFoundError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    sys.stdout.write(f"{path}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
