"""Check the built release for missing source and unintended bundled files."""
import tarfile
import zipfile
from pathlib import Path


def check():
    artifacts = sorted(p for p in Path("dist").glob("*") if p.name != ".gitignore")
    assert artifacts, "No distribution artifacts"
    expected = {p.name for p in Path("src/line_agent").glob("*.py")}
    for path in artifacts:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
            actual = {n.split("/")[-1] for n in names if n.startswith("line_agent/")}
            assert expected <= actual, "Application modules missing from wheel"
            assert all(n.startswith("line_agent/") or ".dist-info/" in n for n in names), (
                "Unexpected bundled package"
            )
            assert any(n.endswith("/licenses/LICENSE") for n in names), "License missing"
        elif path.name.endswith(".tar.gz"):
            with tarfile.open(path) as archive:
                names = archive.getnames()
                assert not any(m.issym() or m.islnk() for m in archive.getmembers())
            assert expected <= {n.split("/")[-1] for n in names if "/src/line_agent/" in n}
        else:
            raise AssertionError("Unexpected release artifact")
        for name in names:
            parts = Path(name).parts
            assert not {".git", ".venv", "__pycache__", "data"}.intersection(parts)
            assert not any(p.startswith(".env") or p.startswith("pairing-") for p in parts)
            assert not name.endswith((".db", ".sqlite", ".sqlite3", ".pem", ".key", ".log"))
        print(f"{path.name}: checked {len(names)} entries")


if __name__ == "__main__":
    check()
