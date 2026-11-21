import hashlib, json, subprocess, sys, tarfile
from pathlib import Path, PurePosixPath

app = Path(sys.argv[1]).resolve()
transfer = Path(__file__).resolve().parent
manifest = json.loads((transfer / 'mobile-current-manifest.json').read_text(encoding='utf-8'))
def digest(data): return hashlib.sha256(data).hexdigest()
def blob(data): return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
def target(name):
    relative = PurePosixPath(name)
    assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in name
    path = app.joinpath(*relative.parts)
    assert path.resolve().is_relative_to(app)
    return path
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=app).decode().strip() == manifest['baselineCommit']
assert not manifest['sensitiveFindings']
archive = transfer / manifest['archive']
assert digest(archive.read_bytes()) == manifest['archiveSha256']
for item in manifest['changes']:
    path = target(item['path'])
    if item['baselineBlobSha1'] is None: assert not path.exists(), item['path']
    else: assert blob(path.read_bytes()) == item['baselineBlobSha1'], item['path']
with tarfile.open(archive) as tar:
    expected = {x['path']: x for x in manifest['changes'] if x['operation'] != 'delete'}
    assert sorted(tar.getnames()) == sorted(expected)
    for member in tar:
        assert member.isfile() and not member.issym() and not member.islnk()
        data = tar.extractfile(member).read()
        item = expected[member.name]
        assert len(data) == item['bytes'] and digest(data) == item['sha256'], member.name
        path = target(member.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for item in manifest['changes']:
        if item['operation'] == 'delete': target(item['path']).unlink()
for name, expected in manifest['expected'].items():
    assert blob(target(name).read_bytes()) == expected, name
print(json.dumps({'status': 'EXACT_CURRENT_SOURCE_VERIFIED', 'files': len(manifest['expected']), 'changes': len(manifest['changes']), 'archiveSha256': manifest['archiveSha256']}))
