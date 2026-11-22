"""Run every native iOS case in one group against matching, prebuilt products."""
import argparse
import hashlib
import datetime
import re
import selectors
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
GROUPS["rosary-focus"] = [
    "NativeGuestSmokeTests/testRosaryBackPreservesCountInPortraitAndLandscape",
]
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


def collect_qa_events(args, case_dir, record):
    """Snapshot this install's existing sanitized log before the next uninstall."""
    if record.get("freshInstallCompleted") is not True:
        record["qaEvents"] = {"status": "not-installed"}
        return
    warnings = list(record.get("evidenceWarnings", []))
    try:
        if (os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("TUTAM_QA_HOSTS_ISOLATED") != "1"
                or not args.simulator):
            raise RuntimeError("QA event collection requires isolated GitHub CI")
        container = text_command([
            "xcrun", "simctl", "get_app_container", args.simulator, BUNDLE, "data"])
        if container.returncode:
            raise RuntimeError("Current QA app data container is unavailable")
        root = Path(container.stdout.strip())
        if not root.is_absolute() or not root.is_dir():
            raise RuntimeError("Invalid QA app data container")
        root = root.resolve(strict=True)
        source = root / "Library/Caches/tutam-qa-events.jsonl"
        if source.is_symlink() or not source.resolve(strict=True).is_relative_to(root):
            raise RuntimeError("QA event file escapes the current app container")
        if not source.is_file() or source.stat().st_size > 256 * 1024:
            raise RuntimeError("QA event file is absent or exceeds its writer's cap")
        payload = source.read_bytes()
        if len(payload) > 256 * 1024:
            raise RuntimeError("QA event snapshot exceeds its writer's cap")
        lines = payload.decode("utf-8").splitlines()
        if any(not isinstance(json.loads(line), dict) for line in lines):
            raise RuntimeError("QA event snapshot must contain JSON objects")
        destination = case_dir / "qa-events.jsonl"
        destination.write_bytes(payload)
        record["qaEvents"] = {
            "status": "copied", "file": destination.name, "bytes": len(payload),
            "events": len(lines), "sha256": hashlib.sha256(payload).hexdigest(),
            "source": "current-install Library/Caches/tutam-qa-events.jsonl",
        }
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        # Missing diagnostics must not replace the real XCTest result or retry it.
        record["qaEvents"] = {"status": "unavailable"}
        warnings.append("qa-events: " + str(error))
    record["evidenceWarnings"] = warnings


# BEGIN CI_ONLY_NOTIFICATION_LOG_DIAGNOSTIC

