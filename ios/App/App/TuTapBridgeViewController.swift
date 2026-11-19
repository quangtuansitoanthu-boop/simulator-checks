import UIKit
import Capacitor
import WebKit
#if DEBUG

private final class TuTapQAEventsHandler: NSObject, WKScriptMessageHandler {
    private let eventsQueue = DispatchQueue(label: "vn.quangtu.tutam.qa-events", qos: .utility)
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "tutamQAEvents", message.frameInfo.isMainFrame,
              let body = message.body as? String, body.utf8.count <= 4096 else { return }
        // UIKit reads stay on the main callback; only primitive values cross to the I/O queue.
        var footerState: [String: Double]? = nil
        var footerEvent: String? = nil
        if Thread.isMainThread, let data = body.data(using: .utf8),
           let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           event["phase"] as? String == "capture", event["footer"] as? Bool == true,
           let eventType = event["type"] as? String,
           ["pointerdown", "pointerup", "pointercancel"].contains(eventType),
           let anchorPath = event["anchorPath"] as? String,
           ["/", "/kinh-sach", "/tu-tap", "/phap-thoai", "/ca-nhan"].contains(anchorPath),
           let webView = message.webView {
            let scroll = webView.scrollView
            let pan = scroll.panGestureRecognizer
            let translation = pan.translation(in: webView)
            let velocity = pan.velocity(in: webView)
            footerEvent = eventType
            footerState = [
                "sourceTime": (event["time"] as? NSNumber)?.doubleValue ?? -1,
                "panState": Double(pan.state.rawValue),
                "translationX": Double(translation.x), "translationY": Double(translation.y),
                "velocityX": Double(velocity.x), "velocityY": Double(velocity.y),
                "offsetX": Double(scroll.contentOffset.x), "offsetY": Double(scroll.contentOffset.y),
                "isDragging": scroll.isDragging ? 1 : 0,
                "isDecelerating": scroll.isDecelerating ? 1 : 0
            ]
        }
        let capturedFooterState = footerState
        let capturedFooterEvent = footerEvent
        // Diagnostic disk/console I/O must not stall UIKit touch delivery.
        eventsQueue.async {
            NSLog("[TutamQA] %@", body)
            self.appendEvent(body)
            if let state = capturedFooterState, let sourceType = capturedFooterEvent {
                let record: [String: Any] = ["type": "native-footer-state", "sourceType": sourceType, "state": state]
                if let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]),
                   let serialized = String(data: data, encoding: .utf8) {
                    self.appendEvent(serialized)
                }
            }
        }
    }

    private func appendEvent(_ body: String) {
        // JSON serialization keeps one physical line per sanitized script event.
        guard let input = body.data(using: .utf8),
              let event = try? JSONSerialization.jsonObject(with: input) as? [String: Any],
              let serialized = try? JSONSerialization.data(withJSONObject: event, options: [.sortedKeys]),
              let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else { return }
        let url = caches.appendingPathComponent("tutam-qa-events.jsonl")
        var line = serialized
        line.append(0x0A)
        let limit: UInt64 = 256 * 1024
        do {
            try FileManager.default.createDirectory(at: caches, withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: url.path) {
                guard FileManager.default.createFile(atPath: url.path, contents: nil) else { return }
            }
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            let size = try handle.seekToEnd()
            // Preserve earlier evidence across relaunches; never truncate or rotate at the cap.
            guard size <= limit, UInt64(line.count) <= limit - size else { return }
            try handle.write(contentsOf: line)
        } catch {
            // Diagnostic storage failures must not affect app interaction or auth.
        }
    }
}
#endif

@objc(TuTapBridgeViewController)
class TuTapBridgeViewController: CAPBridgeViewController {
#if DEBUG
    private var qaEventsInstalled = false
#endif

