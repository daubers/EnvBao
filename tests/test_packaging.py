import gzip
import importlib.util
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_deb", ROOT / "scripts" / "packaging" / "build_deb.py"
)
assert _spec and _spec.loader
bd = importlib.util.module_from_spec(_spec)
sys.modules["build_deb"] = bd
_spec.loader.exec_module(bd)


@pytest.mark.parametrize(
    "semver, deb",
    [
        ("1.2.3", "1.2.3"),
        ("0.2.0-rc.1", "0.2.0~rc.1"),
        ("1.0.0-alpha+001", "1.0.0~alpha"),
        ("1.0.0+20130313", "1.0.0"),
    ],
)
def test_deb_version(semver: str, deb: str) -> None:
    assert bd.deb_version(semver) == deb


@pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "", "1.2.3-"])
def test_deb_version_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        bd.deb_version(bad)


def test_control_text() -> None:
    text = bd.control_text("1.2.3", "arm64", 42)
    assert "Package: envbao\nVersion: 1.2.3\n" in text
    assert "Architecture: arm64\n" in text and "Installed-Size: 42\n" in text
    assert text.endswith("\n") and "Depends: libc6" in text
    with pytest.raises(ValueError):
        bd.control_text("1.2.3", "armhf", 1)


@pytest.fixture
def fake_dist(tmp_path: Path) -> Path:
    app = tmp_path / "dist" / "envbao"
    (app / "_internal" / "lib").mkdir(parents=True)
    (app / "envbao").write_bytes(b"\x7fELFfake")
    (app / "envbao").chmod(0o755)
    (app / "_internal" / "lib" / "data.bin").write_bytes(b"x" * 2048)
    (app / "_internal" / "link").symlink_to("lib/data.bin")
    return tmp_path / "dist"


def read_ar(path: Path) -> dict[str, bytes]:
    raw = path.read_bytes()
    assert raw.startswith(b"!<arch>\n")
    members, pos = {}, 8
    while pos < len(raw):
        name = raw[pos : pos + 16].decode().strip()
        size = int(raw[pos + 48 : pos + 58])
        assert raw[pos + 58 : pos + 60] == b"`\n"
        members[name] = raw[pos + 60 : pos + 60 + size]
        pos += 60 + size + size % 2
    return members


def test_build_deb_structure(fake_dist: Path, tmp_path: Path) -> None:
    out = bd.build("0.2.0-rc.1", "amd64", fake_dist, tmp_path / "out")
    assert out.name == "envbao_0.2.0~rc.1_amd64.deb"
    members = read_ar(out)
    assert list(members) == ["debian-binary", "control.tar.gz", "data.tar.gz"]
    assert members["debian-binary"] == b"2.0\n"

    control = tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]))  # noqa: SIM115
    assert b"Version: 0.2.0~rc.1" in control.extractfile("./control").read()  # type: ignore[union-attr]
    md5s = control.extractfile("./md5sums").read().decode()  # type: ignore[union-attr]
    assert "opt/envbao/envbao" in md5s and "usr/share/doc/envbao/copyright" in md5s

    data = tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"]))  # noqa: SIM115
    by_name = {m.name: m for m in data.getmembers()}
    assert (
        by_name["./usr/bin/envbao"].issym()
        and by_name["./usr/bin/envbao"].linkname == "/opt/envbao/envbao"
    )
    assert by_name["./opt/envbao/envbao"].mode == 0o755
    assert by_name["./opt/envbao/_internal/lib/data.bin"].mode == 0o644
    assert by_name["./opt/envbao/_internal/link"].issym()
    assert all(m.uid == 0 and m.gid == 0 and m.uname == "root" for m in by_name.values())
    for d in ("./opt", "./opt/envbao", "./usr", "./usr/bin"):
        assert by_name[d].isdir()
    assert gzip.decompress(members["data.tar.gz"])  # valid gzip


def test_build_deb_missing_dist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        bd.build("1.0.0", "amd64", tmp_path, tmp_path / "o")


def test_main_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert bd.main(["x"]) == 2
    assert bd.main(["x", "bad", "amd64", str(tmp_path), str(tmp_path)]) == 1
    assert "error" in capsys.readouterr().err


