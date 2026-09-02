"""The running build's version.

Read from the installed package metadata rather than hardcoded, so it
cannot disagree with `pyproject.toml`. Falls back when the app is run
from a source tree that was never installed.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    VERSION = version("interop-tools")
except PackageNotFoundError:  # running from a source checkout
    VERSION = "unknown"
