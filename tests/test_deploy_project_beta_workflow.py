from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / '.github/workflows/deploy-project-beta.yml'


def test_successor_workflow_is_private_serial_and_uses_only_fixed_privileged_commands():
    body = WORKFLOW.read_text()
    assert 'branches: [beta-release]' in body
    assert 'pull_request:' not in body and 'workflow_dispatch:' not in body
    assert "github.repository == 'damjan-popic/KLIKai'" in body
    assert 'cancel-in-progress: false' in body
    assert '/usr/local/sbin/klikai-successor-activate' in body
    assert 'release/components/infra/scripts/prepare-successor-release.py' in body
    sudo_lines = [line.strip() for line in body.splitlines() if 'sudo ' in line]
    assert sudo_lines == [
        'sudo -n /usr/local/sbin/klikai-successor-activate \\',
        'run: sudo -n /usr/local/sbin/klikai-successor-backup',
    ]
    assert not any('release/components' in line for line in sudo_lines)
    assert 'actions/checkout@v4' not in body
    assert body.count('actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683') == 2
    for test in ('test_lexical_profiles.py', 'test_pilot_interpretation_projection.py',
                 'test_pilot_validation_diagnostics.py', 'test_pilot_ui_interpretation.mjs'):
        assert test in body
    assert 'KLIKAI_PINNED_CORE_ROOT: ${{ github.workspace }}/release/components/core' in body
    assert 'KLIKAI_PINNED_INFRA_ROOT: ${{ github.workspace }}/release/components/infra' in body
    assert 'release/components/core/tests/zrc' in body
    assert '/data/projects/' not in body


def test_validation_diagnostics_has_no_mutable_host_checkout_fallback():
    body=(WORKFLOW.parents[2]/'tests/test_pilot_validation_diagnostics.py').read_text()
    assert '/data/projects/'not in body
    assert "pinned_support_root('KLIKAI_PINNED_CORE_ROOT'"in body
    assert "pinned_support_root('KLIKAI_PINNED_INFRA_ROOT'"in body


def test_authenticated_acceptance_precedes_external_edge_and_backup_jobs():
    body = WORKFLOW.read_text()
    assert body.index('Activate through the fixed reversible host coordinator') < body.index('verify-public-edge:')
    assert body.index('verify-public-edge:') < body.index('complete-recovery-checkpoint:')
    assert 'needs: verify-and-deliver' in body
    assert 'needs: verify-public-edge' in body
