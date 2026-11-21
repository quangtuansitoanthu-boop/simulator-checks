"""Copy two text-contract inputs into app/, never execute either configuration."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

BASE_SHA = '043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7'
RUNTIME_SHA = 'cd55b379c0dc3468cc516a0c45034300e49cac2c1cd769e17eb7f98d94ccafd7'
BASE_COMMIT = '166d0aeb43341da6f907e096f80a003b49d3be89'
ALLOWED = ('.codex/environments/environment.toml', '.github/workflows/deploy-cloudflare-worker.yml')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


app = Path(sys.argv[1]).resolve(strict=True)
transfer = Path(__file__).resolve().parent
workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
assert app == workspace / 'app' and transfer == workspace / 'harness'
assert app != transfer and (app / '.git').exists()
git = ['git', '--no-optional-locks', '-C', str(app)]
assert Path(subprocess.check_output(git + ['rev-parse', '--show-toplevel'], text=True).strip()).resolve() == app
assert subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip() == BASE_COMMIT


def checkout_path(name):
    relative = PurePosixPath(name)
    assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in name
    path = app.joinpath(*relative.parts)
    assert path.resolve().is_relative_to(app)
    ancestor = path
    while ancestor != app:
        assert not ancestor.is_symlink(), name
        ancestor = ancestor.parent
    return path


manifest_bytes = (transfer / 'test-support-manifest.json').read_bytes()
assert digest(manifest_bytes) == os.environ['TUTAM_TEST_SUPPORT_MANIFEST_SHA256']
manifest = json.loads(manifest_bytes)
base_bytes = (transfer / 'mobile-current-manifest.json').read_bytes()
runtime_bytes = (transfer / 'runtime-data-manifest.json').read_bytes()
assert digest(base_bytes) == BASE_SHA == manifest['baseManifestSha256']
assert digest(runtime_bytes) == RUNTIME_SHA == manifest['runtimeManifestSha256']
base, runtime = json.loads(base_bytes), json.loads(runtime_bytes)
assert base['baselineCommit'] == manifest['baselineCommit'] == BASE_COMMIT
assert manifest['baselineRepository'] == base['baselineRepository']
assert manifest['schemaVersion'] == 1 and manifest['fileCount'] == 2
assert manifest['archive'] == 'test-support.tar.gz' and not manifest['sensitiveFindings']
assert [item['path'] for item in manifest['files']] == list(ALLOWED)
for item in runtime['files']:
    assert digest(checkout_path(item['path']).read_bytes()) == item['sha256'], item['path']
for item in manifest['consumers']:
    data = checkout_path(item['path']).read_bytes()
    assert digest(data) == item['sha256'] and blob(data) == base['expected'][item['path']], item['path']

archive_bytes = (transfer / manifest['archive']).read_bytes()
assert len(archive_bytes) == manifest['archiveBytes']
assert digest(archive_bytes) == manifest['archiveSha256']
expected = {item['path']: item for item in manifest['files']}
payloads = {}
with tarfile.open(transfer / manifest['archive'], 'r:gz') as tar:
    assert tar.getnames() == list(ALLOWED)
    for member in tar:
        assert member.isfile() and not member.issym() and not member.islnk()
        item = expected[member.name]
        assert member.size == item['bytes']
        data = tar.extractfile(member).read()
        assert len(data) == item['bytes'] and digest(data) == item['sha256'], member.name
        target = checkout_path(member.name)
        if target.exists():
            current = target.read_bytes()
            assert digest(current) == item['sha256'] or blob(current) == item['baselineBlobSha1'], member.name
        payloads[member.name] = data
# Both files are fully verified before writing. app/.github is deliberately
# nested beneath the checkout; it is never the harness workflow directory.
assert len(payloads) == 2 and sum(map(len, payloads.values())) == manifest['totalBytes']
for name, data in payloads.items():
    target = checkout_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
for item in manifest['files']:
    assert digest(checkout_path(item['path']).read_bytes()) == item['sha256'], item['path']
report = {'status': 'EXACT_TEST_SUPPORT_VERIFIED', 'fileCount': 2, 'files': manifest['files'],
          'manifestSha256': digest(manifest_bytes), 'archiveSha256': manifest['archiveSha256'],
          'baseManifestSha256': BASE_SHA, 'runtimeManifestSha256': RUNTIME_SHA,
          'executedConfiguration': False}
output = checkout_path('qa-evidence/test-support-verification.json')
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'status': report['status'], 'fileCount': 2, 'manifestSha256': report['manifestSha256']}))
