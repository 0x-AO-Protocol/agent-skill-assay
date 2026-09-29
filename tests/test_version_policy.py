import warnings

import pytest
from packaging.version import Version

from skill_assay import version_policy


@pytest.fixture(autouse=True)
def clear_version_check_env(monkeypatch):
    monkeypatch.delenv("SKILL_ASSAY_NO_VERSION_CHECK", raising=False)


def test_get_installed_version_parses_release(monkeypatch):
    monkeypatch.setattr(
        version_policy.metadata,
        "version",
        lambda _name: "0.3.2",
    )
    assert version_policy.get_installed_version() == Version("0.3.2")


def test_get_installed_version_dev_returns_none(monkeypatch):
    monkeypatch.setattr(
        version_policy.metadata,
        "version",
        lambda _name: "dev",
    )
    assert version_policy.get_installed_version() is None


def test_should_emit_only_below_min_unsupported():
    assert version_policy.should_emit_unsupported_advisory(Version("0.0.9")) is True
    assert version_policy.should_emit_unsupported_advisory(Version("0.0.8")) is True
    assert version_policy.should_emit_unsupported_advisory(Version("0.0.1")) is True
    assert version_policy.should_emit_unsupported_advisory(Version("0.1.0")) is False
    assert version_policy.should_emit_unsupported_advisory(Version("0.1.1")) is False
    assert version_policy.should_emit_unsupported_advisory(Version("0.1.2")) is False
    assert version_policy.should_emit_unsupported_advisory(Version("0.1.3")) is False
    assert version_policy.should_emit_unsupported_advisory(Version("0.2.0")) is False
    assert version_policy.should_emit_unsupported_advisory(Version("1.0.0")) is False


def test_emit_advisory_silent_for_current_release(monkeypatch, capsys):
    monkeypatch.setattr(
        version_policy.metadata,
        "version",
        lambda _name: "0.1.0",
    )
    version_policy.emit_upgrade_advisory()
    assert capsys.readouterr().err == ""


def test_emit_advisory_silent_for_security_supported_floor(monkeypatch, capsys):
    monkeypatch.setattr(
        version_policy.metadata,
        "version",
        lambda _name: "0.1.0",
    )
    version_policy.emit_upgrade_advisory()
    assert capsys.readouterr().err == ""


def test_emit_advisory_silent_for_outdated_but_supported_band(monkeypatch, capsys):
    for version in ("0.1.0", "0.1.1", "0.1.2"):
        monkeypatch.setattr(
            version_policy.metadata,
            "version",
            lambda _name, v=version: v,
        )
        version_policy.emit_upgrade_advisory()
        assert capsys.readouterr().err == ""


def test_emit_advisory_warns_for_unsupported(monkeypatch, capsys):
    monkeypatch.setattr(
        version_policy.metadata,
        "version",
        lambda _name: "0.0.9",
    )
    version_policy.emit_upgrade_advisory()
    err = capsys.readouterr().err
    assert "0.0.9" in err
    assert "unsupported" in err.lower()
    assert ">=0.1.0" in err


def test_emit_advisory_respects_opt_out(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ASSAY_NO_VERSION_CHECK", "1")
    monkeypatch.setattr(
        version_policy.metadata,
        "version",
        lambda _name: "0.0.9",
    )
    version_policy.emit_upgrade_advisory()
    assert capsys.readouterr().err == ""


def test_cli_main_calls_advisory_once(monkeypatch):
    import sys

    calls = []
    from skill_assay import cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "emit_upgrade_advisory",
        lambda: calls.append(True),
    )
    monkeypatch.setattr(cli_module, "cmd_list", lambda **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["skill-assay", "list"])

    cli_module.main()
    assert len(calls) == 1


def test_support_status_warning_is_emitted_outside_window(monkeypatch):
    monkeypatch.setattr(
        version_policy.metadata,
        "version",
        lambda _name: "0.0.10",
    )
    version_policy._warned_versions.discard("0.0.10")
    with pytest.warns(version_policy.SupportStatusWarning):
        version_policy.emit_support_status_warning()


def test_support_status_warning_is_silent_inside_window(monkeypatch):
    monkeypatch.setattr(
        version_policy.metadata,
        "version",
        lambda _name: "0.1.0",
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        version_policy.emit_support_status_warning()
    assert not caught
