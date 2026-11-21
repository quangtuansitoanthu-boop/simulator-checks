"""Run every native iOS case in one group against matching, prebuilt products."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

BUNDLE = "vn.quangtu.tutam"
GROUPS = {
    "smoke": [
        "NativeGuestSmokeTests/testQuoteImageUsesNativeSavePickerAndCancelKeepsDialogUsable",
        "NativeGuestSmokeTests/testRosaryBackPreservesCountInPortraitAndLandscape",
        "NativeGuestSmokeTests/testMotionSettingPersistsAcrossNativeDeepLinks",
        "NativeGuestSmokeTests/testExistingNotificationButtonDeliversToSystemNotificationCenter",
    ],
    "extended": [
        "NativeExtendedGuestTests/testNativeScriptureCopyingReferenceAndGhost",
        "NativeExtendedGuestTests/testOfflineLibrarySearchChapterReadingAndLargeTextRotation",
        "NativeExtendedGuestTests/testNativeCatalogExcludesWebOnlyBookAndSearchCanBeCleared",
        "NativeExtendedGuestTests/testLightDarkSettingsAndGuestDeepLinkNavigation",
        "NativeExtendedGuestTests/testMeditationPauseResumeSurvivesNativeBackgrounding",
    ],
    "personal": [
        "NativePersonalReminderTests/testPersonalLunarSolarCrudInvalidDateCancelAndPersistence",
        "NativePersonalReminderTests/testPersonalNearFutureReminderDeliversToSystemAndCanBeDeleted",
    ],
    "snapshot": [
        "NativeSimulatorSnapshotTests/testRosaryManualAndAutomaticAudioProgress",
        "NativeSimulatorSnapshotTests/testVegetarianMenuTabsAndRecipeRemainReachable",
    ],
}
COUNTS = ("passedTests", "failedTests", "skippedTests", "totalTestCount")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def execute(command, timeout=45, **kwargs):
    return subprocess.run(command, timeout=timeout, check=False, **kwargs)


def text_command(command, timeout=45):
    return execute(command, timeout, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")


def passed(record):
    summary = record.get("summary", {})
    return (record.get("freshInstallCompleted") is True
            and record.get("completed") is True
            and record.get("xcodeExitCode") == 0
            and not record.get("infrastructureError")
            and all(type(summary.get(k)) is int for k in COUNTS)
            and summary["passedTests"] == summary["totalTestCount"] == 1
            and summary["failedTests"] == summary["skippedTests"] == 0)


def collect(args, index, identifier, record):
    case_dir = args.evidence / f"case-{index:02d}"
    case_dir.mkdir(parents=True, exist_ok=True)
    result = case_dir / "NativeCase.xcresult"
    warnings = list(record.get("evidenceWarnings", []))
    if result.is_dir():
        try:
            response = text_command([
                "xcrun", "xcresulttool", "get", "test-results", "summary",
                "--path", str(result)], timeout=60)
            if response.returncode:
                raise ValueError(response.stderr[-2000:])
            summary = json.loads(response.stdout)
            if not isinstance(summary, dict):
                raise ValueError("XCResult summary is not an object")
            record["summary"] = summary
            write_json(case_dir / "summary.json", summary)
            if any(type(summary.get(k)) is not int for k in COUNTS):
                raise ValueError("XCResult has missing or invalid test counts")
            if summary["totalTestCount"] != 1:
                raise ValueError("Each case must execute exactly one test")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            record["infrastructureError"] = (
                record.get("infrastructureError", "") + "; summary: " + str(error))
        attachments = case_dir / "attachments"
        if not attachments.exists():
            try:
                exported = text_command([
                    "xcrun", "xcresulttool", "export", "attachments",
                    "--path", str(result), "--output-path", str(attachments)], timeout=90)
                if exported.returncode:
                    warnings.append("attachment-export: " + exported.stderr[-2000:])
            except (OSError, subprocess.SubprocessError) as error:
                warnings.append("attachment-export: " + str(error))
    else:
        record["infrastructureError"] = record.get(
            "infrastructureError", "") + "; missing XCResult"
    screenshot = case_dir / "simulator-final.png"
    if not screenshot.exists() and args.simulator:
        try:
            capture = text_command([
                "xcrun", "simctl", "io", args.simulator, "screenshot",
                str(screenshot)], timeout=30)
            if capture.returncode:
                warnings.append("screenshot: " + capture.stderr[-1000:])
        except (OSError, subprocess.SubprocessError) as error:
            warnings.append("screenshot: " + str(error))
    record.update(case=index, identifier=identifier, evidenceWarnings=warnings)
    write_json(case_dir / "record.json", record)
    return record


def run_case(args, index, identifier, xctestrun, app):
    case_dir = args.evidence / f"case-{index:02d}"
    case_dir.mkdir(parents=True, exist_ok=True)
    record_path = case_dir / "record.json"
    result = case_dir / "NativeCase.xcresult"
    record = {"case": index, "identifier": identifier,
              "freshInstallCompleted": False, "completed": False,
              "xcodeExitCode": None, "startedAt": time.time()}
    try:
        if result.exists() or record_path.exists():
            raise RuntimeError("Refusing to reuse previous case evidence")
        write_json(record_path, record)
        installed = text_command([
            "xcrun", "simctl", "get_app_container", args.simulator, BUNDLE, "app"])
        if installed.returncode == 0:
            text_command(["xcrun", "simctl", "terminate", args.simulator, BUNDLE])
            removed = text_command([
                "xcrun", "simctl", "uninstall", args.simulator, BUNDLE], timeout=60)
            if removed.returncode:
                raise RuntimeError("Could not remove the previous isolated CI install")
        install = text_command([
            "xcrun", "simctl", "install", args.simulator, str(app)], timeout=90)
        if install.returncode:
            raise RuntimeError("Fresh simulator install failed: " + install.stderr[-2000:])
        record["freshInstallCompleted"] = True
        command = [
            "xcodebuild", "test-without-building", "-xctestrun", str(xctestrun),
            "-destination", f"platform=iOS Simulator,id={args.simulator}",
            "-resultBundlePath", str(result),
            f"-only-testing:AppUITests/{identifier}",
            "-parallel-testing-enabled", "NO",
            "-test-timeouts-enabled", "YES",
            "-default-test-execution-time-allowance", "750",
            "-maximum-test-execution-time-allowance", "750",
            "CODE_SIGNING_ALLOWED=NO",
        ]
        write_json(case_dir / "command.json", command)
        print(f"::group::{args.group} {index}/{len(GROUPS[args.group])}: {identifier}", flush=True)
        with (case_dir / "native-test.log").open("w", encoding="utf-8") as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                record["xcodeExitCode"] = process.wait(timeout=750)
                record["completed"] = True
            except subprocess.TimeoutExpired:
                record["infrastructureError"] = "750-second wall-clock watchdog expired"
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                record["xcodeExitCode"] = process.returncode
        print("::endgroup::", flush=True)
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        record["infrastructureError"] = str(error)
    finally:
        record["finishedAt"] = time.time()
        write_json(record_path, record)
    return collect(args, index, identifier, record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, choices=GROUPS)
    parser.add_argument("--simulator", default=os.environ.get("TUTAM_QA_SIMULATOR", ""))
    parser.add_argument("--products", type=Path, default=Path("build/ios-simulator/Build/Products"))
    parser.add_argument("--evidence", type=Path, default=Path("build/release-evidence"))
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args()
    args.products, args.evidence = args.products.resolve(), args.evidence.resolve()
    args.evidence.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        if not args.collect_only:
            if (os.environ.get("GITHUB_ACTIONS") != "true"
                    or os.environ.get("TUTAM_QA_HOSTS_ISOLATED") != "1"
                    or not args.simulator):
                raise RuntimeError("Fresh-install execution requires isolated GitHub CI")
            runs = list(args.products.glob("*.xctestrun"))
            app = args.products / "Debug-iphonesimulator/App.app"
            if len(runs) != 1 or not app.is_dir():
                raise RuntimeError("Expected one matching xctestrun and prebuilt simulator app")
        for index, identifier in enumerate(GROUPS[args.group], 1):
            if args.collect_only:
                path = args.evidence / f"case-{index:02d}/record.json"
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    record = {"completed": False, "infrastructureError": "Missing case record"}
                records.append(collect(args, index, identifier, record))
            else:
                records.append(run_case(args, index, identifier, runs[0], app))
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        write_json(args.evidence / "group-infrastructure-error.json", {"error": str(error)})
    totals = {key: sum(r.get("summary", {}).get(key, 0)
                      for r in records if type(r.get("summary", {}).get(key)) is int)
              for key in COUNTS}
    expected = len(GROUPS[args.group])
    success = len(records) == expected and all(passed(record) for record in records)
    summary = {"ios": os.environ.get("IOS_RUNTIME"), "group": args.group,
               "sourceManifestSha256": os.environ.get("MOBILE_SOURCE_MANIFEST_SHA256"),
               "workflowRunId": os.environ.get("GITHUB_RUN_ID"),
               "workflowRunAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
               "expectedTests": expected, **totals, "completeAndPassed": success,
               "records": records}
    write_json(args.evidence / "native-ui-summary.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "records"}), flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
