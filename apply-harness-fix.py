"""Apply only the exact stale vegetarian-title correction to the UI-test harness."""
import argparse
import hashlib
import json
from pathlib import Path

PATH = "ios/App/AppUITests/NativeGuestSmokeTests.swift"
MANIFEST_SHA = "043ca01be793d63f7224e8fbb72871c422b683cc879ea3f0a92087e2acbacfe7"
BASE_SHA = "98669eca6c455ce7cc4db2b6fb2a8c4968eaad5e14bfe0998ccdfe6a603a3edd"
PAYLOAD_SHA = "929c44ca1692211ccdda79ae9317a396484c77dea61b0b0b83d436546821b589"
OLD = 'let title = "Ăn Chay Thanh Tịnh"'.encode("utf-8")
NEW = 'let title = "Lịch ăn chay đủ dinh dưỡng"'.encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    transfer = Path(__file__).resolve().parent
    root = args.app.resolve(strict=True)
    target = root / PATH
    if not target.resolve(strict=True).is_relative_to(root):
        raise RuntimeError("UI harness target escapes application checkout")
    metadata = json.loads((transfer / "ios-harness-fix.json").read_text(encoding="utf-8"))
    manifest = (transfer / "mobile-current-manifest.json").read_bytes()
    if digest(manifest) != MANIFEST_SHA:
        raise RuntimeError("Base complete-source manifest mismatch")
    if (metadata["path"] != PATH or metadata["baseSha256"] != BASE_SHA
            or metadata["sha256"] != PAYLOAD_SHA or metadata["bytes"] != 86188):
        raise RuntimeError("Unexpected harness-only payload metadata")
    before = target.read_bytes()
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
    if before.replace(OLD, NEW, 1) != payload:
        raise RuntimeError("Payload contains changes beyond the one-line test-title correction")
    if not args.check_only:
        target.write_bytes(payload)
        if digest(target.read_bytes()) != PAYLOAD_SHA:
            raise RuntimeError("Written UI harness verification failed")
    result = {"scope": "UI harness only; exact single title-literal replacement",
              "path": PATH, "baseManifestSha256": MANIFEST_SHA,
              "baseHarnessSha256": BASE_SHA, "harnessSha256": PAYLOAD_SHA,
              "runtimeSourceChanged": False,
              "mode": "check-only" if args.check_only else "applied"}
    evidence = root / "qa-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "harness-fix-verification.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