    override func capacitorDidLoad() {
        webView?.scrollView.pinchGestureRecognizer?.isEnabled = false
        bridge?.registerPluginInstance(DharmaAudioPlugin.shared)
        bridge?.registerPluginInstance(ReaderSpeechPlugin())
        bridge?.registerPluginInstance(PracticeHapticsPlugin())
        bridge?.registerPluginInstance(AppSystemBarsPlugin())
        bridge?.registerPluginInstance(BuddhistCalendarWidgetPlugin())
        bridge?.registerPluginInstance(PracticeWidgetPlugin())
        bridge?.registerPluginInstance(ExternalLinksPlugin())
#if DEBUG
        installQAEventsIfRequested()
#endif
    }

#if DEBUG
    private func installQAEventsIfRequested() {
        guard !qaEventsInstalled, ProcessInfo.processInfo.arguments.contains("-tutam-qa-events"),
              let webView = webView else { return }
        qaEventsInstalled = true
        webView.configuration.userContentController.add(TuTapQAEventsHandler(), name: "tutamQAEvents")
        let source = """
        (() => {
          const log = (detail) => {
            const body = JSON.stringify(detail);
            const handler = window.webkit?.messageHandlers?.tutamQAEvents;
            if (handler) handler.postMessage(body);
            else console.info('[TutamQA]', body);
          };
          const context = () => ({ path: location.pathname, readyState: document.readyState, time: performance.now(), activeTag: document.activeElement?.tagName || null, activeSelect: document.activeElement instanceof HTMLSelectElement });
          const describe = (event) => {
            const origin = event.target instanceof Element ? event.target : event.target?.parentElement;
            const target = origin?.closest('a,button,select,input');
            const detail = { ...context(), type: event.type, tag: target?.tagName || origin?.tagName || 'NON_ELEMENT', defaultPrevented: event.defaultPrevented };
            if (target?.closest('.qts-bottom-nav')) detail.footer = true;
            detail.cancelable = event.cancelable;
            if (typeof event.pointerId === 'number') detail.pointerId = event.pointerId;
            if (target?.id) detail.id = target.id.slice(0, 80);
            const label = target?.getAttribute('aria-label');
            if (label) detail.label = label.slice(0, 80);
            else if (target?.tagName === 'BUTTON') detail.buttonText = (target.textContent || '').trim().slice(0, 80);
            if (target?.tagName === 'A') {
              try { detail.anchorPath = new URL(target.getAttribute('href') || '', location.href).pathname; } catch {}
            }
            const point = event.changedTouches?.[0] || event;
            if (typeof point.identifier === 'number') detail.touchIdentifier = point.identifier;
            if (typeof point.clientX === 'number' && typeof point.clientY === 'number') {
              detail.x = point.clientX; detail.y = point.clientY;
            }
            return detail;
          };
          let footerContact = false;
          let footerMoveCount = 0;
          const numericRect = (rect) => ({ x: rect.x, y: rect.y, width: rect.width, height: rect.height });
          for (const type of ['pointermove', 'touchmove']) {
            window.addEventListener(type, (event) => {
              const origin = event.target instanceof Element ? event.target : event.target?.parentElement;
              const anchor = origin?.closest('.qts-bottom-nav a');
              if (!footerContact || !anchor || footerMoveCount >= 4) return;
              footerMoveCount += 1;
              const point = event.changedTouches?.[0] || event;
              const detail = { type, phase: 'capture', footer: true, time: performance.now(), x: point.clientX, y: point.clientY, cancelable: event.cancelable, defaultPrevented: event.defaultPrevented, sample: footerMoveCount };
              if (typeof event.pointerId === 'number') detail.pointerId = event.pointerId;
              if (typeof point.identifier === 'number') detail.touchIdentifier = point.identifier;
              log(detail);
            }, { capture: true, passive: true });
          }
          for (const type of ['pointerdown', 'pointerup', 'pointercancel', 'touchstart', 'touchend', 'touchcancel', 'mousedown', 'mouseup', 'click', 'mouseover']) {
            for (const capture of [true, false]) {
              window.addEventListener(type, (event) => {
                const detail = describe(event);
                if (!detail) return;
                if (capture && detail.footer) {
                  if (type === 'pointerdown' || (type === 'touchstart' && !footerContact)) {
                    footerContact = true; footerMoveCount = 0;
                  }
                  if (type === 'pointercancel') {
                    const origin = event.target instanceof Element ? event.target : event.target?.parentElement;
                    const anchor = origin?.closest('.qts-bottom-nav a');
                    const footer = anchor?.closest('.qts-bottom-nav');
                    if (anchor && footer) {
                      detail.anchorRect = numericRect(anchor.getBoundingClientRect());
                      detail.footerRect = numericRect(footer.getBoundingClientRect());
                      detail.scrollX = window.scrollX; detail.scrollY = window.scrollY;
                      detail.scrollTop = document.scrollingElement?.scrollTop ?? 0;
                      const viewport = window.visualViewport;
                      if (viewport) detail.visualViewport = { offsetLeft: viewport.offsetLeft, offsetTop: viewport.offsetTop, pageLeft: viewport.pageLeft, pageTop: viewport.pageTop, width: viewport.width, height: viewport.height, scale: viewport.scale };
                    }
                  }
                  if (['pointerup', 'pointercancel', 'touchend', 'touchcancel'].includes(type)) footerContact = false;
                }
                log({ ...detail, phase: capture ? 'capture' : 'bubble' });
                if (type === 'click' && capture) queueMicrotask(() => {
                  log({ ...detail, ...context(), phase: 'final', defaultPrevented: event.defaultPrevented });
                });
              }, { capture, passive: true });
            }
          }
          let previousPath = location.pathname;
          new MutationObserver(() => {
            if (location.pathname !== previousPath) {
              previousPath = location.pathname;
              log({ ...context(), type: 'pathname-change' });
            }
          }).observe(document, { childList: true, subtree: true });
          window.addEventListener('error', (event) => {
            const names = ['Error', 'TypeError', 'SyntaxError', 'ReferenceError', 'RangeError', 'URIError', 'EvalError'];
            const name = event.error?.name;
            // Never include error messages, stacks, source URLs, or rejected values.
            log({ ...context(), type: 'error', kind: names.includes(name) ? name : 'resource-or-module-error' });
          }, { capture: true, passive: true });
          log({ ...context(), type: 'installed' });
        })();
        """
        webView.configuration.userContentController.addUserScript(WKUserScript(
            source: source, injectionTime: .atDocumentStart, forMainFrameOnly: true
        ))
    }
#endif
}

