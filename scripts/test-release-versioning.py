"""Exercise the actual release analysis against disposable Git histories."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

workflows = Path(__file__).resolve().parents[1] / '.github/workflows'


def step_script(workflow, step):
    """The shell script of a workflow step, as the runner executes it."""
    source = (workflows / workflow).read_text().split(f'      - name: {step}\n', 1)[1].split('        run: |\n', 1)[1]
    lines = []
    for line in source.splitlines():
        if line and not line.startswith('          '):
            break
        lines.append(line)
    return textwrap.dedent('\n'.join(lines))


script = step_script('release.yml', 'Compute next version')
assert 'git log' in script and 'GITHUB_OUTPUT' in script


def check(messages, expected, force='auto', tagged=True, ref='refs/heads/main', tags=(), on_main=None, head=None):
    """Analyze `messages` committed after a v1.2.3 baseline on `ref`.

    `tags` are extra tags on the baseline. `on_main` is how many of the new
    commits main holds (all of them on main itself; none on dev by default).
    `head` is how many of them the run checks out (all by default).
    """
    with tempfile.TemporaryDirectory() as directory:
        def git(*args):
            return subprocess.run(['git', *args], cwd=directory, check=True, capture_output=True, text=True).stdout.strip()
        git('init', '-q')
        git('config', 'user.name', 'Release fixture')
        git('config', 'user.email', 'fixture@example.invalid')
        git('commit', '-q', '--allow-empty', '-m', 'chore: baseline')
        if tagged:
            git('tag', 'v1.2.3')
        for tag in tags:
            git('tag', tag)
        commits = [git('rev-parse', 'HEAD')]
        for message in messages:
            git('commit', '-q', '--allow-empty', '-m', message)
            commits.append(git('rev-parse', 'HEAD'))
        if on_main is None:
            on_main = len(messages) if ref == 'refs/heads/main' else 0
        git('update-ref', 'refs/remotes/origin/main', commits[on_main])
        git('checkout', '-q', commits[len(messages) if head is None else head])
        output = Path(directory) / 'outputs'
        subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=directory,
                       env={**os.environ, 'GITHUB_OUTPUT': str(output), 'FORCE_BUMP': force, 'GITHUB_REF': ref},
                       check=True, capture_output=True)
        values = dict(line.split('=', 1) for line in output.read_text().splitlines())
        assert values['version'] == expected, (ref, messages, values, expected)


# Stable releases from main.
check(['feat: portable device context', 'fix: latest small correction'], 'v1.3.0')
check(['fix: earlier correction', 'docs: latest notes'], 'v1.2.4')
check(['feat: new context\n\nBREAKING CHANGE: new contract', 'fix: latest correction'], 'v2.0.0')
check(['docs: update notes'], '')
check(['feat: new context'], 'v1.2.4', force='patch')
check(['feat: initial feature'], 'v0.1.0', tagged=False)
# Dev prerelease tags never count as the last stable release.
check(['fix: correction'], 'v1.2.4', tags=['v1.2.4-dev.3', 'v1.3.0-dev.1'])
# A queued main run whose commit main has since moved past still releases it.
check(['feat: new context', 'fix: later push'], 'v1.3.0', head=1)

# Dev prereleases: the next stable version, numbered per build of that version.
dev = 'refs/heads/dev'
check(['feat: portable device context', 'fix: latest small correction'], 'v1.3.0-dev.1', ref=dev)
check(['fix: correction'], 'v1.2.4-dev.3', ref=dev,
      tags=['v1.2.4-dev.1', 'v1.2.4-dev.2', 'v1.3.0-dev.7', 'v1.2.3-dev.9', 'v1.2.4-dev.x'])
check(['fix: correction'], 'v1.2.4-dev.10', ref=dev, tags=['v1.2.4-dev.2', 'v1.2.4-dev.9'])
check(['feat: new context\n\nBREAKING CHANGE: new contract'], 'v2.0.0-dev.1', ref=dev)
check(['feat: initial feature'], 'v0.1.0-dev.1', tagged=False, ref=dev)
# Nothing releasable skips the build, unless a manual run forces a bump.
check(['docs: update notes', 'ci: tune runners'], '', ref=dev)
check(['docs: update notes'], 'v1.2.4-dev.1', ref=dev, force='patch')
# A dev head main already holds is main's to release: the fast-forward after a
# main push, or main pushed first. A forced bump does not override it.
check(['feat: new context'], '', ref=dev, on_main=1)
check(['feat: new context', 'fix: correction'], '', ref=dev, on_main=2)
check(['feat: new context'], '', ref=dev, on_main=1, force='minor')
# dev at the latest stable release, as the fast-forward after it leaves it.
check([], '', ref=dev, tags=['v1.2.4'])
# Only the commits main lacks make a dev build; main holding older ones is fine.
check(['feat: released on main', 'fix: dev only'], 'v1.3.0-dev.1', ref=dev, on_main=1)
print('Release history, precedence, forced bump, first-release and dev prerelease regressions passed')

policy = step_script('build-release.yml', 'Require main or dev for release packaging')
for ref, allowed in [('refs/heads/main', True), ('refs/heads/dev', True), ('refs/heads/feature', False),
                     ('refs/heads/main-backup', False), ('refs/tags/v1.2.3', False), ('refs/pull/7/merge', False)]:
    result = subprocess.run(['bash', '-e', '-c', policy], env={**os.environ, 'SOURCE_REF': ref}, capture_output=True)
    assert (result.returncode == 0) == allowed, (ref, result)
print('Release packaging accepts main and dev only')
