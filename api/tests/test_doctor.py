from __future__ import annotations

from fastapi.testclient import TestClient

from app.doctor import FAIL, PASS, WARN, print_doctor, run_doctor


def test_doctor_runs_offline(client: TestClient):
    """Doctor must not require the network and must classify rather than crash."""
    checks = run_doctor()
    by_name = {check.name: check for check in checks}
    assert by_name["数据库"].level == PASS
    # The test environment uses fakeredis / FakeDriver on purpose.
    assert by_name["Redis"].level == WARN
    assert by_name["Docker"].level == WARN
    # SQLite here, so there is no alembic_version table.
    assert by_name["迁移版本"].level in (PASS, WARN)
    assert all(check.level in (PASS, WARN, FAIL) for check in checks)


def test_doctor_exit_code_reflects_failures(capsys, monkeypatch):
    from app import doctor

    def broken() -> list[doctor.Check]:
        return [doctor.Check(FAIL, "数据库", "boom")]

    monkeypatch.setattr(doctor, "run_doctor", broken)
    assert print_doctor(broken()) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out and "1 项失败" in out


def test_doctor_warn_only_exits_zero(capsys):
    from app import doctor

    assert print_doctor([doctor.Check(WARN, "Redis", "fake")]) == 0
    assert "全部通过" in capsys.readouterr().out


def test_doctor_cli_command(client: TestClient, capsys):
    from app.cli import main

    code = main(["doctor"])
    out = capsys.readouterr().out
    assert "[PASS]" in out
    assert code in (0, 1)
