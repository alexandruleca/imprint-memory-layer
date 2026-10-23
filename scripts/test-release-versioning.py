"""Exercise the actual release analysis against disposable Git histories."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

workflow = Path(__file__).resolve().parents[1] / '.github/workflows/release.yml'
source = workflow.read_text().split('      - name: Compute next version\n', 1)[1].split('        run: |\n', 1)[1]
lines = []
for line in source.splitlines():
    if line and not line.startswith('          '):
        break
    lines.append(line)
script = textwrap.dedent('\n'.join(lines))
assert 'git log' in script and 'GITHUB_OUTPUT' in script


def check(messages, expected, force='auto', tagged=True):
    with tempfile.TemporaryDirectory() as directory:
        def git(*args):
            subprocess.run(['git', *args], cwd=directory, check=True, capture_output=True)
        git('init', '-q')
        git('config', 'user.name', 'Release fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        git('commit', '-q', '--allow-empty', '-m', 'chore: baseline')
        if tagged:
            git('tag', 'v1.2.3')
        for message in messages:
            git('commit', '-q', '--allow-empty', '-m', message)
        output = Path(directory) / 'outputs'
        subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=directory,
                       env={**os.environ, 'GITHUB_OUTPUT': str(output), 'FORCE_BUMP': force}, check=True, capture_output=True)
        values = dict(line.split('=', 1) for line in output.read_text().splitlines())
        assert values['version'] == expected, (messages, values, expected)


check(['feat: portable device context', 'fix: latest small correction'], 'v1.3.0')
check(['fix: earlier correction', 'docs: latest notes'], 'v1.2.4')
check(['feat: new context\n\nBREAKING CHANGE: new contract', 'fix: latest correction'], 'v2.0.0')
check(['docs: update notes'], '')
check(['feat: new context'], 'v1.2.4', force='patch')
check(['feat: initial feature'], 'v0.1.0', tagged=False)
print('Release history, precedence, forced bump and first-release regressions passed')
