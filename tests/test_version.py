import importlib.metadata
import tomllib
from pathlib import Path

from meeting_assist import __version__


def test_version_matches_installed_metadata():
    assert __version__ == importlib.metadata.version("meeting-assist")


def test_version_is_declared_only_in_pyproject():
    # The release workflow compares the tag against pyproject.toml, so the
    # package must not carry a second copy of the version string.
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    source = Path("src/meeting_assist/__init__.py").read_text()
    assert pyproject["project"]["version"] not in source
