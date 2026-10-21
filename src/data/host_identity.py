"""Stable, non-secret host identity for RESON.

A monitored host needs a stable identifier that is:

* **stable** across reboots and application restarts,
* **non-secret** (not a credential, not a secret key),
* **not a sensitive attribute** (never a username, user home path, public IP
  address, or MAC address used as the sole identity),
* **not reversible** into the underlying machine identifier.

The implementation reads the platform's machine identifier when it is
available, hashes it with SHA-256, and truncates the digest. The result is a
hex string that identifies the machine without disclosing the underlying
identifier.

Where no machine identifier is available (some containers and restricted
environments), the collector falls back to the host name, and — as a last
resort — to a process-stable random identifier so that two different hosts
never silently share an identity within one run.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import uuid
from functools import lru_cache

#: Length of the truncated digest used as the stable host identifier.
HOST_ID_LENGTH = 16

#: Prefix applied to the stable host identifier.
HOST_ID_PREFIX = "reson"


def _read_text_file(path: str) -> str | None:
    """Return the stripped contents of ``path``, or ``None`` on any failure."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read().strip()
    except (OSError, UnicodeError):
        return None
    return text or None


def _linux_machine_id() -> str | None:
    """Return the Linux machine identifier (``/etc/machine-id``)."""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        value = _read_text_file(path)
        if value:
            return value
    return None


def _macos_machine_id() -> str | None:
    """Return the macOS hardware UUID via ``ioreg``."""
    try:
        import subprocess

        output = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
    except Exception:
        return None
    match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', output)
    if match:
        return match.group(1)
    return None


def _windows_machine_id() -> str | None:
    """Return the Windows machine GUID from the registry."""
    if not platform.system().lower().startswith("win"):
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value)
    except Exception:
        return None


def machine_fingerprint() -> str | None:
    """Return the raw platform machine identifier, when one is available.

    The value is a machine identifier, never a user identity, credential, or
    network address. It is hashed before use and is never persisted or
    displayed directly.
    """
    system = platform.system().lower()
    if system.startswith("win"):
        return _windows_machine_id()
    if system == "darwin":
        return _macos_machine_id()
    if system == "linux":
        return _linux_machine_id()
    return None


def host_display_name() -> str:
    """Return a human-readable label for the monitored host.

    Used for display only; it is never the identity key.
    """
    name = (platform.node() or "").strip()
    if name:
        return name
    return platform.system() or "unknown-host"


@lru_cache(maxsize=1)
def _stable_host_id() -> str:
    """Compute the stable host identifier once per process."""
    fingerprint = machine_fingerprint()
    if fingerprint:
        digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
        return f"{HOST_ID_PREFIX}-{digest[:HOST_ID_LENGTH]}"

    name = (platform.node() or "").strip()
    if name:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        return f"{HOST_ID_PREFIX}-{digest[:HOST_ID_LENGTH]}"

    # No stable attribute is available: use a process-stable random identifier
    # so that distinct hosts are never conflated within one run.
    return f"{HOST_ID_PREFIX}-{uuid.uuid4().hex[:HOST_ID_LENGTH]}"


def stable_host_id() -> str:
    """Return the stable, non-secret identifier for this host.

    The identifier is stable across application restarts when the platform
    exposes a machine identifier, is not reversible, and never encodes a
    username, user home path, or network address.

    ``RESON_HOST_ID`` overrides the computed value, which lets an operator
    assign an explicit identity to a collector (for example a hosted agent
    reporting on behalf of a named machine).
    """
    override = os.getenv("RESON_HOST_ID")
    if override and override.strip():
        return override.strip()
    return _stable_host_id()


def reset_host_id_cache() -> None:
    """Clear the cached host identifier (used by tests)."""
    _stable_host_id.cache_clear()
    os.environ.pop("RESON_HOST_ID", None)