def test_main_success(fake_dist: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert bd.main(["x", "1.0.0", "arm64", str(fake_dist), str(tmp_path / "o")]) == 0
    assert capsys.readouterr().out.strip().endswith("envbao_1.0.0_arm64.deb")


@pytest.mark.skipif(not shutil.which("dpkg-deb"), reason="dpkg-deb not installed")
def test_dpkg_accepts_package(fake_dist: Path, tmp_path: Path) -> None:
    out = bd.build("1.0.0", "amd64", fake_dist, tmp_path / "o")
    info = subprocess.run(["dpkg-deb", "-I", str(out)], capture_output=True, text=True, check=True)
    assert "Package: envbao" in info.stdout


@pytest.mark.skipif(not shutil.which("pkgbuild"), reason="pkgbuild (macOS) not available")
def test_macos_pkg(fake_dist: Path, tmp_path: Path) -> None:
    script = ROOT / "scripts" / "packaging" / "build_pkg.sh"
    proc = subprocess.run(
        ["/bin/sh", str(script), "1.2.3", str(fake_dist), str(tmp_path / "o")],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    pkg = Path(proc.stdout.strip())
    assert pkg.name == "envbao-1.2.3-macos-arm64.pkg" and pkg.exists()
    files = subprocess.run(
        ["pkgutil", "--payload-files", str(pkg)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "./usr/local/lib/envbao/envbao" in files and "./usr/local/bin/envbao" in files


def test_pkg_script_usage_errors(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "packaging" / "build_pkg.sh"
    assert (
        subprocess.run(["/bin/sh", str(script)], capture_output=True, check=False).returncode == 2
    )
    bad = subprocess.run(
        ["/bin/sh", str(script), "1.0.0", str(tmp_path), str(tmp_path / "o")],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert bad.returncode == 1 and "not found" in bad.stderr


# ---- scripts/install.sh ---------------------------------------------------------------


def make_tarball(tmp_path: Path) -> Path:
    app = tmp_path / "src" / "envbao"
    app.mkdir(parents=True)
    exe = app / "envbao"
    exe.write_text("#!/bin/sh\necho envbao 9.9.9\n")
    exe.chmod(0o755)
    tarball = tmp_path / "envbao-9.9.9-linux-amd64.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(app, arcname="envbao")
    return tarball


def install(tarball: Path, prefix: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(ROOT / "scripts" / "install.sh"), str(tarball), str(prefix)],
        capture_output=True,
        text=True,
        check=False,
    )


def sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_install_script_with_checksum(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)
    (tmp_path / "SHA256SUMS").write_text(f"{sha(tarball)}  {tarball.name}\n")
    prefix = tmp_path / "prefix"
    proc = install(tarball, prefix)
    assert proc.returncode == 0, proc.stderr
    assert "checksum OK" in proc.stdout and "envbao 9.9.9" in proc.stdout
    assert (prefix / "bin" / "envbao").is_symlink()
    assert (prefix / "lib" / "envbao" / "envbao").exists()
    assert install(tarball, prefix).returncode == 0  # re-install over the top


def test_install_script_rejects_bad_checksum(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)
    (tmp_path / "SHA256SUMS").write_text(f"{'0' * 64}  {tarball.name}\n")
    proc = install(tarball, tmp_path / "prefix")
    assert proc.returncode == 1 and "mismatch" in proc.stderr
    assert not (tmp_path / "prefix" / "bin" / "envbao").exists()


def test_install_script_unlisted_and_missing(tmp_path: Path) -> None:
    tarball = make_tarball(tmp_path)
    (tmp_path / "SHA256SUMS").write_text(f"{sha(tarball)}  other.tar.gz\n")
    assert "not listed" in install(tarball, tmp_path / "p").stderr
    assert install(tmp_path / "nope.tar.gz", tmp_path / "p").returncode == 1


def test_install_script_without_sums_warns(tmp_path: Path) -> None:
    proc = install(make_tarball(tmp_path), tmp_path / "prefix")
    assert proc.returncode == 0 and "skipping verification" in proc.stderr
