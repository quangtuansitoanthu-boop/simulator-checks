"""Apply the pinned UI regression overlay only inside the GitHub QA checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

BASE_SHA = '043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7'
MANIFEST_SHA = 'b09de0096afa6eb377b1a24725682d18a7f30f37037c830fcf943dc2aa3de295'
BASE_COMMIT = '166d0aeb43341da6f907e096f80a003b49d3be89'
ALLOWED = (
    'src/lib/components/practice/le-phat/LePhatScreen.svelte',
    'tests/e2e/name-copying.spec.ts',
    'tests/e2e/reader-defaults.spec.ts',
    'tests/e2e/visual-baseline.spec.ts',
    'tests/e2e/tu-tap.spec.ts',
    'tests/e2e/practice-control-layout.spec.ts',
    'tests/e2e/notification-read-sync.spec.ts',
    'tests/e2e/runtime-helpers.ts',
    'tests/e2e/fixtures/local-runtime-seed.sql',
    'tests/e2e/helpers/personal-journal-zen.ts',
)
TEST_DECLARATION = rb'\btest(?:\.(?:only|skip|fixme))?\s*\('
DISABLED_DECLARATION = rb'\btest\.(?:skip|fixme)\s*\('


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def css_parts(data):
    assert data.count(b'<style>') == data.count(b'</style>') == 1
    prefix, _, remaining = data.partition(b'<style>')
    style, _, suffix = remaining.partition(b'</style>')
    return prefix, style, suffix


parser = argparse.ArgumentParser()
parser.add_argument('app')
parser.add_argument('--runtime-only', action='store_true')
args = parser.parse_args()
assert os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub CI only'
workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
app = Path(args.app).resolve(strict=True)
transfer = Path(__file__).resolve().parent
assert app == workspace / 'app' and transfer == workspace / 'harness'
assert app != transfer and (app / '.git').exists()
git = ['git', '--no-optional-locks', '-C', str(app)]
assert Path(subprocess.check_output(git + ['rev-parse', '--show-toplevel'], text=True).strip()).resolve() == app
assert subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip() == BASE_COMMIT


def checkout_path(name):
    relative = PurePosixPath(name)
    assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in name
    target = app.joinpath(*relative.parts)
    assert target.resolve().is_relative_to(app)
    ancestor = target
    while ancestor != app:
        assert not ancestor.is_symlink(), name
        ancestor = ancestor.parent
    return target


manifest_bytes = (transfer / 'ui-fixes.json').read_bytes()
assert sha(manifest_bytes) == MANIFEST_SHA, 'UI overlay manifest pin mismatch'
manifest = json.loads(manifest_bytes)
base_bytes = (transfer / 'mobile-current-manifest.json').read_bytes()
assert sha(base_bytes) == BASE_SHA == manifest['baseManifestSha256']
base = json.loads(base_bytes)
assert manifest['baselineCommit'] == base['baselineCommit'] == BASE_COMMIT
assert manifest['baselineRepository'] == base['baselineRepository'] == 'quangtuansitoanthu-boop/r-8f31b6c2'
assert manifest['schemaVersion'] == 1 and manifest['fileCount'] == len(ALLOWED)
assert manifest['runtimePaths'] == [ALLOWED[0]] and not manifest['sensitiveFindings']
assert [item['path'] for item in manifest['files']] == list(ALLOWED)
assert manifest['archive'] == 'ui-fixes.tar.gz'
assert 0 < manifest['archiveBytes'] <= 1024 * 1024
assert 0 < manifest['totalBytes'] <= 4 * 1024 * 1024
archive = transfer / manifest['archive']
archive_bytes = archive.read_bytes()
assert len(archive_bytes) == manifest['archiveBytes']
assert sha(archive_bytes) == manifest['archiveSha256']
items = {item['path']: item for item in manifest['files']}
payloads = {}
before_blobs = {}
with tarfile.open(archive, 'r:gz') as tar:
    assert tar.getnames() == list(ALLOWED), 'Unexpected or duplicate archive path'
    for member in tar:
        assert member.isfile() and not member.issym() and not member.islnk()
        item = items[member.name]
        assert 0 < member.size == item['bytes'] <= 2 * 1024 * 1024
        target = checkout_path(member.name)
        before = target.read_bytes()
        assert blob(before) == item['baseGitBlobSha1'] == base['expected'][member.name], member.name
        data = tar.extractfile(member).read()
        assert len(data) == item['bytes'] and sha(data) == item['sha256'], member.name
        assert blob(data) == item['gitBlobSha1'], member.name
        data.decode('utf-8')
        if member.name.endswith('.spec.ts'):
            assert len(re.findall(TEST_DECLARATION, before)) == len(re.findall(TEST_DECLARATION, data)) == item['testDeclarations'], member.name
            assert len(re.findall(DISABLED_DECLARATION, before)) == len(re.findall(DISABLED_DECLARATION, data)) == item['disabledDeclarations'], member.name
        if member.name == ALLOWED[0]:
            old_parts, new_parts = css_parts(before), css_parts(data)
            assert old_parts[0] == new_parts[0] and old_parts[2] == new_parts[2], 'Runtime overlay must change CSS only'
            assert sha(old_parts[1]) == manifest['runtimeCss']['baseSha256']
            assert sha(new_parts[1]) == manifest['runtimeCss']['sha256']
        payloads[member.name] = data
        before_blobs[member.name] = blob(before)
assert sum(map(len, payloads.values())) == manifest['totalBytes']
selected = [ALLOWED[0]] if args.runtime_only else list(ALLOWED)
report_path = checkout_path('qa-evidence/ui-fixes-verification.json')

# Toàn bộ archive và source gốc phải khớp trước khi ghi bất kỳ file nào.
for name in selected:
    checkout_path(name).write_bytes(payloads[name])
for name in ALLOWED:
    actual = checkout_path(name).read_bytes()
    if name in selected:
        assert sha(actual) == items[name]['sha256'], name
    else:
        assert blob(actual) == before_blobs[name], name
report = {
    'status': 'EXACT_UI_FIXES_VERIFIED',
    'mode': 'runtime-only' if args.runtime_only else 'all',
    'fileCount': len(selected),
    'archiveFileCount': len(ALLOWED),
    'selectedPaths': selected,
    'validatedPaths': list(ALLOWED),
    'manifestSha256': MANIFEST_SHA,
    'archiveSha256': manifest['archiveSha256'],
    'baseManifestSha256': BASE_SHA,
    'runtimeCssOnly': True,
    'files': [items[name] for name in selected],
}
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'status': report['status'], 'mode': report['mode'], 'fileCount': len(selected), 'manifestSha256': MANIFEST_SHA}))
