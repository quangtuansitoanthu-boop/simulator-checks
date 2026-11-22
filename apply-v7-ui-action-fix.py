"""CI-only two-file E2E amendment after the immutable V7 source producer."""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile

SHA = lambda data: hashlib.sha256(data).hexdigest()
BLOB = lambda data: hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
APP = Path(sys.argv[1]).resolve(strict=True)
HARNESS = Path(__file__).resolve(strict=True).parent
WORKSPACE = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
assert os.environ.get('GITHUB_ACTIONS') == 'true', 'CI-only amendment; never apply to MAIN'
assert APP == WORKSPACE / 'app' and HARNESS == WORKSPACE / 'harness'
assert not APP.is_symlink() and not HARNESS.is_symlink()
assert subprocess.check_output(['git', '--no-optional-locks', '-C', str(APP), 'rev-parse', 'HEAD'], text=True).strip() == '166d0aeb43341da6f907e096f80a003b49d3be89'
META_RAW = (HARNESS / 'v7-ui-action-fix.json').read_bytes()
assert SHA(META_RAW) == '98b09e1ff9d6c065e6878d6a241595d8d1fcfac44cb62315a9f1fcf9b2e871c9'
META = json.loads(META_RAW)
assert META['readyForPublication'] is True
PRODUCER_RAW = (HARNESS / 'moderation-v7-overlay.json').read_bytes()
assert SHA(PRODUCER_RAW) == META['producerManifestSha256'] == '8d2ab07f5287323dd52a93e69259962c94e7793dd05d8f65956e5dd6df58d9ab'
PRODUCER = json.loads(PRODUCER_RAW)
assert PRODUCER['runtimeFileCount'] == len(PRODUCER['runtimePaths']) == 89
assert META['fileCount'] == len(META['files']) == 2 and META['changedRuntimeFiles'] == 0
assert META['executionSourceFiles'] == META['producerSourceFiles'] == 3292
assert META['unchangedProducerFiles'] == 3290 and META['unchangedRuntimeFiles'] == 89
Q = APP / 'qa-evidence'
assert not Q.is_symlink() and Q.resolve(strict=True).is_relative_to(APP)
OLD_MAP_RAW = (Q / 'moderation-brand-expected.json').read_bytes()
SOURCE_PROOF_RAW = (Q / 'moderation-brand-source-verification.json').read_bytes()
assert SHA(OLD_MAP_RAW) == META['producerExpectedMapSha256'] == 'a29c9ee3088385c054806f46cc3eec85428173c10cd0bcf400845ecb78f9da99'
SOURCE_PROOF = json.loads(SOURCE_PROOF_RAW)
assert SOURCE_PROOF['status'] == 'EXACT_MODERATION_BRAND_SOURCE_VERIFIED'
assert SOURCE_PROOF['manifestSha256'] == SHA(PRODUCER_RAW) and SOURCE_PROOF['archiveSha256'] == META['producerArchiveSha256']
assert SOURCE_PROOF['currentExpectedMapSha256'] == SHA(OLD_MAP_RAW)
assert SOURCE_PROOF['files'] == PRODUCER['files'] and SOURCE_PROOF['fileCount'] == 128
assert SOURCE_PROOF['currentExpectedFileCount'] == 3292 and SOURCE_PROOF['addedFileCount'] == 17
OLD_MAP = json.loads(OLD_MAP_RAW)
assert len(OLD_MAP) == 3292

ALLOWED = {'tests/e2e/admin-community-moderation.spec.ts': [("name: 'Xem nội dung & ngữ cảnh', exact: true",
                                                   'name: /^(?:▼\\s*)?Xem nội dung & ngữ cảnh$/u, exact: true',
                                                   5),
                                                  ("await moderation.getByRole('button', { name: 'Ẩn nội dung vi "
                                                   "phạm', exact: true }).click();",
                                                   "await moderation.getByRole('article', { name: 'Nội dung & ngữ "
                                                   "cảnh', exact: true }).getByRole('button', { name: 'Ẩn nội dung vi "
                                                   "phạm', exact: true }).click();",
                                                   1),
                                                  ("await moderation.getByRole('button', { name: 'Đánh dấu đã xử lý', "
                                                   'exact: true }).click();',
                                                   "await moderation.getByRole('article', { name: 'Nội dung & ngữ "
                                                   "cảnh', exact: true }).getByRole('button', { name: 'Đánh dấu đã xử "
                                                   "lý', exact: true }).click();",
                                                   1)],
 'tests/e2e/mindful-moderation-48h.spec.ts': [("name: 'Xem bài', exact: true",
                                               'name: /^(?:▼\\s*)?Xem bài$/u, exact: true',
                                               2),
                                              ("const remove = moderation.getByRole('button', { name: 'Xóa bài đã "
                                               "đăng', exact: true });",
                                               "const remove = moderation.getByRole('article', { name: 'Nội dung bài "
                                               "chia sẻ', exact: true }).getByRole('button', { name: 'Xóa bài đã "
                                               "đăng', exact: true });",
                                               1)]}

