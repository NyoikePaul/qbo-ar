import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    assert m, "no version in pyproject.toml"
    return m.group(1)


def test_changelog_top_entry_matches_pyproject():
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert m, "no release heading in CHANGELOG.md"
    assert m.group(1) == _pyproject_version()


def test_package_version_matches_pyproject_if_declared():
    import qbo_ar

    declared = getattr(qbo_ar, "__version__", None)
    if declared is not None:
        assert declared == _pyproject_version()
