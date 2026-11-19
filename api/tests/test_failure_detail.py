from __future__ import annotations

from app.services.instances import _failure_detail


class _Driver:
    def __init__(self, text: str) -> None:
        self.text = text

    def logs(self, _container_id: str, tail: int = 30) -> str:
        assert tail > 0
        return self.text


def test_failure_detail_prefers_an_error_line():
    """The container's own message is what the operator needs to see."""
    text = (
        "[dsh] dsh version: 0.2.1-rc.5\n"
        "[dsh] ERROR: $DSH_HOME (/data/dsh) is not writable by uid 1000:1000; "
        "it is owned by 0:0.\n"
        "[dsh] exiting\n"
    )
    detail = _failure_detail(_Driver(text), "abc")
    assert "not writable by uid 1000:1000" in detail
    assert detail.startswith(": ")


def test_failure_detail_falls_back_to_the_last_line():
    detail = _failure_detail(_Driver("starting\nlistening\nbye\n"), "abc")
    assert detail == ": bye"


def test_failure_detail_is_empty_when_nothing_is_available():
    assert _failure_detail(_Driver(""), "abc") == ""
    assert _failure_detail(None, "abc") == ""
    assert _failure_detail(None, None) == ""


def test_failure_detail_never_raises():
    class Broken:
        def logs(self, *_args, **_kwargs):
            raise RuntimeError("docker went away")

    assert _failure_detail(Broken(), "abc") == ""


def test_failure_detail_is_bounded():
    detail = _failure_detail(_Driver("x" * 5000), "abc")
    assert len(detail) <= 282