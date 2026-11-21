"""Apply the exact user-approved moderation and brand snapshot in isolated GitHub QA only."""
from pathlib import Path,PurePosixPath
import hashlib,io,json,os,subprocess,sys,tarfile

SHA=lambda data:hashlib.sha256(data).hexdigest()
BLOB=lambda data:hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
APP=Path(sys.argv[1]).resolve(strict=True)
HARNESS=Path(__file__).resolve(strict=True).parent
WORKSPACE=Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
assert os.environ.get('GITHUB_ACTIONS')=='true','GitHub CI only'
assert APP==WORKSPACE/'app' and HARNESS==WORKSPACE/'harness'
assert not APP.is_symlink() and not HARNESS.is_symlink()
git=['git','--no-optional-locks','-C',str(APP)]
raw=(HARNESS/'moderation-brand-overlay.json').read_bytes()
assert SHA(raw)=='7d77a93dad3bcae2024f495657a8285e750be58f353daf34672fe1d4601b553b','Moderation/brand manifest mismatch'
manifest=json.loads(raw)
assert manifest['phase']=='final' and manifest['readyForPublication'] is True,'Preliminary snapshot cannot be applied'
assert not manifest['sensitiveFindings']
assert subprocess.check_output(git+['rev-parse','HEAD'],text=True).strip()==manifest['baselineCommit']
assert Path(subprocess.check_output(git+['rev-parse','--show-toplevel'],text=True).strip()).resolve()==APP
base_raw=(HARNESS/'mobile-current-manifest.json').read_bytes()
assert SHA(base_raw)==manifest['baseManifestSha256']=='043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7'
expected=dict(json.loads(base_raw)['expected'])
def harness_archive(source):
 path=HARNESS/source['archive']
 assert path.resolve(strict=True).parent==HARNESS and not path.is_symlink()
 data=path.read_bytes();assert SHA(data)==source['archiveSha256']
 return data
for name,digest in manifest['prerequisiteManifests'].items():
 source_raw=(HARNESS/name).read_bytes();assert SHA(source_raw)==digest
 source=json.loads(source_raw);items={x['path']:x for x in source['files']}
 with tarfile.open(fileobj=io.BytesIO(harness_archive(source))) as tf:
  assert set(tf.getnames())==set(items) and len(tf.getnames())==len(items)
  for member in tf:
   assert member.isfile() and not member.issym() and not member.islnk()
   item=items[member.name];data=tf.extractfile(member).read()
   assert expected[member.name]==item['baseGitBlobSha1']
   assert SHA(data)==item['sha256'] and len(data)==item['bytes']
   assert BLOB(data)==item['gitBlobSha1']
   expected[member.name]=BLOB(data)
assert len(expected)==manifest['baseEffectiveFileCount']==3275
def target(name):
 relative=PurePosixPath(name)
 assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in name
 result=APP.joinpath(*relative.parts)
 assert result.resolve().is_relative_to(APP)
 cursor=result
 while cursor!=APP:
  assert not cursor.is_symlink(),name
  cursor=cursor.parent
 return result
# Verify every pre-existing counted source file, not merely changed targets, before any write.
for name,wanted in expected.items():
 path=target(name);assert path.is_file() and BLOB(path.read_bytes())==wanted,'Unexpected prerequisite source: '+name
items={x['path']:x for x in manifest['files']}
assert len(items)==len(manifest['files'])==manifest['fileCount']
pending={}
with tarfile.open(fileobj=io.BytesIO(harness_archive(manifest))) as tf:
 assert set(tf.getnames())==set(items) and len(tf.getnames())==len(items)
 for member in tf:
  assert member.isfile() and not member.issym() and not member.islnk()
  item=items[member.name];path=target(member.name)
  if item['operation']=='add':
   assert item['baseGitBlobSha1'] is None and member.name not in expected and not path.exists() and not path.is_symlink(),member.name
  else:
   assert item['operation']=='modify' and path.is_file()
   assert expected[member.name]==item['baseGitBlobSha1']==BLOB(path.read_bytes()),member.name
  data=tf.extractfile(member).read()
  assert SHA(data)==item['sha256'] and BLOB(data)==item['gitBlobSha1'] and len(data)==item['bytes']
  pending[member.name]=data
# All path, baseline and archive checks pass before the first application source mutation.
for name,data in pending.items():
 path=target(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
 expected[name]=BLOB(data)
assert len(expected)==manifest['currentExpectedFileCount']
for name,wanted in expected.items():assert BLOB(target(name).read_bytes())==wanted,name
map_raw=json.dumps(expected,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf8')
assert SHA(map_raw)==manifest['currentExpectedMapSha256']
proof=APP/'qa-evidence'
assert not proof.is_symlink() and proof.resolve().is_relative_to(APP)
proof.mkdir(exist_ok=True)
for filename in ('moderation-brand-source-verification.json','moderation-brand-expected.json'):
 assert not (proof/filename).is_symlink()
report={'status':'EXACT_MODERATION_BRAND_SOURCE_VERIFIED','phase':'final','manifestSha256':SHA(raw),
'archiveSha256':manifest['archiveSha256'],'baseEffectiveFileCount':3275,'currentExpectedFileCount':len(expected),
'currentExpectedMapSha256':SHA(map_raw),'fileCount':len(items),'addedFileCount':manifest['addedFileCount'],
'classificationCounts':manifest['classificationCounts'],'files':manifest['files'],
'nativeFixtureOwnership':manifest['nativeFixtureOwnership'],'validation':manifest['validation'],
'finalCandidateForQA':manifest['finalCandidateForQA'],'productionWrites':False}
(proof/'moderation-brand-source-verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
(proof/'moderation-brand-expected.json').write_bytes(map_raw)
print(json.dumps({key:value for key,value in report.items() if key!='files'}))
