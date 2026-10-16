"""Truthful runtime capability discovery for universal AEGIS autonomy.

The contract is deliberately small and side-effect free.  It only inspects the
current process, optional Python modules, executables on ``PATH`` and (for
Android) the read-only ``adb devices`` command.  It never imports a desktop or
browser implementation while probing, starts a process other than ``adb
devices``, or treats a configured API key as evidence that a capability works.

The result is a snapshot, not a promise that a future action will succeed.  A
capability is ``DEGRADED`` when its optional runtime is absent, ``UNAVAILABLE``
when a platform prerequisite is absent, and ``DISABLED`` when the operator has
disabled it through environment configuration.
"""

from __future__ import annotations

import importlib.util
import os
import platform as platform_module
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence


AVAILABLE = "AVAILABLE"
DEGRADED = "DEGRADED"
UNAVAILABLE = "UNAVAILABLE"
DISABLED = "DISABLED"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})

ModuleFinder = Callable[[str], Any]
ExecutableFinder = Callable[[str], Optional[str]]
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _env_value(env: Mapping[str, str], key: str, default: str) -> str:
    value = env.get(key)
    return default if value is None else str(value).strip()


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = _env_value(env, key, "").lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _module_present(name: str, finder: ModuleFinder) -> bool:
    try:
        return finder(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _find_executable(
    explicit: str,
    candidates: Sequence[str],
    finder: ExecutableFinder,
) -> Optional[str]:
    if explicit:
        expanded = os.path.abspath(os.path.expanduser(explicit))
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        return None
    for candidate in candidates:
        try:
            resolved = finder(candidate)
        except (OSError, ValueError):
            resolved = None
        if resolved:
            return resolved
    return None


def _default_command_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )


@dataclass(frozen=True)
class AutonomyRuntimeConfig:
    """Environment-backed switches used by capability discovery."""

    desktop_enabled: bool = True
    browser_enabled: bool = True
    voice_enabled: bool = True
    android_enabled: bool = True
    confirmation_required: bool = True
    full_access: bool = False
    adb_path: str = ""
    browser_executable: str = ""

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "AutonomyRuntimeConfig":
        values = os.environ if env is None else env
        return cls(
            desktop_enabled=_env_bool(values, "AEGIS_DESKTOP_ENABLED", True),
            browser_enabled=_env_bool(values, "AEGIS_BROWSER_ENABLED", True),
            voice_enabled=_env_bool(values, "AEGIS_VOICE_ENABLED", True),
            android_enabled=_env_bool(values, "AEGIS_ANDROID_ENABLED", True),
            confirmation_required=_env_bool(values, "AEGIS_CONFIRMATION_REQUIRED", True),
            full_access=_env_bool(values, "AEGIS_FULL_ACCESS", False),
            adb_path=_env_value(values, "AEGIS_ADB_PATH", ""),
            browser_executable=_env_value(values, "AEGIS_BROWSER_EXECUTABLE", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "desktop_enabled": self.desktop_enabled,
            "browser_enabled": self.browser_enabled,
            "voice_enabled": self.voice_enabled,
            "android_enabled": self.android_enabled,
            "confirmation_required": self.confirmation_required,
            "full_access": self.full_access,
            # Paths are operational configuration, never credentials.
            "adb_path_configured": bool(self.adb_path),
            "browser_executable_configured": bool(self.browser_executable),
        }


@dataclass(frozen=True)
class CapabilityReport:
    """One stable, serializable capability result."""

    name: str
    status: str
    enabled: bool
    reason: str
    missing_optional: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.status == AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "enabled": self.enabled,
            "available": self.available,
            "reason": self.reason,
            "missing_optional": list(self.missing_optional),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AutonomyCapabilityContract:
    """A reproducible snapshot of the five autonomy safety boundaries."""

    config: AutonomyRuntimeConfig
    capabilities: Mapping[str, CapabilityReport]

    def __getitem__(self, name: str) -> CapabilityReport:
        return self.capabilities[name]

    def get(self, name: str, default: Optional[CapabilityReport] = None) -> Optional[CapabilityReport]:
        return self.capabilities.get(name, default)

    @property
    def safe_for_autonomy(self) -> bool:
        confirmation = self.capabilities.get("confirmation")
        return bool(
            confirmation
            and confirmation.status == AVAILABLE
            and self.config.confirmation_required
            and not self.config.full_access
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1",
            "safe_for_autonomy": self.safe_for_autonomy,
            "config": self.config.to_dict(),
            "capabilities": {
                name: self.capabilities[name].to_dict()
                for name in sorted(self.capabilities)
            },
        }

    as_dict = to_dict


def _disabled(name: str, reason: str) -> CapabilityReport:
    return CapabilityReport(name, DISABLED, False, reason)


def _desktop_report(
    config: AutonomyRuntimeConfig,
    platform_name: str,
    finder: ModuleFinder,
) -> CapabilityReport:
    if not config.desktop_enabled:
        return _disabled("desktop", "disabled by AEGIS_DESKTOP_ENABLED")
    if platform_name != "win32":
        return CapabilityReport(
            "desktop",
            UNAVAILABLE,
            True,
            f"desktop input requires an interactive Windows session (platform={platform_name})",
            metadata={"platform": platform_name, "requires_interactive_session": True},
        )
    missing = tuple(
        dependency
        for dependency in ("pyautogui", "PIL")
        if not _module_present(dependency, finder)
    )
    if missing:
        return CapabilityReport(
            "desktop",
            DEGRADED,
            True,
            "desktop platform is present but optional input/capture dependencies are missing",
            missing_optional=missing,
            metadata={"platform": platform_name, "requires_interactive_session": True},
        )
    return CapabilityReport(
        "desktop",
        AVAILABLE,
        True,
        "Windows desktop input and capture dependencies are discoverable",
        metadata={"platform": platform_name, "requires_interactive_session": True},
    )


def _browser_report(
    config: AutonomyRuntimeConfig,
    finder: ModuleFinder,
    executable_finder: ExecutableFinder,
) -> CapabilityReport:
    if not config.browser_enabled:
        return _disabled("browser", "disabled by AEGIS_BROWSER_ENABLED")
    if not _module_present("playwright", finder):
        return CapabilityReport(
            "browser",
            DEGRADED,
            True,
            "Playwright is not installed; browser automation is unavailable",
            missing_optional=("playwright",),
        )
    metadata: dict[str, Any] = {"playwright": True}
    if config.browser_executable:
        executable = _find_executable(config.browser_executable, (), executable_finder)
        if not executable:
            return CapabilityReport(
                "browser", DEGRADED, True,
                "Playwright is installed but AEGIS_BROWSER_EXECUTABLE does not point to an executable",
                missing_optional=("browser-executable",),
            )
        metadata["browser_executable_configured"] = True
        return CapabilityReport(
            "browser", AVAILABLE, True,
            "Playwright is installed and an operator browser executable is configured",
            metadata=metadata,
        )
    return CapabilityReport(
        "browser", AVAILABLE, True,
        "Playwright Python bindings are discoverable; launchability is checked when a session starts",
        metadata=metadata,
    )


def _voice_report(config: AutonomyRuntimeConfig, finder: ModuleFinder) -> CapabilityReport:
    if not config.voice_enabled:
        return _disabled("voice", "disabled by AEGIS_VOICE_ENABLED")
    missing = tuple(
        dependency
        for dependency in ("pyttsx3", "whisper")
        if not _module_present(dependency, finder)
    )
    if missing:
        return CapabilityReport(
            "voice",
            DEGRADED,
            True,
            "voice is partially available; missing optional text-to-speech or speech-to-text dependencies",
            missing_optional=missing,
            metadata={"tts": "pyttsx3" not in missing, "stt": "whisper" not in missing},
        )
    return CapabilityReport(
        "voice", AVAILABLE, True,
        "text-to-speech and speech-to-text dependencies are discoverable",
        metadata={"tts": True, "stt": True},
    )


def _android_report(
    config: AutonomyRuntimeConfig,
    executable_finder: ExecutableFinder,
    command_runner: CommandRunner,
) -> CapabilityReport:
    if not config.android_enabled:
        return _disabled("android", "disabled by AEGIS_ANDROID_ENABLED")
    adb_path = _find_executable(config.adb_path, ("adb",), executable_finder)
    if not adb_path:
        return CapabilityReport(
            "android", DEGRADED, True,
            "Android support is configured but the optional ADB executable is unavailable",
            missing_optional=("adb",),
        )
    try:
        result = command_runner((adb_path, "devices"))
    except Exception as exc:
        return CapabilityReport(
            "android", DEGRADED, True,
            f"ADB is present but the device probe failed: {exc}",
            metadata={"adb": adb_path},
        )
    if result.returncode != 0:
        return CapabilityReport(
            "android", DEGRADED, True,
            "ADB is present but did not return a usable device listing",
            metadata={"adb": adb_path, "returncode": result.returncode},
        )
    devices = tuple(
        line.split()[0]
        for line in (result.stdout or "").splitlines()[1:]
        if len(line.split()) >= 2 and line.split()[1] == "device"
    )
    if not devices:
        return CapabilityReport(
            "android", DEGRADED, True,
            "ADB is available but no authorized Android device is connected",
            metadata={"adb": adb_path, "device_count": 0},
        )
    return CapabilityReport(
        "android", AVAILABLE, True,
        "ADB is available and at least one authorized Android device is connected",
        metadata={"adb": adb_path, "device_count": len(devices)},
    )


def _confirmation_report(
    config: AutonomyRuntimeConfig,
    finder: ModuleFinder,
) -> CapabilityReport:
    if not _module_present("core.execution.tool_executor", finder):
        return CapabilityReport(
            "confirmation", UNAVAILABLE, True,
            "the in-process operator approval mechanism is not discoverable",
        )
    if not config.confirmation_required:
        return CapabilityReport(
            "confirmation", DEGRADED, True,
            "operator confirmation has been disabled by AEGIS_CONFIRMATION_REQUIRED",
            metadata={"confirmation_required": False},
        )
    if config.full_access:
        return CapabilityReport(
            "confirmation", DEGRADED, True,
            "AEGIS_FULL_ACCESS bypasses approval for high-risk actions; safe autonomy is disabled",
            metadata={"confirmation_required": True, "full_access": True},
        )
    return CapabilityReport(
        "confirmation", AVAILABLE, True,
        "high-risk actions require explicit operator approval",
        metadata={"confirmation_required": True, "full_access": False},
    )


def build_capability_contract(
    env: Optional[Mapping[str, str]] = None,
    *,
    platform_name: Optional[str] = None,
    module_finder: Optional[ModuleFinder] = None,
    executable_finder: Optional[ExecutableFinder] = None,
    command_runner: Optional[CommandRunner] = None,
) -> AutonomyCapabilityContract:
    """Build a deterministic capability snapshot from the current runtime.

    Probe functions are injectable so offline tests never need desktop, browser,
    audio, or Android hardware.  Production callers should use the defaults.
    """
    config = AutonomyRuntimeConfig.from_env(env)
    module_probe = module_finder or importlib.util.find_spec
    executable_probe = executable_finder or shutil.which
    command_probe = command_runner or _default_command_runner
    detected_platform = platform_name or sys_platform()
    reports = {
        "android": _android_report(config, executable_probe, command_probe),
        "browser": _browser_report(config, module_probe, executable_probe),
        "confirmation": _confirmation_report(config, module_probe),
        "desktop": _desktop_report(config, detected_platform, module_probe),
        "voice": _voice_report(config, module_probe),
    }
    return AutonomyCapabilityContract(config=config, capabilities=reports)


def sys_platform() -> str:
    """Small seam for tests and a single place to read the host platform."""
    return sys.platform or platform_module.system().lower()


def get_capability_contract(**kwargs: Any) -> AutonomyCapabilityContract:
    """Convenience alias for callers that want a fresh snapshot."""
    return build_capability_contract(**kwargs)


__all__ = [
    "AVAILABLE", "DEGRADED", "UNAVAILABLE", "DISABLED",
    "AutonomyRuntimeConfig", "CapabilityReport", "AutonomyCapabilityContract",
    "build_capability_contract", "get_capability_contract", "detect_capabilities",
]


detect_capabilities = build_capability_contract