@objc(ExternalLinksPlugin)
public class ExternalLinksPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "ExternalLinksPlugin"
    public let jsName = "ExternalLinks"
    public let pluginMethods: [CAPPluginMethod] = [CAPPluginMethod(name: "openStore", returnType: CAPPluginReturnPromise)]

    @objc public func openStore(_ call: CAPPluginCall) {
        guard call.getString("url") == "https://apps.apple.com/app/id6829182305",
              let url = URL(string: "itms-apps://apps.apple.com/app/id6829182305") else {
            call.reject("Invalid App Store URL")
            return
        }
        DispatchQueue.main.async {
            UIApplication.shared.open(url, options: [:]) { opened in
                if opened { call.resolve() }
                else { call.reject("Unable to open App Store") }
            }
        }
    }
}

@objc(PracticeHapticsPlugin)
public class PracticeHapticsPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "PracticeHapticsPlugin"
    public let jsName = "PracticeHaptics"
    public let pluginMethods: [CAPPluginMethod] = [CAPPluginMethod(name: "rosaryBead", returnType: CAPPluginReturnPromise)]

    @MainActor private lazy var selection = UISelectionFeedbackGenerator()
    @MainActor private lazy var completion = UINotificationFeedbackGenerator()

    @objc public func rosaryBead(_ call: CAPPluginCall) {
        let completedCycle = call.getBool("completedCycle", false)
        DispatchQueue.main.async {
            if self.bridge?.webView?.window?.isKeyWindow == true,
               UIApplication.shared.applicationState == .active {
                if completedCycle {
                    self.completion.notificationOccurred(.success)
                } else {
                    self.selection.selectionChanged()
                }
                self.selection.prepare()
            }
            call.resolve()
        }
    }
}

// Keep the native bottom safe area in the same color as the active web surface.
@objc(AppSystemBarsPlugin)
public class AppSystemBarsPlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "AppSystemBarsPlugin"
    public let jsName = "AppSystemBars"
    public let pluginMethods: [CAPPluginMethod] = [CAPPluginMethod(name: "setStyle", returnType: CAPPluginReturnPromise)]

    @objc public func setStyle(_ call: CAPPluginCall) {
        guard let hex = call.getString("backgroundColor"), hex.count == 7, hex.first == "#",
              let value = UInt32(hex.dropFirst(), radix: 16) else {
            call.reject("Invalid system bar background")
            return
        }
        let color = UIColor(red: CGFloat((value >> 16) & 255) / 255,
                            green: CGFloat((value >> 8) & 255) / 255,
                            blue: CGFloat(value & 255) / 255, alpha: 1)
        DispatchQueue.main.async {
            self.bridge?.viewController?.view.backgroundColor = color
            self.bridge?.webView?.backgroundColor = color
            self.bridge?.webView?.scrollView.backgroundColor = color
            if #available(iOS 15.0, *) {
                self.bridge?.webView?.underPageBackgroundColor = color
            }
            call.resolve()
        }
    }
}
