"""Apply four pinned E2E fixture corrections after the complete current QA snapshot."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

BASE_SHA = '043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7'
UI_SHA = 'b09de0096afa6eb377b1a24725682d18a7f30f37037c830fcf943dc2aa3de295'
CURRENT_SHA = '9851a327a9f64343a362cc08cccf7b0f251f7829a57b6d65cd30a29ee2b1c137'
MANIFEST_SHA = 'eb6dabe803ec93016364795f807a47fe91641286e2d81e77ce8afafb9431a3aa'
BASE_COMMIT = '166d0aeb43341da6f907e096f80a003b49d3be89'
ALLOWED = (
    'tests/e2e/name-copying.spec.ts',
    'tests/e2e/english-reader-presentation.spec.ts',
    'tests/e2e/tracker-visual.spec.ts',
    'tests/e2e/audio-initial-link.spec.ts',
)
DECLARATIONS = (10, 2, 4, 2)
SELECTORS = (89, 38, 45, 6)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('app')
args = parser.parse_args()
assert os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub CI only'
workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
app = Path(args.app).resolve(strict=True)
transfer = Path(__file__).resolve().parent
assert app == workspace / 'app' and transfer == workspace / 'harness'
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


base_bytes = (transfer / 'mobile-current-manifest.json').read_bytes()
ui_bytes = (transfer / 'ui-fixes.json').read_bytes()
current_bytes = (transfer / 'current-follow-up.json').read_bytes()
manifest_bytes = (transfer / 'qa-test-follow-up.json').read_bytes()
assert sha(base_bytes) == BASE_SHA and sha(ui_bytes) == UI_SHA
assert sha(current_bytes) == CURRENT_SHA and sha(manifest_bytes) == MANIFEST_SHA
base, ui, current, manifest = map(json.loads, (base_bytes, ui_bytes, current_bytes, manifest_bytes))
assert base['baselineCommit'] == ui['baselineCommit'] == current['baselineCommit'] == manifest['baselineCommit'] == BASE_COMMIT
assert manifest['schemaVersion'] == 1 and manifest['fileCount'] == len(ALLOWED) == 4
assert manifest['baseManifestSha256'] == BASE_SHA
assert manifest['uiFixManifestSha256'] == UI_SHA and manifest['currentFollowUpManifestSha256'] == CURRENT_SHA
assert not manifest['runtimePaths'] and manifest['runtimeFileCount'] == 0
assert not manifest['sensitiveFindings']
assert [item['path'] for item in manifest['files']] == list(ALLOWED)
ui_proof = json.loads(checkout_path('qa-evidence/ui-fixes-verification.json').read_bytes())
current_proof = json.loads(checkout_path('qa-evidence/current-follow-up-verification.json').read_bytes())
assert ui_proof['status'] == 'EXACT_UI_FIXES_VERIFIED' and ui_proof['mode'] == 'all'
assert ui_proof['manifestSha256'] == UI_SHA and ui_proof['archiveSha256'] == ui['archiveSha256']
assert current_proof['status'] == 'EXACT_CURRENT_FOLLOW_UP_VERIFIED' and current_proof['mode'] == 'all'
assert current_proof['manifestSha256'] == CURRENT_SHA and current_proof['archiveSha256'] == current['archiveSha256']
effective = dict(base['expected'])
for layer in (ui, current):
    effective.update({item['path']: item['gitBlobSha1'] for item in layer['files']})
assert len(effective) == 3275
assert manifest['archive'] == 'qa-test-follow-up.tar.gz'
assert 0 < manifest['archiveBytes'] <= 1024 * 1024
archive = transfer / manifest['archive']
archive_bytes = archive.read_bytes()
assert len(archive_bytes) == manifest['archiveBytes'] and sha(archive_bytes) == manifest['archiveSha256']
payloads = {}
with tarfile.open(archive, 'r:gz') as tar:
    assert tar.getnames() == list(ALLOWED), 'Unexpected or duplicate test path'
    for position, member in enumerate(tar):
        item = manifest['files'][position]
        assert member.isfile() and not member.issym() and not member.islnk()
        assert 0 < member.size == item['bytes'] <= 256 * 1024
        assert item['baseGitBlobSha1'] == effective[member.name]
        before = checkout_path(member.name).read_bytes()
        assert blob(before) == item['baseGitBlobSha1'] and sha(before) == item['baseSha256'], member.name
        data = tar.extractfile(member).read()
        data.decode('utf-8')
        assert len(data) == item['bytes'] and sha(data) == item['sha256'] and blob(data) == item['gitBlobSha1']
        assert item['contractsUnchanged'] is True
        assert item['testContract']['testDeclarations'] == DECLARATIONS[position]
        assert len(item['testContract']['selectors']) == SELECTORS[position]
        assert not item['testContract']['disabledCalls']
        payloads[member.name] = data
assert sum(map(len, payloads.values())) == manifest['totalBytes']

# Mọi file gốc và payload đều đã kiểm xong trước lần ghi đầu; không ghi runtime/native.
for name in ALLOWED:
    checkout_path(name).write_bytes(payloads[name])
for item in manifest['files']:
    assert sha(checkout_path(item['path']).read_bytes()) == item['sha256']
report = {
    'status': 'EXACT_QA_TEST_FOLLOW_UP_VERIFIED', 'mode': 'all',
    'fileCount': 4, 'runtimeFileCount': 0, 'runtimePaths': [],
    'baseManifestSha256': BASE_SHA, 'uiFixManifestSha256': UI_SHA,
    'currentFollowUpManifestSha256': CURRENT_SHA,
    'manifestSha256': MANIFEST_SHA, 'archiveSha256': manifest['archiveSha256'],
    'selectedPaths': list(ALLOWED), 'files': manifest['files'],
    'testTitlesSelectorsDeclarationsUnchanged': True,
}
output = checkout_path('qa-evidence/qa-test-follow-up-verification.json')
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({key: value for key, value in report.items() if key != 'files'}, ensure_ascii=False))
