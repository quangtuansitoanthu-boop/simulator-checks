"""Prepare a Foundation-only smoke fixture from verbatim current App validation/observer code.
No application, WebKit, SDK, network, file writer, or native process is started here.
Root executes the emitted Swift fixture with xcrun swift <file> -tutam-qa-events.
"""
from pathlib import Path
import hashlib,json,sys
root=Path.cwd()
source=(root/'ios/App/App/TuTapBridgeViewController.swift').read_text(encoding='utf-8').replace('\r\n','\n')
def between(start,end):
    assert source.count(start)==1 and source.count(end)==1,(start,end)
    return source[source.index(start):source.index(end,source.index(start))]
structs=between('private struct TuTapQANotificationSDKEvent:', 'private final class TuTapQAEventsHandler:')
validator=between('    private func diagnosticInteger(', '    private func queueNativeDiagnostic<')
observer=between('        qaNativeDiagnosticObserver = NotificationCenter.default.addObserver(', '        return handler\n')
remove="""    deinit {
        if let observer = qaNativeDiagnosticObserver { NotificationCenter.default.removeObserver(observer) }
    }
"""
assert source.count(remove)==1
swift='import Foundation\nimport CoreFoundation\n\n'+structs+"final class TuTapQAEventsHandler {\n    var captured = [[String: Any]]()\n"+validator+"""    private func queueNativeDiagnostic<Event: Encodable & Sendable>(_ event: Event) {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try! encoder.encode(event)
        captured.append(try! JSONSerialization.jsonObject(with: data) as! [String: Any])
    }
}
final class FoundationObserverHarness {
    private var qaEventsHandler: TuTapQAEventsHandler?
    private var qaNativeDiagnosticObserver: NSObjectProtocol?
    init(_ handler: TuTapQAEventsHandler) {
        qaEventsHandler = handler
"""+observer+"    }\n"+remove+"}\n"+"""
let handler = TuTapQAEventsHandler()
var observer: FoundationObserverHarness? = FoundationObserverHarness(handler)
let name = Notification.Name("TuTapQANativeDiagnostic")
func post(_ payload: [AnyHashable: Any]) {
    NotificationCenter.default.post(name: name, object: nil, userInfo: payload)
}
func check(_ condition: @autoclosure () -> Bool, _ label: String) {
    guard condition() else { fatalError("Foundation diagnostic smoke failed: " + label) }
}
let process = ProcessInfo.processInfo
let sdk: [AnyHashable: Any] = ["type": "native-notification-sdk", "phase": "check_entry", "pid": Int(process.processIdentifier),
    "uptime": TimeInterval(123.25), "status": -1, "domain": "none", "errorCode": 0]
let nav: [AnyHashable: Any] = ["type": "native-navigation", "phase": "didCommit", "pid": Int(process.processIdentifier),
    "uptime": TimeInterval(123.25), "schemeClass": "capacitor", "pathClass": "copy", "domain": "none", "errorCode": 0]
post(sdk); post(nav)
check(handler.captured.count == 2, "native Int/Double payloads accepted through Foundation")
check(Set(handler.captured[0].keys) == Set(["type","phase","pid","uptime","status","domain","errorCode"]), "SDK exact seven serialized keys")
check(Set(handler.captured[1].keys) == Set(["type","phase","pid","uptime","schemeClass","pathClass","domain","errorCode"]), "navigation exact eight serialized keys")
var bridged = sdk
bridged["pid"] = NSNumber(value: Int32(5))
bridged["uptime"] = NSNumber(value: 123.25)
bridged["status"] = NSNumber(value: Int32(4))
bridged["errorCode"] = NSNumber(value: Int32(-999999))
post(bridged)
check(handler.captured.count == 3, "Foundation NSNumber representations accepted")
let phases = ["check_entry","check_settings_callback","check_resolve","schedule_entry","schedule_invalid_batch","schedule_invalid_id","schedule_content_reject","schedule_past_reject","schedule_trigger_reject","schedule_pending_start","schedule_pending_callback","schedule_add_error","schedule_callbacks_complete","schedule_restore_error","schedule_rollback_expired","schedule_rollback_reject","schedule_rollback_incomplete","schedule_reject","schedule_resolve"]
for phase in phases { var row = sdk; row["phase"] = phase; post(row) }
for phase in ["didStart","didCommit","didFinish","didFail","didFailProvisional","processTerminate"] { var row = nav; row["phase"] = phase; post(row) }
for domain in ["UNErrorDomain","NSCocoaErrorDomain","NSPOSIXErrorDomain","NSURLErrorDomain","TuTapLocalNotifications","other","none"] { var row = sdk; row["domain"] = domain; post(row) }
for domain in ["none","WKErrorDomain","NSURLErrorDomain","NSCocoaErrorDomain","NSPOSIXErrorDomain","other"] { var row = nav; row["domain"] = domain; post(row) }
let validCount = handler.captured.count
check(validCount == 41, "all fixed phase/domain values accepted")
let malformed: [[AnyHashable: Any]] = [
    sdk.merging(["secret": "SYNTHETIC_REJECT_ME"]) { _, new in new },
    nav.merging(["url": "SYNTHETIC_REJECT_ME"]) { _, new in new },
    sdk.merging(["pid": true]) { _, new in new },
    sdk.merging(["pid": 1.5]) { _, new in new },
    sdk.merging(["pid": "123"]) { _, new in new },
    sdk.merging(["pid": -1]) { _, new in new },
    sdk.merging(["uptime": true]) { _, new in new },
    sdk.merging(["uptime": "123.25"]) { _, new in new },
    sdk.merging(["uptime": Double.nan]) { _, new in new },
    sdk.merging(["uptime": Double.infinity]) { _, new in new },
    sdk.merging(["uptime": -1.25]) { _, new in new },
    sdk.merging(["status": true]) { _, new in new },
    sdk.merging(["status": 1.5]) { _, new in new },
    sdk.merging(["status": 5]) { _, new in new },
    sdk.merging(["status": -2]) { _, new in new },
    sdk.merging(["errorCode": true]) { _, new in new },
    sdk.merging(["errorCode": "0"]) { _, new in new },
    sdk.merging(["errorCode": 1000000]) { _, new in new },
    sdk.merging(["errorCode": -1000000]) { _, new in new },
    sdk.merging(["domain": "SYNTHETIC_REJECT_ME"]) { _, new in new },
    sdk.merging(["phase": "unknown"]) { _, new in new },
    sdk.merging(["type": "unknown"]) { _, new in new },
    nav.merging(["schemeClass": "SYNTHETIC_REJECT_ME"]) { _, new in new },
    nav.merging(["pathClass": "SYNTHETIC_REJECT_ME"]) { _, new in new },
    nav.merging(["phase": "check_entry"]) { _, new in new },
    sdk.merging(["phase": "didFinish"]) { _, new in new },
    sdk.merging([AnyHashable(1): "SYNTHETIC_REJECT_ME"]) { _, new in new }
]
for payload in malformed { post(payload) }
var missing = sdk; missing.removeValue(forKey: "phase"); post(missing)
check(handler.captured.count == validCount, "malformed key/type/PII/range/enum payloads rejected")
let data = try! JSONSerialization.data(withJSONObject: handler.captured)
check(!String(data: data, encoding: .utf8)!.contains("SYNTHETIC_REJECT_ME"), "rejected synthetic content never serialized")
observer = nil
post(sdk)
check(handler.captured.count == validCount, "observer removed at deinit")
print("PASS Foundation diagnostic transport: 41 valid, 28 rejected, observer removed; no SDK calls")
"""
output=Path(sys.argv[1]) if len(sys.argv)>1 else root/'.artifacts/full-github-current-20261121/native-diagnostic-foundation-smoke.swift'
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(swift,encoding='utf-8',newline='\n')
proof={'sourcePath':'ios/App/App/TuTapBridgeViewController.swift','sourceSha256':hashlib.sha256(source.encode()).hexdigest(),'fixtureSha256':hashlib.sha256(swift.encode()).hexdigest(),'verbatimValidatorSha256':hashlib.sha256(validator.encode()).hexdigest(),'verbatimObserverSha256':hashlib.sha256(observer.encode()).hexdigest(),'onlySubstitution':'existing serial file writer replaced by synchronous in-memory JSON capture; no SDK/WebKit/UI calls','executed':False}
output.with_suffix('.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
print(json.dumps(proof))
