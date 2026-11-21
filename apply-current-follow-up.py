"""Apply the current-source follow-up after the pinned UI overlay, in GitHub QA only."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

BASE_SHA = '043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7'
UI_SHA = 'b09de0096afa6eb377b1a24725682d18a7f30f37037c830fcf943dc2aa3de295'
MANIFEST_SHA = '9851a327a9f64343a362cc08cccf7b0f251f7829a57b6d65cd30a29ee2b1c137'
BASE_COMMIT = '166d0aeb43341da6f907e096f80a003b49d3be89'
ALLOWED = (
    'src/lib/browser/public-media-fallback.ts',
    'src/lib/browser/quang-tu-facebook-videos.ts',
    'src/lib/components/practice/name-copying/NameCopying.svelte',
    'src/lib/data/offline-album-plays.json',
    'src/lib/data/quang-tu-facebook-videos.json',
    'src/lib/domain/practice/name-copying-copy.ts',
    'src/lib/domain/practice/name-copying.ts',
    'src/lib/i18n/copying-interface-en.json',
    'static/videos/fb-video-1593854504-6.mp4',
    'tests/e2e/admin-participation.spec.ts',
    'tests/e2e/audio-web-seek-regressions.spec.ts',
    'tests/e2e/bundled-book-illustrations.spec.ts',
    'tests/e2e/copying-layout.spec.ts',
    'tests/e2e/helpers/personal-journal-zen.ts',
    'tests/e2e/name-copying.spec.ts',
    'tests/e2e/native-approved-ui.spec.ts',
    'tests/e2e/native-chrome-selection.spec.ts',
    'tests/e2e/native-footer-paper.spec.ts',
    'tests/e2e/startup-branding.spec.ts',
    'tests/e2e/thanh-tinh-web-seek-regressions.spec.ts',
    'tests/e2e/tu-tap.spec.ts',
    'tests/unit/practice/name-copying.test.ts',
    'tests/unit/public-media-fallback.test.ts',
    'tests/unit/reading/quang-tu-facebook-videos.test.ts',
)
EXCLUDED = (
    'android/app/src/androidTest/java/vn/quangtu/tutam/audio/DharmaAudioWidgetInitialLayoutTest.java',
    'android/app/src/androidTest/java/vn/quangtu/tutam/widgets/CalendarWidgetRenderingTest.java',
    'ios/App/AppUITests/NativeGuestSmokeTests.swift',
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument('app')
parser.add_argument('--runtime-only', action='store_true')
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
manifest_bytes = (transfer / 'current-follow-up.json').read_bytes()
assert sha(base_bytes) == BASE_SHA
assert sha(ui_bytes) == UI_SHA
assert sha(manifest_bytes) == MANIFEST_SHA
base, ui, manifest = json.loads(base_bytes), json.loads(ui_bytes), json.loads(manifest_bytes)
assert manifest['baseManifestSha256'] == ui['baseManifestSha256'] == BASE_SHA
assert manifest['uiFixManifestSha256'] == UI_SHA
assert manifest['baselineCommit'] == ui['baselineCommit'] == base['baselineCommit'] == BASE_COMMIT
assert manifest['baselineRepository'] == ui['baselineRepository'] == base['baselineRepository'] == 'quangtuansitoanthu-boop/r-8f31b6c2'
assert manifest['schemaVersion'] == 1 and manifest['fileCount'] == len(ALLOWED)
assert [item['path'] for item in manifest['files']] == list(ALLOWED)
assert manifest['excludedPaths'] == list(EXCLUDED) and not manifest['sensitiveFindings']
assert manifest['baseExpectedFileCount'] == len(base['expected']) == 3275
runtime = [name for name in ALLOWED if name.startswith(('src/', 'static/'))]
assert manifest['runtimePaths'] == runtime and len(runtime) == 9

# Native dùng lớp UI runtime-only; core/UI cần đủ mười file của lớp UI trước đó.
ui_proof = json.loads(checkout_path('qa-evidence/ui-fixes-verification.json').read_bytes())
assert ui_proof['status'] == 'EXACT_UI_FIXES_VERIFIED' and ui_proof['manifestSha256'] == UI_SHA
assert ui_proof['archiveSha256'] == ui['archiveSha256']
ui_items = {item['path']: item for item in ui['files']}
assert len(ui_items) == ui['fileCount'] == 10
if ui_proof['mode'] == 'all':
    assert ui_proof['selectedPaths'] == list(ui_items)
elif ui_proof['mode'] == 'runtime-only':
    assert args.runtime_only and ui_proof['selectedPaths'] == ui['runtimePaths']
else:
    raise AssertionError('Unexpected prior UI overlay mode')
effective_full = dict(base['expected'])
effective_full.update({name: item['gitBlobSha1'] for name, item in ui_items.items()})
effective_applied = dict(base['expected'])
for name in ui_proof['selectedPaths']:
    item = ui_items[name]
    assert sha(checkout_path(name).read_bytes()) == item['sha256'], name
    effective_applied[name] = item['gitBlobSha1']

assert manifest['archive'] == 'current-follow-up.tar.gz'
assert 0 < manifest['archiveBytes'] <= 64 * 1024 * 1024
assert 0 < manifest['totalBytes'] <= 64 * 1024 * 1024
archive = transfer / manifest['archive']
archive_bytes = archive.read_bytes()
assert len(archive_bytes) == manifest['archiveBytes'] and sha(archive_bytes) == manifest['archiveSha256']
items = {item['path']: item for item in manifest['files']}
payloads = {}
before = {}
with tarfile.open(archive, 'r:gz') as tar:
    assert tar.getnames() == list(ALLOWED), 'Unexpected or duplicate archive path'
    for member in tar:
        assert member.isfile() and not member.issym() and not member.islnk()
        item = items[member.name]
        assert 0 < member.size == item['bytes'] <= 32 * 1024 * 1024
        assert item['baseGitBlobSha1'] == effective_full[member.name], member.name
        current = checkout_path(member.name).read_bytes()
        assert blob(current) == effective_applied[member.name], member.name
        data = tar.extractfile(member).read()
        assert len(data) == item['bytes'] and sha(data) == item['sha256'] and blob(data) == item['gitBlobSha1'], member.name
        if item['binary']:
            assert member.name == 'static/videos/fb-video-1593854504-6.mp4' and data[4:8] == b'ftyp'
        else:
            data.decode('utf-8')
        payloads[member.name] = data
        before[member.name] = blob(current)
assert sum(map(len, payloads.values())) == manifest['totalBytes']
selected = runtime if args.runtime_only else list(ALLOWED)
report_path = checkout_path('qa-evidence/current-follow-up-verification.json')

# Kiểm tra xong toàn bộ payload và mọi file gốc trước khi ghi, không động đến helper native riêng.
for name in selected:
    checkout_path(name).write_bytes(payloads[name])
for name in ALLOWED:
    actual = checkout_path(name).read_bytes()
    if name in selected:
        assert sha(actual) == items[name]['sha256'], name
    else:
        assert blob(actual) == before[name], name
report = {
    'status': 'EXACT_CURRENT_FOLLOW_UP_VERIFIED',
    'mode': 'runtime-only' if args.runtime_only else 'all',
    'priorUiMode': ui_proof['mode'],
    'fileCount': len(selected),
    'archiveFileCount': len(ALLOWED),
    'selectedPaths': selected,
    'validatedPaths': list(ALLOWED),
    'excludedPaths': list(EXCLUDED),
    'manifestSha256': MANIFEST_SHA,
    'archiveSha256': manifest['archiveSha256'],
    'baseManifestSha256': BASE_SHA,
    'uiFixManifestSha256': UI_SHA,
    'files': [items[name] for name in selected],
}
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'status': report['status'], 'mode': report['mode'], 'fileCount': len(selected), 'manifestSha256': MANIFEST_SHA}))
