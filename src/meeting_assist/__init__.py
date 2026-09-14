"""Live meeting transcription and translation in the terminal."""

from importlib.metadata import version

# The version lives in pyproject.toml only; the release workflow checks the
# git tag against it.
__version__ = version("meeting-assist")
