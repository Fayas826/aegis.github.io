"""Offline safety and reproducibility checks for the autonomy contract."""

from __future__ import annotations

import subprocess

from core.runtime.autonomy_capability_contract import (
    AVAILABLE,
    DEGRADED,
    DISABLED,
    UNAVAILABLE,
    build_capability_contract,
)


def _finder(*available: str):
    names = set(available)
    return lambda name: object() if name in names else None


def _which(*available: str):
    names = set(available)
    return lambda name: f"/opt/{name}" if name in names else None


def _no_command(_args):
    return subprocess.CompletedProcess(_args, 0, "List of devices attached\n", "")


def test_environment_configuration_is_explicit_and_never_a_secret():
    contract = build_capability_contract(
        {
            "AEGIS_DESKTOP_ENABLED": "false",
            "AEGIS_CONFIRMATION_REQUIRED": "true",
            "AEGIS_FULL_ACCESS": "false",
            "AEGIS_ADB_PATH": "/custom/adb",
        },
        platform_name="win32",
        module_finder=_finder("pyautogui", "PIL", "playwright", "pyttsx3", "whisper",
                              "core.execution.tool_executor"),
        executable_finder=lambda _name: None,
        command_runner=_no_command,
    )

    assert contract.config.desktop_enabled is False
    assert contract.config.adb_path == "/custom/adb"
    assert contract.safe_for_autonomy is True
    payload = contract.to_dict()
    assert payload["capabilities"]["desktop"]["status"] == DISABLED
    assert "api_key" not in str(payload).lower()
    assert "secret" not in str(payload).lower()


def test_optional_dependencies_are_reported_as_degraded_without_importing_them():
    contract = build_capability_contract(
        {},
        platform_name="win32",
        module_finder=_finder("core.execution.tool_executor"),
        executable_finder=_which(),
        command_runner=_no_command,
    )

    assert contract["desktop"].status == DEGRADED
    assert set(contract["desktop"].missing_optional) == {"pyautogui", "PIL"}
    assert contract["browser"].status == DEGRADED
    assert contract["browser"].missing_optional == ("playwright",)
    assert contract["voice"].status == DEGRADED
    assert set(contract["voice"].missing_optional) == {"pyttsx3", "whisper"}
    assert contract["android"].status == DEGRADED
    assert contract["android"].missing_optional == ("adb",)


def test_platform_prerequisite_and_confirmation_policy_are_truthful():
    contract = build_capability_contract(
        {"AEGIS_FULL_ACCESS": "true"},
        platform_name="linux",
        module_finder=_finder("core.execution.tool_executor"),
        executable_finder=_which("adb"),
        command_runner=_no_command,
    )

    assert contract["desktop"].status == UNAVAILABLE
    assert "Windows" in contract["desktop"].reason
    assert contract["confirmation"].status == DEGRADED
    assert contract.safe_for_autonomy is False
    assert contract["android"].status == DEGRADED
    assert "no authorized Android device" in contract["android"].reason


def test_authorized_android_device_and_complete_optional_stack_are_available():
    def devices(_args):
        return subprocess.CompletedProcess(
            _args, 0, "List of devices attached\nserial-123\tdevice\n", ""
        )

    contract = build_capability_contract(
        {},
        platform_name="win32",
        module_finder=_finder("pyautogui", "PIL", "playwright", "pyttsx3", "whisper",
                              "core.execution.tool_executor"),
        executable_finder=_which("adb"),
        command_runner=devices,
    )

    for name in ("desktop", "browser", "voice", "android", "confirmation"):
        assert contract[name].status == AVAILABLE, name
    assert contract["android"].metadata["device_count"] == 1
    assert contract.safe_for_autonomy is True


def test_disabled_capabilities_are_stable_and_serialization_is_sorted():
    contract = build_capability_contract(
        {
            "AEGIS_BROWSER_ENABLED": "off",
            "AEGIS_VOICE_ENABLED": "0",
            "AEGIS_ANDROID_ENABLED": "false",
            "AEGIS_CONFIRMATION_REQUIRED": "false",
        },
        platform_name="linux",
        module_finder=_finder("core.execution.tool_executor"),
        executable_finder=_which(),
        command_runner=_no_command,
    )

    assert contract["browser"].status == DISABLED
    assert contract["voice"].status == DISABLED
    assert contract["android"].status == DISABLED
    assert contract["confirmation"].status == DEGRADED
    assert list(contract.to_dict()["capabilities"]) == [
        "android", "browser", "confirmation", "desktop", "voice"
    ]