def collect_notification_os_log(args, case_dir, record):
    """Read this synthetic guest case's OS log without changing XCTest or permissions."""
    if os.environ.get("TUTAM_QA_NOTIFICATION_LOGS") != "1":
        return
    evidence = {"status": "unavailable", "diagnosticOnly": True,
                "rawMessagesSaved": False, "byteLimit": 256 * 1024,
                "queryTimeoutSeconds": 45}
    warnings = list(record.get("evidenceWarnings", []))
    process = None
    selector = None
    try:
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", "")).resolve(strict=True)
        expected_case = "NativePersonalReminderTests/testPersonalLunarSolarCrudInvalidDateCancelAndPersistence"
        if (os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("GITHUB_REPOSITORY") != "quangtuansitoanthu-boop/simulator-checks"
                or os.environ.get("TUTAM_QA_HOSTS_ISOLATED") != "1"
                or os.environ.get("IOS_RUNTIME") != "27.0"
                or record.get("identifier") != expected_case
                or Path.cwd().resolve() != workspace / "app"
                or not re.fullmatch(r"[0-9A-Fa-f-]{36}", args.simulator)
                or not case_dir.resolve(strict=True).is_relative_to(workspace / "app/build/release-evidence")):
            raise RuntimeError("scope-guard")
        if record.get("freshInstallCompleted") is not True:
            evidence["status"] = "not-installed"
            return
        started, finished = record["startedAt"], record["finishedAt"]
        if not (isinstance(started, (float, int)) and isinstance(finished, (float, int))
                and 0 <= finished - started <= 1000):
            raise RuntimeError("invalid-case-window")
        # log show uses the runner's local wall clock; both boundaries come from this case only.
        start_text = datetime.datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S")
        end_text = datetime.datetime.fromtimestamp(finished).strftime("%Y-%m-%d %H:%M:%S")
        predicate = '(process == "App" OR process == "usernotificationsd") AND (eventMessage CONTAINS[c] "notification" OR eventMessage CONTAINS[c] "authorization" OR eventMessage CONTAINS[c] "permission")'
        command = ["xcrun", "simctl", "spawn", args.simulator, "log", "show",
                   "--style", "ndjson", "--start", start_text, "--end", end_text,
                   "--info", "--debug", "--predicate", predicate]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 45
        chunks, received = [], 0
        timed_out, truncated = False, False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(timeout=min(remaining, 0.2))
            if not events:
                if process.poll() is not None:
                    break
                continue
            data = os.read(process.stdout.fileno(), min(8192, 256 * 1024 - received + 1))
            if not data:
                break
            available = 256 * 1024 - received
            chunks.append(data[:available])
            received += min(len(data), available)
            if len(data) > available:
                truncated = True
                break
        if timed_out or truncated:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        else:
            process.wait(timeout=max(0.1, min(5, deadline - time.monotonic())))
        # Never persist raw eventMessage, process paths, bundle lists, payloads, URLs or stderr.
        # Only fixed operation/state vocabulary and numeric error codes leave this process.
        operations = ["LocalNotifications", "checkPermissions", "requestPermissions",
                      "getNotificationSettings", "requestAuthorization", "authorization",
                      "notificationSettings", "permission", "schedule", "UNUserNotificationCenter",
                      "connection", "XPC", "completion", "callback", "error", "timeout"]
        states = ["authorized", "unauthorized", "denied", "notDetermined", "granted",
                  "provisional", "ephemeral", "pending", "requested", "requesting",
                  "completed", "failed", "enabled", "disabled", "invalidated", "interrupted"]
        rows, unparsed = [], 0
        for line in b"".join(chunks).decode("utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                unparsed += 1
                continue
            if not isinstance(item, dict):
                unparsed += 1
                continue
            name = Path(str(item.get("processImagePath", ""))).name
            if name not in ("App", "usernotificationsd"):
                continue
            message = item.get("eventMessage", "")
            timestamp = item.get("timestamp", "")
            if not isinstance(message, str) or not isinstance(timestamp, str):
                continue
            if not re.fullmatch(r"[0-9 T:.+Z-]{10,40}", timestamp):
                timestamp = "redacted"
            found = [word for word in operations if word.casefold() in message.casefold()]
            state_tokens = [word for word in states if re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", message, re.I)]
            row = {"timestamp": timestamp, "process": name, "messageOperations": found,
                   "messageStates": state_tokens, "containsPrivacyMarker": "<private>" in message,
                   "matchesGuestBundle": BUNDLE in message,
                   "errorCodes": re.findall(r"\berror(?:\s*code)?\s*[:= ]\s*(-?\d{1,4})\b", message, re.I)[:8]}
            rows.append(row)
        payload = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8") for row in rows)
        if len(payload) > 256 * 1024:
            raise RuntimeError("sanitized-output-limit")
        destination = case_dir / "notification-os-log.sanitized.jsonl"
        if destination.exists() or destination.is_symlink():
            raise RuntimeError("refusing-existing-diagnostic")
        destination.write_bytes(payload)
        status = "collected" if process.returncode == 0 and not timed_out and not truncated else "partial" if rows else "unavailable"
        evidence.update({"status": status, "file": destination.name, "bytes": len(payload),
                         "sha256": hashlib.sha256(payload).hexdigest(), "events": len(rows),
                         "queryExitCode": process.returncode, "timedOut": timed_out,
                         "truncated": truncated, "readBytes": received, "unparsedLines": unparsed,
                         "caseStartedAt": started, "caseFinishedAt": finished,
                         "processes": ["App", "usernotificationsd"],
                         "messagePolicy": "fixed operation/state vocabulary only; no raw free text",
                         "absenceDoesNotProveCallWasNotMade": True})
        if status != "collected":
            warnings.append("notification-os-log: " + status)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        evidence["errorType"] = type(error).__name__
        warnings.append("notification-os-log: unavailable")
    finally:
        try:
            if selector is not None:
                selector.close()
            if process is not None:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
        except (OSError, subprocess.SubprocessError):
            evidence["cleanupWarning"] = True
        record["notificationOSLog"] = evidence
        record["evidenceWarnings"] = warnings

# END CI_ONLY_NOTIFICATION_LOG_DIAGNOSTIC


# BEGIN CI_ONLY_COPY_LIFECYCLE_LOG_DIAGNOSTIC

def collect_copy_lifecycle_os_log(args, case_dir, record):
    """Capture bounded, sanitized OS lifecycle evidence for the isolated Copy27 case."""
    if os.environ.get("TUTAM_QA_COPY_LIFECYCLE_LOGS") != "1":
        return
    evidence = {"status": "unavailable", "diagnosticOnly": True,
                "rawMessagesSaved": False, "byteLimit": 256 * 1024,
                "queryTimeoutSeconds": 45}
    warnings = list(record.get("evidenceWarnings", []))
    process = None
    selector = None
    try:
        workspace = Path(os.environ.get("GITHUB_WORKSPACE", "")).resolve(strict=True)
        expected_case = "NativeExtendedGuestTests/testNativeScriptureCopyingReferenceAndGhost"
        if (os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("GITHUB_REPOSITORY") != "quangtuansitoanthu-boop/simulator-checks"
                or os.environ.get("TUTAM_QA_HOSTS_ISOLATED") != "1"
                or os.environ.get("IOS_RUNTIME") != "27.0"
                or record.get("identifier") != expected_case
                or Path.cwd().resolve() != workspace / "app"
                or not re.fullmatch(r"[0-9A-Fa-f-]{36}", args.simulator)
                or not case_dir.resolve(strict=True).is_relative_to(workspace / "app/build/release-evidence")):
            raise RuntimeError("scope-guard")
        if record.get("freshInstallCompleted") is not True:
            evidence["status"] = "not-installed"
            return
        started, finished = record["startedAt"], record["finishedAt"]
        if not (isinstance(started, (float, int)) and isinstance(finished, (float, int))
                and 0 <= finished - started <= 1000):
            raise RuntimeError("invalid-case-window")
        # The existing attachment export runs before this diagnostic. Anchor at the
        # actual pre-open snapshot so test-host startup cannot exhaust the log cap.
        manifest_path = case_dir / "attachments" / "manifest.json"
        if (manifest_path.is_symlink() or not manifest_path.is_file()
                or manifest_path.stat().st_size > 1024 * 1024):
            raise RuntimeError("missing-copy-before-manifest")
        manifest = json.loads(manifest_path.read_bytes())
        if not isinstance(manifest, list):
            raise RuntimeError("invalid-copy-before-manifest")
        before = [attachment.get("timestamp") for group in manifest if isinstance(group, dict)
                  and isinstance(group.get("attachments"), list)
                  for attachment in group.get("attachments", []) if isinstance(attachment, dict)
                  and isinstance(attachment.get("suggestedHumanReadableName"), str)
                  and attachment["suggestedHumanReadableName"].startswith("copy-before-native-url-open_")
                  and attachment["suggestedHumanReadableName"].endswith(".png")]
        if len(before) != 1 or type(before[0]) not in (float, int):
            raise RuntimeError("missing-unique-copy-before-timestamp")
        before_timestamp = before[0]
        query_started = before_timestamp - 2
        if not started <= query_started <= before_timestamp <= finished:
            raise RuntimeError("invalid-copy-before-window")
        start_text = datetime.datetime.fromtimestamp(query_started).strftime("%Y-%m-%d %H:%M:%S")
        end_text = datetime.datetime.fromtimestamp(finished).strftime("%Y-%m-%d %H:%M:%S")
        webkit_processes = ("com.apple.WebKit.WebContent", "com.apple.WebKit.Networking",
                            "com.apple.WebKit.GPU", "WebContent", "Networking", "GPU")
        system_processes = ("runningboardd", "launchd", "SpringBoard")
        webkit_predicate = " OR ".join('process == "' + name + '"' for name in webkit_processes)
        system_predicate = " OR ".join('process == "' + name + '"' for name in system_processes)
        lifecycle_predicate = '(eventMessage CONTAINS[c] "process" OR eventMessage CONTAINS[c] "WebView" OR eventMessage CONTAINS[c] "navigation" OR eventMessage CONTAINS[c] "connection" OR eventMessage CONTAINS[c] "terminat" OR eventMessage CONTAINS[c] "crash" OR eventMessage CONTAINS[c] "launch" OR eventMessage CONTAINS[c] "exit")'
        predicate = ('((process == "App" OR (' + webkit_predicate + ')) AND '
                     + lifecycle_predicate + ') OR ((' + system_predicate
                     + ') AND eventMessage CONTAINS "' + BUNDLE + '")')
        command = ["xcrun", "simctl", "spawn", args.simulator, "log", "show",
                   "--style", "ndjson", "--start", start_text, "--end", end_text,
                   "--info", "--debug", "--predicate", predicate]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 45
        chunks, received = [], 0
        timed_out, truncated = False, False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(timeout=min(remaining, 0.2))
            if not events:
                if process.poll() is not None:
                    break
                continue
            data = os.read(process.stdout.fileno(), min(8192, 256 * 1024 - received + 1))
            if not data:
                break
            available = 256 * 1024 - received
            chunks.append(data[:available])
            received += min(len(data), available)
            if len(data) > available:
                truncated = True
                break
        if timed_out or truncated:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        else:
            process.wait(timeout=max(0.1, min(5, deadline - time.monotonic())))
        # Discard raw messages, paths, URLs, stderr and arbitrary text before writing evidence.
        operations = ["launch", "launching", "launched", "exit", "exited", "termination",
                      "terminate", "terminated", "crash", "crashed", "reload", "navigation",
                      "provisional", "connection", "invalidated", "interrupted", "suspended",
                      "running", "WebView loaded", "WebView failed to load",
                      "WebView failed provisional navigation", "WebView process terminated"]
        rows, unparsed = [], 0
        for line in b"".join(chunks).decode("utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                unparsed += 1
                continue
            if not isinstance(item, dict):
                unparsed += 1
                continue
            name = Path(str(item.get("processImagePath", ""))).name
            if name not in ("App", *webkit_processes, *system_processes):
                continue
            message = item.get("eventMessage", "")
            timestamp = item.get("timestamp", "")
            if not isinstance(message, str) or not isinstance(timestamp, str):
                continue
            matches_bundle = BUNDLE in message
            if name in system_processes and not matches_bundle:
                continue
            if not re.fullmatch(r"[0-9 T:.+Z-]{10,40}", timestamp):
                timestamp = "redacted"
            found = [word for word in operations if re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", message, re.I)]
            if not found and not matches_bundle:
                continue
            pid = item.get("processID")
            row = {"timestamp": timestamp, "process": name,
                   "processID": pid if type(pid) is int and 0 < pid < 2**31 else None,
                   "messageOperations": found, "matchesGuestBundle": matches_bundle,
                   "containsPrivacyMarker": "<private>" in message,
                   "mentionedPIDs": [int(value) for value in re.findall(r"\bpid\s*[:= ]\s*(\d{1,10})\b", message, re.I)[:8] if 0 < int(value) < 2**31],
                   "errorCodes": [int(value) for value in re.findall(r"\b(?:error(?:\s*code)?|code)\s*[:= ]\s*(-?\d{1,6})\b", message, re.I)[:8]]}
            rows.append(row)
        payload = b"".join((json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8") for row in rows)
        if len(payload) > 256 * 1024:
            raise RuntimeError("sanitized-output-limit")
        destination = case_dir / "copy-lifecycle-os-log.sanitized.jsonl"
        if destination.exists() or destination.is_symlink():
            raise RuntimeError("refusing-existing-diagnostic")
        destination.write_bytes(payload)
        status = "collected" if process.returncode == 0 and not timed_out and not truncated else "partial" if rows else "unavailable"
        evidence.update({"status": status, "file": destination.name, "bytes": len(payload),
                         "sha256": hashlib.sha256(payload).hexdigest(), "events": len(rows),
                         "queryExitCode": process.returncode, "timedOut": timed_out,
                         "truncated": truncated, "readBytes": received, "unparsedLines": unparsed,
                         "caseStartedAt": started, "caseFinishedAt": finished,
                         "copyBeforeAttachmentTimestamp": before_timestamp,
                         "queryStartedAt": query_started,
                         "processes": ["App", *webkit_processes, *system_processes],
                         "messagePolicy": "fixed lifecycle vocabulary and bounded numeric PIDs/codes only; no raw free text",
                         "absenceDoesNotProveCallWasNotMade": True})
        if status != "collected":
            warnings.append("copy-lifecycle-os-log: " + status)
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        evidence["errorType"] = type(error).__name__
        warnings.append("copy-lifecycle-os-log: unavailable")
    finally:
        try:
            if selector is not None:
                selector.close()
            if process is not None:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                if process.stdout is not None:
                    process.stdout.close()
        except (OSError, subprocess.SubprocessError):
            evidence["cleanupWarning"] = True
        record["copyLifecycleOSLog"] = evidence
        record["evidenceWarnings"] = warnings

# END CI_ONLY_COPY_LIFECYCLE_LOG_DIAGNOSTIC


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
        collect_qa_events(args, case_dir, record)
        collect_notification_os_log(args, case_dir, record)
        write_json(record_path, record)
    record = collect(args, index, identifier, record)
    collect_copy_lifecycle_os_log(args, case_dir, record)
    write_json(record_path, record)
    return record


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
