import hashlib, json, os, re, sys, tarfile
from pathlib import Path, PurePosixPath

app = Path(sys.argv[1]).resolve()
transfer = Path(__file__).resolve().parent
assert os.environ.get('GITHUB_ACTIONS') == 'true'
assert app == Path(os.environ['GITHUB_WORKSPACE']).resolve() / 'app'
assert transfer == app.parent / 'harness'
sha = lambda data: hashlib.sha256(data).hexdigest()
blob = lambda data: hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
manifest_bytes = (transfer / 'android-test-fix.json').read_bytes()
assert sha(manifest_bytes) == 'cc4793ef30879588221df90ace51a50457772617d4eccb59c1e4878083b476b1'
manifest = json.loads(manifest_bytes)
base_bytes = (transfer / 'mobile-current-manifest.json').read_bytes()
assert sha(base_bytes) == manifest['baseManifestSha256'] == '043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7'
base = json.loads(base_bytes)
allowed = {'android/app/src/androidTest/java/vn/quangtu/tutam/audio/DharmaAudioWidgetInitialLayoutTest.java', 'android/app/src/androidTest/java/vn/quangtu/tutam/widgets/CalendarWidgetRenderingTest.java'}
items = {item['path']: item for item in manifest['files']}
assert set(items) == allowed and len(manifest['files']) == 2
archive = transfer / 'android-test-fix.tar.gz'
assert sha(archive.read_bytes()) == manifest['archiveSha256']
payloads = {}
with tarfile.open(archive) as tar:
    assert set(tar.getnames()) == allowed and len(tar.getnames()) == 2
    for member in tar:
        assert member.isfile() and not member.issym() and not member.islnk()
        relative = PurePosixPath(member.name)
        assert not relative.is_absolute() and '..' not in relative.parts
        target = app.joinpath(*relative.parts)
        assert target.resolve().is_relative_to(app)
        item = items[member.name]
        before = target.read_bytes()
        assert blob(before) == item['baseGitBlobSha1'] == base['expected'][member.name]
        data = tar.extractfile(member).read()
        assert len(data) == item['bytes'] and sha(data) == item['sha256']
        assert len(re.findall(rb'@Test\b', before)) == len(re.findall(rb'@Test\b', data)) == item['testMethods']
        payloads[target] = data
assert sum(item['testMethods'] for item in items.values()) == 13
for target, data in payloads.items(): target.write_bytes(data)
evidence = app / 'qa-evidence'
evidence.mkdir(exist_ok=True)
report = {'scope': 'Test fixture locale only; application source unchanged', 'manifestSha256': sha(manifest_bytes), 'files': manifest['files'], 'testMethodsInChangedFiles': 13, 'requiredInstrumentationMethods': 57}
(evidence / 'android-harness-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report))
