import pytest

# Shared mutable store — tests write into this; pytest_terminal_summary reads it.
_smoke_results: dict[str, tuple[str, str]] = {}


@pytest.fixture
def smoke_results():
    """Return the shared results dict for tests to record (operation → (status, verdict))."""
    return _smoke_results


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print the ALE adapter smoke test summary table after the run."""
    if not _smoke_results:
        return

    terminalreporter.write_sep("━", "ALE Adapter Smoke Test Summary")
    header = f"  {'Operation':<36}  {'Status Returned':<24}  Result"
    terminalreporter.write_line(header)
    terminalreporter.write_line("  " + "─" * 66)
    for op in sorted(_smoke_results):
        status, verdict = _smoke_results[op]
        terminalreporter.write_line(f"  {op:<36}  {status:<24}  {verdict}")
    terminalreporter.write_sep("━", "")