def target(name):
    relative = PurePosixPath(name)
    assert not relative.is_absolute() and '..' not in relative.parts and '\\' not in name
    path = APP.joinpath(*relative.parts)
    assert path.resolve(strict=True).is_relative_to(APP) and path.is_file()
    cursor = path
    while cursor != APP:
        assert not cursor.is_symlink(), name
        cursor = cursor.parent
    return path

# Verify all producer bytes before the first amendment write, including every runtime.
for name, wanted in OLD_MAP.items():
    assert BLOB(target(name).read_bytes()) == wanted, 'Unexpected V7 producer bytes: ' + name
items = {item['path']: item for item in META['files']}
assert len(items) == 2 and set(items) == set(ALLOWED)
archive_path = HARNESS / META['archive']
assert archive_path.resolve(strict=True).parent == HARNESS and not archive_path.is_symlink()
ARCHIVE = archive_path.read_bytes()
assert SHA(ARCHIVE) == META['archiveSha256'] and len(ARCHIVE) == META['archiveBytes']
pending = {}
with tarfile.open(fileobj=io.BytesIO(ARCHIVE)) as archive:
    assert set(archive.getnames()) == set(ALLOWED) and len(archive.getnames()) == 2
    for member in archive:
        assert member.isfile() and not member.issym() and not member.islnk()
        item = items[member.name]
        assert item['operation'] == 'modify' and item['classification'] == 'test' and item['runtime'] is False
        before = target(member.name).read_bytes()
        after = archive.extractfile(member).read()
        rules = ALLOWED[member.name]
        assert item['locatorAmendments'] == [{'before': old, 'after': new, 'count': count} for old, new, count in rules]
        restored = after
        for old, new, count in reversed(rules):
            old = old.encode('utf-8'); new = new.encode('utf-8')
            assert before.count(old) == restored.count(new) == count
            restored = restored.replace(new, old)
        assert restored == before, 'Unexpected non-locator amendment'
        assert SHA(before) == item['beforeSha256'] and len(before) == item['beforeBytes']
        assert BLOB(before) == item['baseGitBlobSha1'] == OLD_MAP[member.name]
        assert SHA(after) == item['sha256'] and BLOB(after) == item['gitBlobSha1'] and len(after) == item['bytes']
        pending[member.name] = after
EXPECTED = dict(OLD_MAP)
for name, data in pending.items():
    EXPECTED[name] = BLOB(data)
assert set(EXPECTED) == set(OLD_MAP) and {name for name in OLD_MAP if EXPECTED[name] != OLD_MAP[name]} == set(ALLOWED)
assert all(EXPECTED[name] == OLD_MAP[name] for name in PRODUCER['runtimePaths'])
EXPECTED_RAW = json.dumps(EXPECTED, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
assert SHA(EXPECTED_RAW) == META['executionExpectedMapSha256']
for filename in ('v7-ui-action-fix-verification.json', 'v7-ui-action-fix-expected.json'):
    assert not (Q / filename).exists() and not (Q / filename).is_symlink()

# Every path, producer, archive, inverse diff and execution map is verified before writing.
for name, data in pending.items():
    target(name).write_bytes(data)
for name, wanted in EXPECTED.items():
    assert BLOB(target(name).read_bytes()) == wanted, 'Post-amendment source differs: ' + name
assert (Q / 'moderation-brand-expected.json').read_bytes() == OLD_MAP_RAW
assert (Q / 'moderation-brand-source-verification.json').read_bytes() == SOURCE_PROOF_RAW
report = {
    'status': 'EXACT_V7_UI_ACTION_TEST_AMENDMENT_VERIFIED',
    'testAmendmentManifestSha256': SHA(META_RAW), 'archiveSha256': SHA(ARCHIVE),
    'producerManifestSha256': SHA(PRODUCER_RAW), 'producerExpectedMapSha256': SHA(OLD_MAP_RAW),
    'producerSourceProofSha256': SHA(SOURCE_PROOF_RAW), 'producerProofsUnchanged': True,
    'producerBytesVerifiedBeforeAmendment': 3292, 'fileCount': 2, 'files': META['files'],
    'executionExpectedMapSha256': SHA(EXPECTED_RAW), 'executionSourceFiles': len(EXPECTED),
    'unchangedProducerFiles': 3290, 'unchangedRuntimeFiles': 89, 'changedRuntimeFiles': 0,
    'sourceMapPhase': META['sourceMapPhase'], 'assertionsUnchangedOutsideTenLocatorAmendments': True,
    'applicationRuntimeChanged': False, 'nativeProductsRebuilt': False,
}
(Q / 'v7-ui-action-fix-expected.json').write_bytes(EXPECTED_RAW)
(Q / 'v7-ui-action-fix-verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({key: value for key, value in report.items() if key != 'files'}))
