"""CI-only two-line upstream Playwright driver diagnostic; never patch local dependencies."""
import argparse
import hashlib
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('app', type=Path)
parser.add_argument('--dry-run', action='store_true')
args = parser.parse_args()
app = args.app.resolve(strict=True)
core = app / 'node_modules/playwright-core/lib/coreBundle.js'
before_sha = '9393fa79e1c67c74edc26b610d65a4f7ed73d345a762465cc88340a33a2454ac'
old = (b'        if (contextOptions.offline)\n'
       b'          promises2.push(session2.send("Network.setEmulateOfflineState", { offline: true }));')
new = (b'        if (contextOptions.offline !== undefined)\n'
       b'          promises2.push(session2.send("Network.setEmulateOfflineState", { offline: contextOptions.offline }));')

def sha(data):
    return hashlib.sha256(data).hexdigest()

assert core.is_file() and not core.is_symlink()
assert core.resolve().is_relative_to(app / 'node_modules/playwright-core')
if not args.dry_run:
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'Local dependencies must remain stock; use --dry-run'
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    assert app == workspace / 'app', 'Only the disposable CI app checkout may be changed'

unchanged_paths = [
    'package.json', 'package-lock.json',
    'node_modules/@playwright/test/package.json',
    'node_modules/playwright/package.json',
    'node_modules/playwright-core/package.json',
]
unchanged = {name: sha((app / name).read_bytes()) for name in unchanged_paths}
versions = {name: json.loads((app / f'node_modules/{name}/package.json').read_text())['version']
            for name in ['@playwright/test', 'playwright', 'playwright-core']}
assert set(versions.values()) == {'1.62.1'}, 'Do not upgrade the locked version'
before = core.read_bytes()
assert sha(before) == before_sha, 'Original driver differs from the verified stock run'
assert before.count(old) == 1 and new not in before, 'Expected exactly the original initialization snippet'
after = before.replace(old, new)
assert after.count(new) == 1 and after.replace(new, old) == before
assert len(old.splitlines()) == len(new.splitlines()) == 2
proof = {
    'diagnosticOnly': True,
    'applicationValidated': False,
    'treatment': 'CI-only upstream offline-initialization driver backport; package version unchanged',
    'stockBaselineRun': 44917102264,
    'upstreamPr': 'https://github.com/microsoft/playwright/pull/42894',
    'upstreamMergeCommit': 'ea75b9f32ca4120efdfaf8f5b8cfd292f548f197',
    'target': core.relative_to(app).as_posix(),
    'beforeSha256': sha(before), 'afterSha256': sha(after),
    'beforeBytes': len(before), 'afterBytes': len(after),
    'changedLines': 2,
    'beforeSnippet': old.decode(), 'afterSnippet': new.decode(),
    'allOtherDriverBytesUnchanged': True,
    'packageVersions': versions,
    'unchangedPackageHashes': unchanged,
    'dryRun': args.dry_run,
}
if not args.dry_run:
    core.write_bytes(after)
    assert core.read_bytes() == after
    assert {name: sha((app / name).read_bytes()) for name in unchanged_paths} == unchanged
    output = app / 'qa-evidence/webkit-sw-offline-probe/driver-patch.json'
    assert output.resolve().is_relative_to(app / 'qa-evidence')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
print(json.dumps(proof))
