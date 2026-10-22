"""Platform collector selection for RESON.

Resolves the correct :class:`src.data.collector.Collector` implementation for
the current operating system. Each platform module is imported lazily so that
an unsupported-platform import error can never break another operating system
(for example importing the macOS collector on a headless Linux server must not
raise).
"""

from __future__ import annotations

import platform

from src.data.collector import Collector

#: Platform keys understood by :func:`create_collector`.
SUPPORTED_PLATFORMS = ("windows", "linux", "macos")


def normalize_platform(name: str | None = None) -> str:
    """Return the normalized RESON platform key for ``name``.

    ``name`` defaults to the running platform. Raises ``ValueError`` for a
    platform RESON does not support, naming the supported set.
    """
    if name is None:
        system = platform.system().strip().lower()
    else:
        system = name.strip().lower()

    if system.startswith("win") or system == "windows":
        return "windows"
    if system.startswith("linux") or system == "linux":
        return "linux"
    if system == "darwin" or system.startswith("mac") or system == "macos":
        return "macos"

    raise ValueError(
        f"Unsupported platform {name or system!r}. "
        f"Supported platforms: {', '.join(SUPPORTED_PLATFORMS)}."
    )


def create_collector(
    platform_name: str | None = None,
    host_id: str | None = None,
    source: str | None = None,
) -> Collector:
    """Create the collector for the given (or current) platform.

    The platform-specific module is imported only after the platform has been
    resolved, so a missing or broken platform module cannot affect the other
    operating systems.
    """
    key = normalize_platform(platform_name)

    if key == "windows":
        from src.data.windows_collector import (
            SOURCE_WINDOWS_LOCAL,
            WindowsTelemetryCollector,
        )

        return WindowsTelemetryCollector(
            host_id=host_id, source=source or SOURCE_WINDOWS_LOCAL
        )

    if key == "linux":
        from src.data.linux_collector import (
            SOURCE_LINUX_LOCAL,
            LinuxTelemetryCollector,
        )

        return LinuxTelemetryCollector(
            host_id=host_id, source=source or SOURCE_LINUX_LOCAL
        )

    from src.data.macos_collector import (
        SOURCE_MACOS_LOCAL,
        MacOSTelemetryCollector,
    )

    return MacOSTelemetryCollector(
        host_id=host_id, source=source or SOURCE_MACOS_LOCAL
    )


def create_event_collector(
    platform_name: str | None = None,
    host_id: str | None = None,
    source: str | None = None,
    window_minutes: int = 60,
    max_records: int = 200,
    checkpoint_path: str | None = None,
):
    """Create the system-event collector for the given (or current) platform.

    Each platform exposes its own event log — the Windows Event Log, the
    systemd journal, the macOS unified log — and each has its own reader.
    All of them apply the same guarantees (allowlist, bounded window, record
    limit, stable identifiers, checkpoint de-duplication, honest errors).

    The platform-specific module is imported only after the platform has been
    resolved, so a missing or broken platform module cannot affect the other
    operating systems.
    """
    key = normalize_platform(platform_name)

    if key == "windows":
        from src.data.windows_event_log import (
            SOURCE_WINDOWS_LOCAL,
            WindowsEventLogCollector,
        )

        return WindowsEventLogCollector(
            host_id=host_id,
            source=source or SOURCE_WINDOWS_LOCAL,
            window_minutes=window_minutes,
            max_records=max_records,
            checkpoint_path=checkpoint_path,
        )

    if key == "linux":
        from src.data.linux_journal import (
            SOURCE_LINUX_LOCAL,
            LinuxJournalCollector,
        )

        return LinuxJournalCollector(
            host_id=host_id,
            source=source or SOURCE_LINUX_LOCAL,
            window_minutes=window_minutes,
            max_records=max_records,
            checkpoint_path=checkpoint_path,
        )

    from src.data.macos_unified_log import (
        SOURCE_MACOS_LOCAL,
        MacOSUnifiedLogCollector,
    )

    return MacOSUnifiedLogCollector(
        host_id=host_id,
        source=source or SOURCE_MACOS_LOCAL,
        window_minutes=window_minutes,
        max_records=max_records,
        checkpoint_path=checkpoint_path,
    )
