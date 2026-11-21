"""Apply only the seven reviewed runtime JSON files to the isolated CI checkout."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile

BASE_SHA = '043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7'
BASE_COMMIT = '166d0aeb43341da6f907e096f80a003b49d3be89'
NAMES = ('recipes-reviewed.json', 'recipes-extra.json', 'recipes-metric.json',
         'recipes-rotation.json', 'regions-existing.json', 'schedule-2026-11-09.json',
         'schedule-2026-11-16.json')
ALLOWED = tuple('docs/plans/vegan-sources/' + name for name in NAMES)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


app = Path(sys.argv[1]).resolve(strict=True)
transfer = Path(__file__).resolve().parent
workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
# This supplement is deliberately restricted to sibling GitHub checkouts.
# It cannot be applied from this artifact folder into the user's main workspace.
assert app == workspace / 'app' and transfer == workspace / 'harness'
assert app != transfer and (app / '.git').exists()
git = ['git', '--no-optional-locks', '-C', str(app)]
assert Path(subprocess.check_output(git + ['rev-parse', '--show-toplevel'], text=True).strip()).resolve() == app
assert subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip() == BASE_COMMIT

manifest_bytes = (transfer / 'runtime-data-manifest.json').read_bytes()
assert digest(manifest_bytes) == os.environ['TUTAM_RUNTIME_MANIFEST_SHA256']
manifest = json.loads(manifest_bytes)
base_bytes = (transfer / 'mobile-current-manifest.json').read_bytes()
assert digest(base_bytes) == BASE_SHA == manifest['baseManifestSha256']
base = json.loads(base_bytes)
assert base['baselineCommit'] == manifest['baselineCommit'] == BASE_COMMIT
assert manifest['baselineRepository'] == base['baselineRepository']
assert manifest['schemaVersion'] == 1 and manifest['fileCount'] == 7
assert not manifest['sensitiveFindings']
assert manifest['archive'] == 'runtime-data.tar.gz'
assert [item['path'] for item in manifest['files']] == list(ALLOWED)
assert manifest['owner']['path'] == 'src/jobs/vegan-week.ts'
owner = (app / manifest['owner']['path']).read_bytes()
assert digest(owner) == manifest['owner']['sha256']
assert blob(owner) == base['expected'][manifest['owner']['path']]
imports = re.findall(r"^import\s+\w+\s+from\s+['\"]\.\./\.\./(docs/plans/vegan-sources/[^'\"]+\.json)['\"]", owner.decode(), re.M)
assert imports == list(ALLOWED)


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


archive = transfer / manifest['archive']
archive_bytes = archive.read_bytes()
assert len(archive_bytes) == manifest['archiveBytes']
assert digest(archive_bytes) == manifest['archiveSha256']
expected = {item['path']: item for item in manifest['files']}
payloads = {}
# Check the complete archive and every existing target before the first write.
with tarfile.open(archive, 'r:gz') as tar:
    assert tar.getnames() == list(ALLOWED)
    for member in tar:
        assert member.isfile() and not member.issym() and not member.islnk()
        item = expected[member.name]
        assert member.size == item['bytes']
        data = tar.extractfile(member).read()
        assert len(data) == item['bytes'] and digest(data) == item['sha256'], member.name
        json.loads(data)
        target = checkout_path(member.name)
        if target.exists():
            current = target.read_bytes()
            assert digest(current) == item['sha256'] or blob(current) == item['baselineBlobSha1'], member.name
        payloads[member.name] = data
assert len(payloads) == 7 and sum(map(len, payloads.values())) == manifest['totalBytes']
for name, data in payloads.items():
    target = checkout_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
for item in manifest['files']:
    assert digest(checkout_path(item['path']).read_bytes()) == item['sha256'], item['path']
report = {'status': 'EXACT_RUNTIME_DATA_VERIFIED', 'files': manifest['files'],
          'fileCount': 7, 'archiveSha256': manifest['archiveSha256'],
          'manifestSha256': digest(manifest_bytes), 'baseManifestSha256': BASE_SHA}
output = checkout_path('qa-evidence/runtime-data-verification.json')
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'status': report['status'], 'fileCount': 7,
                  'archiveSha256': report['archiveSha256'], 'manifestSha256': report['manifestSha256']}))
