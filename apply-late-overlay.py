"""Apply the exact seven reviewed late changes only inside an isolated GitHub app checkout."""
import hashlib,json,os,sys,tarfile
from pathlib import Path,PurePosixPath

APP=Path(sys.argv[1]).resolve(strict=True)
HARNESS=Path(__file__).resolve().parent
assert os.environ.get('GITHUB_ACTIONS')=='true','CI only'
assert APP==Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)/'app'
assert HARNESS==APP.parent/'harness'
sha=lambda data:hashlib.sha256(data).hexdigest()
blob=lambda data:hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
MANIFEST_SHA='934ffb0237675171025c5c9e48323043f68573e4e7cd706f9fc658d80d925ccd'
raw=(HARNESS/'late-overlay.json').read_bytes()
assert sha(raw)==MANIFEST_SHA,'Late overlay manifest mismatch'
manifest=json.loads(raw)
base_raw=(HARNESS/'mobile-current-manifest.json').read_bytes()
assert sha(base_raw)==manifest['baseManifestSha256']=='043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7'
expected=dict(json.loads(base_raw)['expected'])
# Reconstruct the exact source after every prerequisite overlay; verify every old/new blob.
for name,digest in manifest['prerequisiteManifests'].items():
    source_raw=(HARNESS/name).read_bytes();assert sha(source_raw)==digest
    source=json.loads(source_raw);archive_path=HARNESS/source['archive']
    assert archive_path.resolve().parent==HARNESS
    assert sha(archive_path.read_bytes())==source['archiveSha256']
    entries={item['path']:item for item in source['files']}
    with tarfile.open(archive_path) as archive:
        assert set(archive.getnames())==set(entries) and len(archive.getnames())==len(entries)
        for member in archive:
            assert member.isfile() and not member.issym() and not member.islnk()
            item=entries[member.name];data=archive.extractfile(member).read()
            assert sha(data)==item['sha256']
            assert expected[member.name]==item['baseGitBlobSha1'],'Prerequisite chain mismatch'
            expected[member.name]=blob(data)
allowed={'tests/e2e/copying-layout.spec.ts', 'src/lib/i18n/content-experience-notice.ts', 'tests/e2e/mobile-talk-video-audio.spec.ts', 'src/lib/data/offline-album-plays.json', 'tests/unit/content-experience-notice.test.ts', 'src/lib/components/practice/name-copying/NameCopying.svelte', 'tests/e2e/meditation-completion.spec.ts'}
items={item['path']:item for item in manifest['files']}
assert set(items)==allowed and len(manifest['files'])==7 and manifest['runtimeFileCount']==3
archive_path=HARNESS/manifest['archive']
assert archive_path.resolve().parent==HARNESS and sha(archive_path.read_bytes())==manifest['archiveSha256']
pending={}
with tarfile.open(archive_path) as archive:
    assert set(archive.getnames())==allowed and len(archive.getnames())==7
    for member in archive:
        relative=PurePosixPath(member.name)
        assert member.isfile() and not member.issym() and not member.islnk()
        assert not relative.is_absolute() and '..' not in relative.parts
        target=APP.joinpath(*relative.parts)
        assert target.resolve().is_relative_to(APP) and target.is_file() and not target.is_symlink()
        item=items[member.name]
        before=target.read_bytes()
        assert blob(before)==item['baseGitBlobSha1']==expected[member.name],'Unexpected existing source: '+member.name
        data=archive.extractfile(member).read()
        assert sha(data)==item['sha256'] and blob(data)==item['gitBlobSha1'] and len(data)==item['bytes']
        pending[target]=data
# No file is changed unless all paths, baseline blobs and new payloads passed.
assert len(pending)==7
for target,data in pending.items():target.write_bytes(data)
for target,data in pending.items():assert target.read_bytes()==data
proof=APP/'qa-evidence';proof.mkdir(exist_ok=True)
report={'status':'EXACT_LATE_OVERLAY_VERIFIED','manifestSha256':sha(raw),'prerequisiteManifests':manifest['prerequisiteManifests'],'fileCount':7,'runtimeFileCount':3,'files':manifest['files']}
(proof/'late-overlay-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
print(json.dumps(report))
