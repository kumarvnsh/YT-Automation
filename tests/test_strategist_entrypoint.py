from pathlib import Path


def test_old_task_entrypoint_delegates_before_guard_and_dispatch_precedes_dashboard():
    script=(Path(__file__).resolve().parents[1]/'scripts/run_strategist.ps1').read_text(encoding='utf-8')
    guard="throw 'The strategist must use its dedicated data clone"
    assert "D:\\YT-Automation\\scripts\\run_strategist.ps1" in script
    assert script.index('-File $CanonicalRunner -DataRoot $Repo')<script.index(guard)
    assert 'exit $LASTEXITCODE' in script[:script.index(guard)]
    assert script.index("@('scripts\\dispatch.py')")<script.index("(Join-Path $PSScriptRoot 'start_dashboard.ps1')")
