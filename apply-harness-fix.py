"""Apply only the reviewed title and nested AX geometry edits in GitHub CI."""
import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

PATH = "ios/App/AppUITests/NativeGuestSmokeTests.swift"
MANIFEST_SHA = "043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7"
BASE_SHA = "98669eca6c455ce7cc4db2b6fb2a8c4968eaad5e14bfe0998ccdfe6a603a3edd"
PAYLOAD_SHA = "318c4d745d076f6641217db78ee011e85af12f9c29a62fa8a2d1fbcbeed51902"
METADATA_SHA = "3ec70fc7f6f435feb6259251c1e315f1b14fb8f270f75bda88d944b0594a7726"
BASE_COMMIT = "166d0aeb43341da6f907e096f80a003b49d3be89"
OLD = 'let title = "Ăn Chay Thanh Tịnh"'.encode("utf-8")
NEW = 'let title = "Lịch ăn chay đủ dinh dưỡng"'.encode("utf-8")

OLD_THEME = b'            let choice = labelledToggle(label, app: app)\n            tap(choice, app: app)\n            waitUntil("Selected theme must become active") { self.checked(choice) == true }'
NEW_THEME = b'            let choice = labelledToggle(label, app: app)\n            revealContentControl(choice, app: app)\n            tap(choice, app: app)\n            waitUntil("Selected theme must become active") { self.checked(self.labelledToggle(label, app: app)) == true }'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--check-only", action="store_true", help="Verify and print only; write no files")
    args = parser.parse_args()
    transfer = Path(__file__).resolve().parent
    root = args.app.resolve(strict=True)
    assert os.environ.get('GITHUB_ACTIONS') == 'true', 'GitHub CI only; never apply to the main local checkout'
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    assert root == workspace / 'app' and transfer == workspace / 'harness' and root != transfer
    git = ['git', '--no-optional-locks', '-C', str(root)]
    assert Path(subprocess.check_output(git + ['rev-parse', '--show-toplevel'], text=True).strip()).resolve() == root
    assert subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip() == BASE_COMMIT
    target = root / PATH
    ancestor = target
    while ancestor != root:
        assert not ancestor.is_symlink(), PATH
        ancestor = ancestor.parent
    if not target.resolve(strict=True).is_relative_to(root):
        raise RuntimeError("UI harness target escapes application checkout")
    metadata_bytes = (transfer / 'ios-harness-fix.json').read_bytes()
    assert digest(metadata_bytes) == METADATA_SHA
    metadata = json.loads(metadata_bytes)
    manifest = (transfer / "mobile-current-manifest.json").read_bytes()
    if digest(manifest) != MANIFEST_SHA:
        raise RuntimeError("Base complete-source manifest mismatch")
    if (metadata["path"] != PATH or metadata["baseSha256"] != BASE_SHA
            or metadata["sha256"] != PAYLOAD_SHA or metadata["bytes"] != 87231):
        raise RuntimeError("Unexpected harness-only payload metadata")
    base = json.loads(manifest)
    assert metadata['schemaVersion'] == 4 and metadata['baseCommit'] == base['baselineCommit'] == BASE_COMMIT
    assert metadata['baseManifestSha256'] == MANIFEST_SHA
    assert metadata['runtimeSourceChanged'] is False
    assert metadata['testBodiesUnchangedExceptReviewedVegetarianAndThemeSegments'] is True
    before = target.read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(before)).encode() + b'\0' + before).hexdigest()
    assert blob == base['expected'][PATH]
    payload = (transfer / "NativeGuestSmokeTests.swift").read_bytes()
    if digest(before) != BASE_SHA:
        raise RuntimeError("UI harness bytes differ from the original full-suite source")
    if digest(payload) != PAYLOAD_SHA or len(payload) != metadata["bytes"]:
        raise RuntimeError("Full UI-test payload SHA or length mismatch")
    marker = b"func testVegetarianMenuTabsAndRecipeRemainReachable()"
    if before.count(OLD) != 1 or payload.count(NEW) != 1:
        raise RuntimeError("Expected one exact title literal in each version")
    if marker not in before or before.index(OLD) < before.index(marker):
        raise RuntimeError("Title correction does not belong to the vegetarian test")
    def split_helper(data):
        start_marker = b'        func contentViewport() -> CGRect? {'
        end_marker = b'        for attempt in 0..<14 {'
        assert data.count(start_marker) == 1
        start = data.index(start_marker); end = data.index(end_marker, start)
        return data[:start], data[start:end], data[end:]
    bp, bh, bs = split_helper(before); ap, ah, suffix = split_helper(payload)
    assert bp == ap and bs.count(OLD_THEME) == suffix.count(NEW_THEME) == 1
    def personal_helper(data, marker):
        assert data.count(marker) == 1
        start = data.index(marker); end = data.index(b'\n    @MainActor\n', start)
        return data[start:end]
    normalized_suffix = bs
    assert [item['name'] for item in metadata['personalHelpers']] == ['allowPersonalNotificationPermission', 'openPersonal']
    for item in metadata['personalHelpers']:
        old_helper = personal_helper(before, item['marker'].encode())
        new_helper = personal_helper(payload, item['marker'].encode())
        assert digest(old_helper) == item['baseSha256'] and digest(new_helper) == item['sha256']
        assert len(old_helper) == item['baseBytes'] and len(new_helper) == item['bytes']
        assert normalized_suffix.count(old_helper) == 1
        normalized_suffix = normalized_suffix.replace(old_helper, new_helper, 1)
        if item['name'] == 'allowPersonalNotificationPermission':
            assert new_helper.count(b'allowExpectedNotificationPermission(app)') == 1
            assert new_helper.index(b'tap(send, app: app)') < new_helper.index(b'allowExpectedNotificationPermission(app)')
        else:
            assert new_helper.index(b'if requestPermission { allowPersonalNotificationPermission(app) }') < new_helper.index(b'let enabled =')
            assertion = b'XCTAssertEqual(checked(enabled), true, "Personal reminders must be enabled for these scenarios")'
            assert old_helper.count(assertion) == new_helper.count(assertion) == 1
    normalized_suffix = normalized_suffix.replace(OLD, NEW, 1).replace(OLD_THEME, NEW_THEME, 1)
    assert normalized_suffix == suffix
    assert digest(bp + normalized_suffix) == metadata['unchangedOutsideReviewedSegmentsSha256']
    assert digest(bh) == metadata['helper']['baseSha256'] and digest(ah) == metadata['helper']['sha256']
    assert len(bh) == metadata['helper']['baseBytes'] and len(ah) == metadata['helper']['bytes']
    assert metadata['helper']['chromeTolerancePoints'] == 2
    assert ah.count(b'let chromeTolerance: CGFloat = 2') == 1 and ah.count(b'.intersection(frame)') == 2
    assert digest(OLD_THEME) == metadata['themeSegment']['baseSha256'] and digest(NEW_THEME) == metadata['themeSegment']['sha256']
    assert ah.count(metadata['helper']['observedHeaderAlias'].encode()) == 1
    assert before.index(OLD_THEME) > before.index(b'func testLightDarkSettingsAndGuestDeepLinkNavigation()')
    assert payload.index(NEW_THEME) > payload.index(b'func testLightDarkSettingsAndGuestDeepLinkNavigation()')
    methods = lambda data: [n.decode() for n in re.findall(rb'^    func (test\w+)\(\)', data, re.M)]
    assert methods(before) == methods(payload) == metadata['testMethods']
    assert len(methods(payload)) == metadata['testMethodCount'] == 13
    assert metadata['allTestMethodAssertionsUnchanged'] is True and metadata['existingPersonalEnabledAssertionRetainedOnce'] is True
    assertions = lambda data: collections.Counter(re.findall(rb'\b(XCTAssert\w*|XCTFail|waitUntil)\s*\(', data))
    assert dict((key.decode(), value) for key, value in assertions(before).items()) == metadata['baseAssertionCallCounts']
    assert dict((key.decode(), value) for key, value in assertions(payload).items()) == metadata['assertionCallCounts']
    assert all(assertions(payload)[key] >= value for key, value in assertions(before).items())
    if not args.check_only:
        target.write_bytes(payload)
        if digest(target.read_bytes()) != PAYLOAD_SHA:
            raise RuntimeError("Written UI harness verification failed")
    result = {"scope": "UI harness only; exact title/theme segments, contentViewport and two personal helpers",
              "path": PATH, "baseManifestSha256": MANIFEST_SHA,
              "baseHarnessSha256": BASE_SHA, "harnessSha256": PAYLOAD_SHA,
              "harnessFixManifestSha256": METADATA_SHA, "testMethodCount": 13,
              "testMethods": methods(payload),
              "unchangedOutsideReviewedSegmentsSha256": metadata['unchangedOutsideReviewedSegmentsSha256'],
              "runtimeSourceChanged": False,
              "mode": "check-only" if args.check_only else "applied"}
    if args.check_only:
        print(json.dumps(result, indent=2))
        return
    evidence = root / "qa-evidence"
    assert evidence.resolve().is_relative_to(root) and not evidence.is_symlink()
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "harness-fix-verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
