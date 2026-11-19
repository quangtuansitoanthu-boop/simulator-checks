import XCTest
import UIKit

// Mỗi phương thức chạy trên bản cài mới của Simulator CI; không chỉnh storage hoặc quyền bằng test hook.
class NativeGuestUITestCase: XCTestCase {
    private var evidenceApplication: XCUIApplication?

    override func tearDown() async throws {
        await MainActor.run {
            if let app = evidenceApplication {
                evidence("final-" + name, app: app)
                evidence("system-final-" + name, app: XCUIApplication(bundleIdentifier: "com.apple.springboard"))
            }
            XCUIDevice.shared.orientation = .portrait
        }
        try await super.tearDown()
    }

    @MainActor
    func evidence(_ name: String, app: XCUIApplication) {
        // Lấy snapshot AX trước để WebKit kết thúc cập nhật layout trước ảnh hiển thị tương ứng.
        let tree = XCTAttachment(string: app.debugDescription)
        tree.name = name + "-accessibility"
        tree.lifetime = .keepAlways
        let screenshot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        screenshot.name = name
        screenshot.lifetime = .keepAlways
        add(screenshot)
        add(tree)
    }

    @MainActor
    func launchGuest() -> XCUIApplication {
        continueAfterFailure = false
        XCUIDevice.shared.orientation = .portrait
        let app = XCUIApplication()
        evidenceApplication = app
        app.launchArguments = ["-tutam-qa-events", "1", "-AppleLanguages", "(vi)", "-AppleLocale", "vi_VN"]
        app.launch()
        XCTAssertTrue(app.webViews.firstMatch.waitForExistence(timeout: 30), "Native WKWebView must load")
        XCTAssertTrue(app.links["Cá nhân"].firstMatch.waitForExistence(timeout: 30), "Native app must finish rendering its navigation")
        return app
    }

    @MainActor
    func waitUntil(_ message: String, timeout: TimeInterval = 15, _ condition: @escaping () -> Bool) {
        let ready = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in condition() }, object: NSObject())
        XCTAssertEqual(XCTWaiter.wait(for: [ready], timeout: timeout), .completed, message)
    }

    @MainActor
    func allowExpectedNotificationPermission(_ app: XCUIApplication) {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let expected = NSPredicate(format: "label CONTAINS[cd] %@ AND (label CONTAINS[cd] %@ OR label CONTAINS[cd] %@)", "Tu tập", "notification", "thông báo")
        let system = springboard.alerts.matching(expected).firstMatch
        let local = app.alerts.matching(expected).firstMatch
        // Không cố định owner sau lần chờ đầu: iOS có thể xuất alert SpringBoard trễ hơn cây AX app.
        waitUntil("Fresh install must expose this app's actual notification authorization alert", timeout: 30) { system.exists || local.exists }
        let alert = system.exists ? system : local
        let words = ([alert.label] + alert.staticTexts.allElementsBoundByIndex.map { $0.label }).joined(separator: " ").lowercased()
        XCTAssertTrue(words.contains("tu tập") && (words.contains("notification") || words.contains("thông báo")), "Only this app's notification permission may be granted")
        let allow = alert.buttons.matching(NSPredicate(format: "label IN %@", ["Allow", "Cho phép", "Cho Phép"] as NSArray)).firstMatch
        XCTAssertTrue(allow.waitForExistence(timeout: 5), "Expected exact affirmative notification permission button inside the verified alert")
        allow.tap()
        waitUntil("Accepted notification permission dialog must dismiss") { !alert.exists }
    }

    @MainActor
    func insideViewport(_ element: XCUIElement, viewport: CGRect) -> Bool {
        guard element.exists else { return false }
        let frame = element.frame
        return frame.width > 0 && frame.height > 0 && viewport.contains(frame) && element.isHittable
    }

    @MainActor
    func insideViewport(_ element: XCUIElement, app: XCUIApplication) -> Bool {
        insideViewport(element, viewport: app.frame)
    }

    @MainActor
    func dismissPracticeSyncNotice(_ app: XCUIApplication) {
        // Chỉ xử lý banner công phu đã lưu, không chạm các nút Để sau của hướng dẫn/thông báo tài khoản.
        let message = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Đã lưu trên thiết bị. Đăng nhập lại để đồng bộ công phu.")).firstMatch
        guard message.exists else { return }
        let viewport = app.frame
        let noticeFrame = message.frame
        guard viewport.intersects(noticeFrame) else { return }
        let candidates = app.buttons.matching(identifier: "Để sau").allElementsBoundByIndex
        if let later = candidates.first(where: {
            let frame = $0.frame
            return frame.width > 0 && frame.height > 0 && viewport.contains(frame) &&
                frame.minY >= noticeFrame.minY && frame.minY <= noticeFrame.maxY + 100 &&
                frame.midX >= noticeFrame.minX && frame.midX <= noticeFrame.maxX && $0.isHittable
        }) {
            later.tap()
            waitUntil("Dismissed practice-sync notice must disappear") { !message.exists }
        }
    }

    @MainActor
    func reveal(_ element: XCUIElement, app: XCUIApplication) {
        // WKWebView có thể chưa đưa control ngoài màn hình vào AX; phải tìm bằng cuộn trước khi kết luận thiếu.
        var viewport = app.frame
        let dialog = app.otherElements.matching(NSPredicate(format: "label ENDSWITH %@ OR label ENDSWITH %@", "hộp thoại web", "web dialog")).firstMatch
        if dialog.exists {
            // aria-modal ẩn cây AX phía sau; chỉ dùng khung dialog, không truy vấn footer nền.
            viewport = viewport.intersection(dialog.frame)
        } else {
            let navigation = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@ OR label BEGINSWITH %@", "Điều hướng nhanh", "Quick navigation")).firstMatch
            if navigation.exists {
                let frame = navigation.frame
                if frame.width > 0 && frame.minY > viewport.midY && frame.minY < viewport.maxY {
                    // WebKit có thể báo hittable cho nội dung nằm dưới thanh điều hướng fixed.
                    viewport.size.height = frame.minY - viewport.minY
                }
            }
        }
        let keyboard = app.keyboards.firstMatch
        if keyboard.exists {
            let frame = keyboard.frame
            if frame.width > 0 && frame.minY > viewport.minY {
                viewport.size.height = min(viewport.maxY, frame.minY) - viewport.minY
                let toolbar = app.toolbars.firstMatch
                if toolbar.exists {
                    let bar = toolbar.frame
                    if bar.height > 0 && bar.minY > viewport.minY && bar.maxY <= frame.minY {
                        viewport.size.height = min(viewport.maxY, bar.minY) - viewport.minY
                    }
                }
            }
        }
        for attempt in 0..<14 {
            if element.exists {
                let frame = element.frame
                if frame.width > 0 && frame.height > 0 {
                    if viewport.contains(frame) && element.isHittable { return }
                    if frame.midY < viewport.midY { app.swipeDown() }
                    else { app.swipeUp() }
                    continue
                }
            }
            if attempt < 7 {
                app.swipeUp()
            } else {
                app.swipeDown()
            }
        }
        evidence("unreachable-control", app: app)
        XCTAssertTrue(element.exists, "Expected actual native accessibility control after bounded scroll search")
        XCTAssertTrue(insideViewport(element, viewport: viewport), "Control must be visible and reachable inside the native viewport")
    }

    @MainActor
    func tap(_ element: XCUIElement, app: XCUIApplication) {
        reveal(element, app: app)
        element.tap()
    }

    @MainActor
    func homeLoginReady(_ element: XCUIElement, app: XCUIApplication) -> Bool {
        reveal(element, app: app)
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        var capturedInterruption = false
        var previousFrame: CGRect?
        var ready = false
        waitUntil("Home login requires an unobstructed, stable native control") {
            let visibleBanner = springboard.descendants(matching: .any)
                .matching(identifier: "NotificationShortLookView").allElementsBoundByIndex.contains {
                    let frame = $0.frame
                    return frame.width > 0 && frame.height > 0 && app.frame.intersects(frame)
                }
            if visibleBanner {
                if !capturedInterruption {
                    self.evidence("home-login-system-interruption", app: springboard)
                    capturedInterruption = true
                }
                previousFrame = nil
                return false
            }
            guard self.insideViewport(element, app: app), element.isEnabled else {
                previousFrame = nil
                return false
            }
            let frame = element.frame
            ready = previousFrame == frame
            previousFrame = frame
            return ready
        }
        return ready
    }

    @MainActor
    func navigate(_ label: String, app: XCUIApplication, captureNavigationEvidence: Bool = true) {
        dismissPracticeSyncNotice(app)
        let navigation = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@ OR label BEGINSWITH %@", "Điều hướng nhanh", "Quick navigation")).firstMatch
        XCTAssertTrue(navigation.waitForExistence(timeout: 20), "Bottom tab tap must belong to the actual navigation group")
        let links = navigation.links.matching(identifier: label)
        XCTAssertTrue(links.firstMatch.waitForExistence(timeout: 20), "Bottom navigation must expose \(label)")
        let viewport = app.frame
        let footer = navigation.frame
        let visible = links.allElementsBoundByIndex.compactMap { element -> (XCUIElement, CGRect)? in
            let frame = element.frame
            return frame.width > 0 && frame.height > 0 && viewport.contains(frame) && footer.contains(frame) ? (element, frame) : nil
        }
        guard visible.count == 1, let tab = visible.first?.0 else {
            evidence("missing-bottom-tab-" + label, app: app)
            XCTFail("Expected one visible link inside the actual bottom navigation: \(label)")
            return
        }
        if captureNavigationEvidence { evidence("navigation-before-" + label, app: app) }
        let targetViewport = app.frame
        let navigationFrame = navigation.frame
        let labelFrame = tab.frame
        let target = CGPoint(x: labelFrame.midX, y: (navigationFrame.minY + labelFrame.minY) / 2)
        guard navigationFrame.contains(labelFrame), navigationFrame.contains(target), targetViewport.contains(target) else {
            evidence("invalid-bottom-tab-icon-target-" + label, app: app)
            XCTFail("Visible footer icon target must lie inside its actual navigation group")
            return
        }
        // Một cử chỉ chạm ngắn có thời lượng rõ ràng; không lặp tap để vượt kiểm tra đích đến.
        app.coordinate(withNormalizedOffset: CGVector(dx: (target.x - targetViewport.minX) / targetViewport.width, dy: (target.y - targetViewport.minY) / targetViewport.height)).press(forDuration: 0.08)
        let marker: XCUIElement
        switch label {
        case "Cá nhân": marker = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Cài đặt ứng dụng ")).firstMatch
        case "Kinh sách": marker = app.staticTexts["Kho Kinh Sách"].firstMatch
        case "Tu tập": marker = app.staticTexts["Chế Độ Tu Tập"].firstMatch
        default:
            XCTFail("Missing destination contract for tested navigation tab \(label)")
            return
        }
        if !marker.waitForExistence(timeout: 30) {
            evidence("navigation-first-tap-failed-" + label, app: app)
            // Chỉ chẩn đoán touch/hover: dù lần hai tới đích, yêu cầu một lần chạm vẫn FAIL.
            let diagnosticViewport = app.frame
            let diagnosticFooter = navigation.frame
            let diagnosticLabel = tab.frame
            let diagnosticPoint = CGPoint(x: diagnosticLabel.midX, y: (diagnosticFooter.minY + diagnosticLabel.minY) / 2)
            if diagnosticLabel.width > 0 && diagnosticLabel.height > 0 &&
                diagnosticFooter.contains(diagnosticLabel) && diagnosticFooter.contains(diagnosticPoint) && diagnosticViewport.contains(diagnosticPoint) {
                app.coordinate(withNormalizedOffset: CGVector(dx: (diagnosticPoint.x - diagnosticViewport.minX) / diagnosticViewport.width,
                    dy: (diagnosticPoint.y - diagnosticViewport.minY) / diagnosticViewport.height)).tap()
                let reachedAfterDiagnosticTap = marker.waitForExistence(timeout: 10)
                evidence("navigation-diagnostic-second-tap-\(reachedAfterDiagnosticTap ? "reached" : "missing")-" + label, app: app)
            } else {
                evidence("navigation-diagnostic-second-tap-invalid-geometry-" + label, app: app)
            }
            XCTFail("Native tab must reach its actual destination after one tap; diagnostic second tap cannot satisfy this requirement")
            return
        }
        if captureNavigationEvidence { evidence("navigation-after-" + label, app: app) }
    }

    @MainActor
    func openSettings(_ app: XCUIApplication, captureNavigationEvidence: Bool = true) {
        let master = app.switches["Bật thông báo nhắc nhở tu tập"].firstMatch
        // Quay lại từ Notification Center vẫn ở settings; bấm lại tab hiện tại không xóa query tab=settings.
        if master.exists && master.frame.width > 0 && master.frame.height > 0 { return }
        navigate("Cá nhân", app: app, captureNavigationEvidence: captureNavigationEvidence)
        let settings = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Cài đặt ứng dụng")).firstMatch
        tap(settings, app: app)
        XCTAssertTrue(app.switches["Bật thông báo nhắc nhở tu tập"].firstMatch.waitForExistence(timeout: 20))
    }

    @MainActor
    func openAppearance(_ app: XCUIApplication) {
        let motion = app.switches["Hiệu ứng chuyển động"].firstMatch
        if !motion.exists || motion.frame.width == 0 || motion.frame.height == 0 {
            let summary = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@ AND label != %@", "Giao diện & ngôn ngữ ", "Giao diện & ngôn ngữ")).firstMatch
            tap(summary, app: app)
        }
        reveal(motion, app: app)
    }

    @MainActor
    func labelledToggle(_ label: String, app: XCUIApplication) -> XCUIElement {
        // WebKit exposes aria-pressed controls as Switch on some iOS versions.
        let toggle = app.switches[label].firstMatch
        return toggle.exists ? toggle : app.buttons[label].firstMatch
    }

    @MainActor
    func checked(_ element: XCUIElement) -> Bool? {
        if let value = element.value as? String {
            if ["1", "true"].contains(value.lowercased()) { return true }
            if ["0", "false"].contains(value.lowercased()) { return false }
        }
        return (element.value as? NSNumber)?.boolValue
    }

    @MainActor
    func setMotion(_ enabled: Bool, app: XCUIApplication) {
        let toggle = app.switches["Hiệu ứng chuyển động"].firstMatch
        reveal(toggle, app: app)
        XCTAssertNotNil(checked(toggle), "Actual motion switch must expose checked state")
        if checked(toggle) != enabled { toggle.tap() }
        waitUntil("Motion switch must reflect requested state") { self.checked(toggle) == enabled }
    }

    @MainActor
    func revealContentControl(_ card: XCUIElement, app: XCUIApplication) {
        func contentViewport() -> CGRect? {
            let frame = app.frame
            guard frame.width.isFinite && frame.height.isFinite && frame.minX.isFinite && frame.minY.isFinite,
                  frame.width > 0 && frame.height > 0 else { return nil }
            let headers = app.otherElements.matching(NSPredicate(format: "label == %@ OR label == %@ OR label == %@", "biểu ngữ", "banner", "Tu tập Phật pháp, điều hướng")).allElementsBoundByIndex
            guard let header = headers.first(where: {
                let bounds = $0.frame
                return bounds.width > 0 && bounds.height > 0 && bounds.intersects(frame)
                    && bounds.minX >= frame.minX && bounds.maxX <= frame.maxX
                    && bounds.minY <= frame.minY && bounds.maxY > frame.minY && bounds.maxY < frame.midY
            }) else { return nil }
            let navigation = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@ OR label BEGINSWITH %@", "Điều hướng nhanh", "Quick navigation")).firstMatch
            guard navigation.exists else { return nil }
            let footer = navigation.frame
            // AX thực tế làm tròn đáy footer 875pt trong app 874pt; vẫn dùng đúng đỉnh footer đo được.
            guard footer.width > 0 && footer.height > 0,
                  footer.minX >= frame.minX && footer.maxX <= frame.maxX,
                  footer.minY > frame.midY && footer.minY < frame.maxY,
                  abs(footer.maxY - frame.maxY) <= 1 else { return nil }
            let top = header.frame.maxY
            let bottom = footer.minY
            guard bottom > top else { return nil }
            return CGRect(x: frame.minX, y: top, width: frame.width, height: bottom - top)
        }
        for attempt in 0..<14 {
            guard let viewport = contentViewport() else {
                evidence("missing-content-control-chrome", app: app)
                XCTFail("Content control scrolling requires actual header and footer accessibility frames")
                return
            }
            let margin = min(CGFloat(12), viewport.height * 0.05)
            var delta: CGFloat
            let bounds = card.exists ? card.frame : .zero
            if bounds.width > 0 && bounds.height > 0 {
                guard viewport.height >= bounds.height && viewport.width >= bounds.width else {
                    evidence("oversized-content-control", app: app)
                    XCTFail("Whole control must fit the measured content viewport before scrolling")
                    return
                }
                if viewport.contains(bounds) && card.isHittable { return }
                if bounds.maxY > viewport.maxY { delta = bounds.maxY - viewport.maxY + margin }
                else if bounds.minY < viewport.minY { delta = bounds.minY - viewport.minY - margin }
                else { continue }
            } else {
                delta = viewport.height * (attempt < 7 ? 0.25 : -0.25)
            }
            // Kéo ngắn trong vùng nội dung thật; giữ cuối cử chỉ để không văng qua thẻ.
            let limit = viewport.height * 0.5
            delta = max(-limit, min(limit, delta))
            let x = bounds.width > 0 ? min(viewport.maxX - margin, max(viewport.minX + margin, bounds.midX)) : viewport.midX
            let frame = app.frame
            let start = app.coordinate(withNormalizedOffset: CGVector(dx: (x - frame.minX) / frame.width, dy: (viewport.midY + delta / 2 - frame.minY) / frame.height))
            let end = app.coordinate(withNormalizedOffset: CGVector(dx: (x - frame.minX) / frame.width, dy: (viewport.midY - delta / 2 - frame.minY) / frame.height))
            start.press(forDuration: 0.1, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.1)
        }
        if let viewport = contentViewport(), card.exists, viewport.contains(card.frame), card.isHittable { return }
        evidence("unreachable-content-control", app: app)
        XCTAssertTrue(card.exists, "Content control must exist after bounded content scrolling")
        if let viewport = contentViewport() {
            XCTAssertTrue(insideViewport(card, viewport: viewport), "Whole control must fit between the actual header and footer")
            XCTAssertTrue(card.isHittable, "Content control must receive the single real tap")
        } else {
            XCTFail("Content chrome must remain measurable after scrolling")
        }
    }

    @MainActor
    func openRosaryFromCatalog(_ app: XCUIApplication) {
        dismissPracticeSyncNotice(app)
        let card = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Lần tràng hạt")).firstMatch
        revealContentControl(card, app: app)
        tap(card, app: app)
        let start = app.buttons["Bắt đầu 🙏"].firstMatch
        tap(start, app: app)
        XCTAssertTrue(app.buttons["Quay lại tu tập"].firstMatch.waitForExistence(timeout: 15))
    }

    @MainActor
    func rosaryCount(_ app: XCUIApplication) -> Int {
        let numbers = app.staticTexts.matching(NSPredicate(format: "label MATCHES %@", "^[0-9]+$"))
        XCTAssertEqual(numbers.count, 1, "Actual rosary counter must be uniquely exposed")
        guard let count = Int(numbers.firstMatch.label) else { XCTFail("Rosary counter must be numeric"); return -1 }
        return count
    }

    @MainActor
    func librarySearch(_ app: XCUIApplication) -> XCUIElement {
        let field = app.searchFields["Tìm kiếm bài viết"].firstMatch
        XCTAssertTrue(field.waitForExistence(timeout: 20))
        reveal(field, app: app)
        return field
    }

    @MainActor
    func rotate(_ orientation: UIDeviceOrientation, app: XCUIApplication) {
        XCUIDevice.shared.orientation = orientation
        waitUntil("Native viewport must complete rotation") {
            orientation == .portrait ? app.frame.height > app.frame.width : app.frame.width > app.frame.height
        }
    }
}

final class NativeGuestSmokeTests: NativeGuestUITestCase {

    @MainActor
    private func verifyHomeQuickAction(labels: [String], headings: [String], markerType: XCUIElement.ElementType = .staticText, coldStart: Bool = false) throws {
        let app = launchGuest()
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        let icon = springboard.icons.matching(NSPredicate(format: "label IN[c] %@", ["Tu tập", "Tu tap"] as NSArray)).firstMatch

        if coldStart { app.terminate() }
        XCUIDevice.shared.press(.home)
        XCTAssertTrue(springboard.wait(for: .runningForeground, timeout: 15))
        let viewport = springboard.frame
        XCTAssertGreaterThan(viewport.width, 0)
        XCTAssertGreaterThan(viewport.height, 0)

        var iconFrame: CGRect?
        for page in 0..<5 {
            // An offscreen SpringBoard icon can be in the accessibility tree. On iOS 26.2,
            // isHittable can throw while resolving the activation point of such an icon.
            if icon.waitForExistence(timeout: 3) {
                let candidate = icon.frame
                if candidate.width > 0 && candidate.height > 0 && viewport.contains(candidate) {
                    iconFrame = candidate
                    break
                }
            }
            if page < 4 { springboard.swipeLeft() }
        }

        let frame = try XCTUnwrap(iconFrame, "The installed Tu tap icon must be on a visible Home Screen page")
        let center = CGVector(dx: (frame.midX - viewport.minX) / viewport.width,
                              dy: (frame.midY - viewport.minY) / viewport.height)
        springboard.coordinate(withNormalizedOffset: center).press(forDuration: 1.15)
        let shortcut = springboard.buttons.matching(NSPredicate(format: "label IN[c] %@", labels as NSArray)).firstMatch
        XCTAssertTrue(shortcut.waitForExistence(timeout: 10), "Expected the actual Home Screen action: \(labels[0])")
        shortcut.tap()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 30))
        let webView = app.webViews.firstMatch
        let heading = webView.descendants(matching: markerType).matching(NSPredicate(format: "label IN[c] %@", headings as NSArray)).firstMatch
        let opened = heading.waitForExistence(timeout: 25)
        if !opened { print("QA destination accessibility: " + String(webView.debugDescription.prefix(5000))) }
        XCTAssertTrue(opened, "Home Screen action must expose \(headings[0])")
    }

    @MainActor
    func testHomeQuickActionScripturesColdStart() throws {
        try verifyHomeQuickAction(labels: ["Scriptures", "Kinh sách"],
                                  headings: ["Kho Kinh Sách", "Sutra and Book Library"], coldStart: true)
    }

    @MainActor
    func testHomeQuickActionPractice() throws {
        try verifyHomeQuickAction(labels: ["Practice", "Tu tập"], headings: ["Chế Độ Tu Tập", "Practice Mode", "Practice"])
    }

    @MainActor
    func testHomeQuickActionDharmaTalks() throws {
        try verifyHomeQuickAction(labels: ["Dharma Talks", "Pháp thoại"],
                                  headings: ["Tìm kiếm và bộ lọc", "Search and filters"], markerType: .button)
    }

    @MainActor
    func testHomeQuickActionWidgetsGuide() throws {
        try verifyHomeQuickAction(labels: ["Widgets", "Widget"],
                                  headings: ["Thêm widget lên màn hình chính", "Add a home-screen widget"], coldStart: true)
    }

    @MainActor
    func testRosaryBackPreservesCountInPortraitAndLandscape() {
        let app = launchGuest()
        navigate("Tu tập", app: app)
        openRosaryFromCatalog(app)
        for pass in 1...5 {
            for orientation in [UIDeviceOrientation.portrait, .landscapeLeft] {
                rotate(orientation, app: app)
                let count = rosaryCount(app)
                tap(app.buttons["Niệm một hạt"].firstMatch, app: app)
                XCTAssertTrue(app.staticTexts[String(count + 1)].firstMatch.waitForExistence(timeout: 10))
                XCTAssertEqual(rosaryCount(app), count + 1, "One real tap must increase the rosary count exactly once")
                let back = app.buttons["Quay lại tu tập"].firstMatch
                reveal(back, app: app)
                XCTAssertGreaterThanOrEqual(back.frame.width, 44)
                XCTAssertGreaterThanOrEqual(back.frame.height, 44)
                evidence("rosary-pass-\(pass)-\(orientation == .portrait ? "portrait" : "landscape")", app: app)
                back.tap()
                waitUntil("Rosary back must close the tool") { !back.exists }
                openRosaryFromCatalog(app)
                XCTAssertEqual(rosaryCount(app), count + 1, "Real Back and catalog reopening must preserve the count")
            }
        }
    }

    @MainActor
    func testMotionSettingPersistsAcrossNativeDeepLinks() {
        let app = launchGuest()
        let homeLogin = app.buttons["Đăng nhập ngay"].firstMatch
        guard homeLoginReady(homeLogin, app: app) else { return }
        homeLogin.tap()
        XCTAssertFalse(app.buttons["Đăng nhập bằng Facebook"].exists)
        for label in ["Đăng nhập bằng Google", "Đăng nhập bằng Apple"] {
            let provider = app.buttons[label].firstMatch
            reveal(provider, app: app)
            XCTAssertTrue(insideViewport(provider, app: app), "Banner must open the real account page with a reachable provider: \(label)")
            XCTAssertTrue(provider.isEnabled)
        }
        evidence("home-login-banner-opens-native-account-providers", app: app)
        tap(app.buttons["Quay lại"].firstMatch, app: app)
        waitUntil("Account Back must leave the provider subpage before opening settings") {
            !app.buttons["Đăng nhập bằng Google"].firstMatch.exists
        }
        openSettings(app)
        openAppearance(app)
        XCTAssertEqual(checked(app.switches["Hiệu ứng chuyển động"].firstMatch), true, "Fresh install enables motion")
        setMotion(false, app: app)
        evidence("motion-off", app: app)
        // Một deep link ấm và một deep link lạnh kiểm chứng chính contract native, không dùng cho mọi bước điều hướng.
        app.open(URL(string: "vn.quangtu.tutam:///tu-tap?action=rosary")!)
        let startDialog = app.otherElements["Bắt Đầu Lần Hạt?, hộp thoại web"].firstMatch
        XCTAssertTrue(startDialog.waitForExistence(timeout: 20), "Warm rosary deep link must expose its actual confirmation dialog")
        let start = startDialog.buttons["Bắt đầu 🙏"].firstMatch
        XCTAssertTrue(start.waitForExistence(timeout: 15), "Rosary confirmation must expose its actual Start action")
        // Modal ẩn footer khỏi AX; không gọi reveal toàn trang trong lúc deep link đang mở modal.
        let viewport = app.frame
        let modalFrame = startDialog.frame
        let startFrame = start.frame
        guard startFrame.width >= 44 && startFrame.height >= 44 && viewport.contains(startFrame) &&
            modalFrame.contains(startFrame) && start.isHittable else {
            evidence("warm-rosary-start-invalid-geometry", app: app)
            XCTFail("Warm rosary Start must be visible and hittable inside the actual modal")
            return
        }
        app.coordinate(withNormalizedOffset: CGVector(dx: (startFrame.midX - viewport.minX) / viewport.width,
            dy: (startFrame.midY - viewport.minY) / viewport.height)).press(forDuration: 0.08)
        waitUntil("Warm rosary Start must close its confirmation dialog") { !startDialog.exists }
        evidence("rosary-motion-off", app: app)
        tap(app.buttons["Quay lại tu tập"].firstMatch, app: app)
        openSettings(app)
        openAppearance(app)
        XCTAssertEqual(checked(app.switches["Hiệu ứng chuyển động"].firstMatch), false)
        app.terminate()
        app.open(URL(string: "vn.quangtu.tutam:///ca-nhan?tab=settings")!)
        XCTAssertTrue(app.switches["Bật thông báo nhắc nhở tu tập"].firstMatch.waitForExistence(timeout: 30))
        openAppearance(app)
        XCTAssertEqual(checked(app.switches["Hiệu ứng chuyển động"].firstMatch), false, "Motion setting must survive native cold deep link")
        setMotion(true, app: app)
        evidence("motion-restored", app: app)
    }

    @MainActor
    func testExistingNotificationButtonDeliversToSystemNotificationCenter() {
        let app = launchGuest()
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        openSettings(app)
        XCTAssertEqual(checked(app.switches["Bật thông báo nhắc nhở tu tập"].firstMatch), true)
        let sound = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Âm thanh & rung thông báo Âm thanh:")).firstMatch
        // Bấm summary thật sau khi đã cuộn nó vào viewport; không dựa vào exists của children mounted ẩn.
        reveal(sound, app: app)
        evidence("notification-sound-before", app: app)
        tap(sound, app: app)
        evidence("notification-sound-after", app: app)
        let send = app.buttons["🔔 Gửi thử thông báo mẫu"].firstMatch
        tap(send, app: app)
        allowExpectedNotificationPermission(app)
        // Nút trở lại tên thường sau promise schedule. Toast chỉ tồn tại 2.8s; bằng chứng cuối vẫn phải là iOS nhận thông báo.
        XCTAssertTrue(send.waitForExistence(timeout: 20), "Native test scheduling must finish before backgrounding")
        evidence("notification-scheduled", app: app)
        XCUIDevice.shared.press(.home)
        XCTAssertTrue(springboard.wait(for: .runningForeground, timeout: 10))
        let top = springboard.coordinate(withNormalizedOffset: CGVector(dx: 0.12, dy: 0.01))
        let bottom = springboard.coordinate(withNormalizedOffset: CGVector(dx: 0.12, dy: 0.80))
        top.press(forDuration: 0.1, thenDragTo: bottom)
        let title = "🔔 Kiểm tra thông báo Tu Tập"
        let body = "Nhắc nhở tu tập hoạt động tốt!"
        let deliveredTitle = springboard.staticTexts[title].firstMatch
        let deliveredBody = springboard.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", body)).firstMatch
        let grouped = springboard.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@ AND label CONTAINS %@", title, body)).firstMatch
        waitUntil("OS Notification Center must expose the exact test notification title and body", timeout: 25) {
            (deliveredTitle.exists && deliveredBody.exists) || grouped.exists
        }
        evidence("notification-system-received", app: springboard)
    }
}

final class NativeExtendedGuestTests: NativeGuestUITestCase {
    @MainActor
    func testNativeScriptureCopyingReferenceAndGhost() {
        let app = launchGuest()
        app.open(URL(string: "vn.quangtu.tutam:///tu-tap/chep-kinh")!)
        tap(app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Kinh Địa Tạng Bồ Tát Bổn Nguyện")).firstMatch, app: app)
        tap(app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Tôi đã đọc, tiếp tục chép")).firstMatch, app: app)

        let ghost = app.switches["Chữ mờ"].firstMatch
        XCTAssertTrue(ghost.waitForExistence(timeout: 20))
        XCTAssertEqual(ghost.value as? String, "0")
        tap(ghost, app: app)
        XCTAssertEqual(ghost.value as? String, "1")

        let editor = app.textViews["Tự gõ bản chép của bạn"].firstMatch
        let reference = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@", "Nội dung kinh mẫu")).firstMatch
        let laterWord = reference.staticTexts["GIỚI"].firstMatch
        let header = app.buttons["Đổi kinh hoặc phẩm"].firstMatch
        tap(editor, app: app)
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(reference.exists)
        XCTAssertFalse(laterWord.exists && reference.frame.contains(laterWord.frame), "The later word must start outside the reference viewport")
        evidence("copy-ghost-keyboard-before-typing", app: app)

        editor.typeText("TINH KHAU NGHIEP CHON NGON Tu ri tu ri, ma ha tu ri tu tu ri, ta ba ha (3 lan). TINH TAM NGHIEP CHAN NGON An ta pha ba pha, truat da ta pha, dat ma ta pha, ba pha truat do ham (3 lan). TINH PHAP GIOI")
        waitUntil("The editor must accept the text and the reference must scroll the later word into view", timeout: 20) {
            (editor.value as? String)?.range(of: "GIOI", options: [.caseInsensitive, .diacriticInsensitive]) != nil
                && laterWord.exists && reference.frame.contains(laterWord.frame)
        }
        XCTAssertTrue(app.keyboards.firstMatch.exists)
        XCTAssertEqual(ghost.value as? String, "1")
        XCTAssertTrue(insideViewport(header, app: app))
        let statusBar = app.statusBars.firstMatch
        if statusBar.exists && !statusBar.frame.isEmpty {
            XCTAssertGreaterThanOrEqual(header.frame.minY, statusBar.frame.maxY - 1)
        }
        evidence("copy-ghost-keyboard-reference-followed", app: app)
    }

    @MainActor
    private func selectPracticePaper(_ label: String, app: XCUIApplication) {
        let toolbar = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", "Tùy chỉnh chế độ tu tập")).firstMatch
        XCTAssertTrue(toolbar.waitForExistence(timeout: 15), "Practice must expose its actual customization toolbar")
        let initialPaper = labelledToggle(label, app: app)
        // WebKit may clamp offscreen-left AX frames to the toolbar's left edge.
        // Use that geometry only to prioritize a direction, then search both ways.
        let preferRight = initialPaper.exists && initialPaper.frame.minX <= toolbar.frame.minX
        for swipeRight in [preferRight, !preferRight] {
            for step in 0...8 {
                let paper = labelledToggle(label, app: app)
                let frame = paper.exists ? paper.frame : .zero
                if paper.exists && app.frame.contains(frame) && toolbar.frame.contains(frame) && paper.isHittable {
                    paper.tap()
                    XCTAssertEqual(checked(paper), true, "The named paper must become selected after one real tap")
                    return
                }
                if step < 8 {
                    let barFrame = toolbar.frame
                    let viewport = app.frame
                    // Stay in the verified bottom padding, clear of the native speed select.
                    let startPoint = CGPoint(x: barFrame.midX + (swipeRight ? -0.125 : 0.125) * barFrame.width, y: barFrame.maxY - 4)
                    let endPoint = CGPoint(x: barFrame.midX + (swipeRight ? 0.125 : -0.125) * barFrame.width, y: barFrame.maxY - 4)
                    guard barFrame.width > 0, barFrame.height > 8,
                          viewport.contains(barFrame),
                          barFrame.contains(startPoint), barFrame.contains(endPoint),
                          viewport.contains(startPoint), viewport.contains(endPoint) else {
                        evidence("invalid-practice-toolbar-geometry-" + label, app: app)
                        XCTFail("Practice toolbar must expose a safe contained drag path")
                        return
                    }
                    let start = toolbar.coordinate(withNormalizedOffset: CGVector(dx: (startPoint.x - barFrame.minX) / barFrame.width, dy: (startPoint.y - barFrame.minY) / barFrame.height))
                    let end = toolbar.coordinate(withNormalizedOffset: CGVector(dx: (endPoint.x - barFrame.minX) / barFrame.width, dy: (endPoint.y - barFrame.minY) / barFrame.height))
                    start.press(forDuration: 0.1, thenDragTo: end, withVelocity: .slow, thenHoldForDuration: 0.1)
                }
            }
        }
        evidence("unreachable-practice-paper-" + label, app: app)
        XCTFail("The actual horizontal toolbar must reveal the requested paper: \(label)")
    }

    @MainActor
    func testOfflineLibrarySearchChapterReadingAndLargeTextRotation() {
        let app = launchGuest()
        navigate("Kinh sách", app: app)
        let search = librarySearch(app)
        search.tap()
        search.typeText("Kinh Địa Tạng Bồ Tát Bổn Nguyện Giảng Giải")
        let book = app.buttons["Đọc ngay Kinh Địa Tạng Bồ Tát Bổn Nguyện Giảng Giải"].firstMatch
        tap(book, app: app)
        let chapter = app.buttons.matching(NSPredicate(format: "label CONTAINS[cd] %@", "Duyên Khởi Của Pháp Hội Giảng Kinh")).firstMatch
        tap(chapter, app: app)
        let body = app.staticTexts.matching(NSPredicate(format: "label CONTAINS[cd] %@", "Tất cả mọi nơi đều là đạo tràng")).firstMatch
        XCTAssertTrue(body.waitForExistence(timeout: 30), "Bundled chapter body must render while production hosts are blocked")
        let larger = app.buttons["Chữ lớn hơn"].firstMatch
        reveal(larger, app: app)
        XCTAssertGreaterThanOrEqual(larger.frame.width, 44)
        XCTAssertGreaterThanOrEqual(larger.frame.height, 44)
        for _ in 0..<3 { larger.tap() }
        XCTAssertTrue(app.staticTexts["18px"].firstMatch.waitForExistence(timeout: 10), "Reader font control must reflect three real increments")
        var purplePaper = labelledToggle("Nền giấy Tím nhạt", app: app)
        if !purplePaper.exists || !insideViewport(purplePaper, viewport: app.frame) || !purplePaper.isHittable {
            tap(app.buttons["Công cụ"].firstMatch, app: app)
        }
        for _ in 0..<8 {
            purplePaper = labelledToggle("Nền giấy Tím nhạt", app: app)
            if purplePaper.exists && insideViewport(purplePaper, viewport: app.frame) && purplePaper.isHittable { break }
            let next = app.buttons["Cuộn thanh công cụ sang phải"].firstMatch
            guard next.exists && next.isEnabled else { break }
            tap(next, app: app)
        }
        purplePaper = labelledToggle("Nền giấy Tím nhạt", app: app)
        tap(purplePaper, app: app)
        XCTAssertEqual(checked(purplePaper), true, "The real reader must select its purple paper")
        evidence("offline-reader-purple-header-and-system-bars", app: app)
        for _ in 0..<8 {
            let automatic = labelledToggle("Nền giấy Tự động", app: app)
            if automatic.exists && insideViewport(automatic, viewport: app.frame) && automatic.isHittable { break }
            let previous = app.buttons["Cuộn thanh công cụ sang trái"].firstMatch
            guard previous.exists && previous.isEnabled else { break }
            tap(previous, app: app)
        }
        let automaticPaper = labelledToggle("Nền giấy Tự động", app: app)
        tap(automaticPaper, app: app)
        XCTAssertEqual(checked(automaticPaper), true, "Reader Auto must be selectable through the actual toolbar")
        evidence("offline-reader-auto-paper", app: app)
        let closeTools = app.buttons["Đóng công cụ"].firstMatch
        if closeTools.exists && closeTools.isHittable { closeTools.tap() }
        evidence("offline-reader-large-text-portrait", app: app)
        rotate(.landscapeLeft, app: app)
        let backToContents = app.buttons["Mục lục"].firstMatch
        reveal(backToContents, app: app)
        XCTAssertGreaterThanOrEqual(backToContents.frame.width, 44)
        XCTAssertGreaterThanOrEqual(backToContents.frame.height, 44)
        evidence("offline-reader-large-text-landscape", app: app)
        backToContents.tap()
        XCTAssertTrue(chapter.waitForExistence(timeout: 15), "Back from chapter must return to real table of contents")
        tap(app.buttons["Quay lại"].firstMatch, app: app)
        XCTAssertTrue(librarySearch(app).isHittable, "Closing table of contents must return to the searchable library")
        rotate(.portrait, app: app)
        openSettings(app)
        openAppearance(app)
        tap(labelledToggle("Tối", app: app), app: app)
        app.open(URL(string: "vn.quangtu.tutam:///tu-tap?article=tt_1780910773084&chap=3")!)
        let practice = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Vào chế độ tu tập")).firstMatch
        tap(practice, app: app)
        tap(app.buttons["Bắt đầu 🙏"].firstMatch, app: app)
        XCTAssertTrue(app.staticTexts["PHẨM 3: QUÁN CHÚNG SINH NGHIỆP DUYÊN"].firstMatch.waitForExistence(timeout: 20))
        selectPracticePaper("Tím nhạt", app: app)
        evidence("practice-purple-shared-theme-header-toolbar-footer", app: app)
        selectPracticePaper("Trắng", app: app)
        evidence("practice-white-shared-theme-header-toolbar-footer", app: app)
        selectPracticePaper("Tự động", app: app)
        evidence("practice-auto-shared-theme", app: app)
        let collapseControls = app.buttons["Thu gọn công cụ tu tập"].firstMatch
        tap(collapseControls, app: app)
        let increment = app.buttons["+1"].firstMatch
        let decrement = app.buttons["-1"].firstMatch
        let openControls = app.buttons["Mở công cụ tu tập"].firstMatch
        waitUntil("Collapsed practice controls must disappear from accessibility") {
            !increment.exists && !decrement.exists && openControls.exists
        }
        XCTAssertTrue(insideViewport(openControls, app: app) && openControls.isHittable, "Collapsed practice controls must retain a reachable opener")
        openControls.tap()
        XCTAssertTrue(increment.waitForExistence(timeout: 15))
        let controls = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@", "Bảng điều khiển tu tập")).firstMatch
        XCTAssertTrue(controls.staticTexts["0"].firstMatch.exists, "Fresh practice counter must start at zero")
        tap(increment, app: app)
        waitUntil("One increment must increase the real practice counter to exactly one") { controls.staticTexts["1"].firstMatch.exists }
        tap(decrement, app: app)
        waitUntil("One decrement must restore the real practice counter to zero") { controls.staticTexts["0"].firstMatch.exists }
        tap(collapseControls, app: app)
        waitUntil("Reclosed practice controls must remain absent from accessibility") {
            !increment.exists && !decrement.exists && openControls.exists
        }
        XCTAssertTrue(insideViewport(openControls, app: app) && openControls.isHittable, "Reclosed practice controls must keep their opener reachable")
        evidence("practice-controls-collapsed-accessibility", app: app)
    }

    @MainActor
    func testNativeCatalogExcludesWebOnlyBookAndSearchCanBeCleared() {
        let app = launchGuest()
        navigate("Kinh sách", app: app)
        let search = librarySearch(app)
        search.tap()
        search.typeText("Kinh Địa Tạng Bồ Tát Bổn Nguyện Giảng Giải")
        let book = app.buttons["Đọc ngay Kinh Địa Tạng Bồ Tát Bổn Nguyện Giảng Giải"].firstMatch
        XCTAssertTrue(book.waitForExistence(timeout: 25))
        let clear = app.buttons["Xóa tìm kiếm"].firstMatch
        tap(clear, app: app)
        search.tap()
        search.typeText("Muôn Kiếp Nhân Sinh")
        XCTAssertTrue(app.staticTexts["Không tìm thấy bài viết phù hợp."].firstMatch.waitForExistence(timeout: 20))
        XCTAssertEqual(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@ AND label CONTAINS[cd] %@", "Đọc ngay", "Muôn Kiếp Nhân Sinh")).count, 0)
        evidence("native-web-only-search-empty", app: app)
        tap(clear, app: app)
        search.tap()
        search.typeText("Kinh Địa Tạng Bồ Tát Bổn Nguyện Giảng Giải")
        XCTAssertTrue(book.waitForExistence(timeout: 20), "Normal offline catalog must recover after clearing the excluded title")
    }

    @MainActor
    func testLightDarkSettingsAndGuestDeepLinkNavigation() {
        let app = launchGuest()
        for (label, theme) in [("Tối", "dark"), ("Trắng", "light")] {
            openSettings(app, captureNavigationEvidence: false)
            openAppearance(app)
            let choice = labelledToggle(label, app: app)
            tap(choice, app: app)
            waitUntil("Selected theme must become active") { self.checked(choice) == true }
            let summary = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Giao diện & ngôn ngữ " + label)).firstMatch
            XCTAssertTrue(summary.waitForExistence(timeout: 15), "Settings summary must render the selected theme before screenshot")
            evidence("settings-" + theme, app: app)
            navigate("Kinh sách", app: app, captureNavigationEvidence: false)
            let search = app.searchFields["Tìm kiếm bài viết"].firstMatch
            XCTAssertTrue(search.waitForExistence(timeout: 20), "Fresh library must expose its actual search field")
            let navigation = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@ OR label BEGINSWITH %@", "Điều hướng nhanh", "Quick navigation")).firstMatch
            XCTAssertTrue(navigation.waitForExistence(timeout: 20), "Library search must be checked against the actual footer")
            let searchFrame = search.frame
            let footerFrame = navigation.frame
            guard searchFrame.width > 0 && searchFrame.height > 0 && app.frame.contains(searchFrame)
                && footerFrame.width > 0 && footerFrame.height > 0 && searchFrame.maxY <= footerFrame.minY
                && search.isHittable else {
                evidence("library-search-invalid-geometry-" + theme, app: app)
                XCTFail("Fresh library search must be fully visible and hittable above the actual footer")
                return
            }
            evidence("library-" + theme, app: app)
            navigate("Tu tập", app: app, captureNavigationEvidence: false)
            XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Lần tràng hạt")).firstMatch.waitForExistence(timeout: 20))
            evidence("practice-" + theme, app: app)
        }
        openSettings(app, captureNavigationEvidence: false)
        openAppearance(app)
        for label in ["Tự động", "Trắng", "Be nhạt", "Xám", "Tím nhạt", "Tối"] {
            XCTAssertTrue(labelledToggle(label, app: app).exists, "Settings must expose all six theme choices: \(label)")
        }
        let purple = labelledToggle("Tím nhạt", app: app)
        tap(purple, app: app)
        XCTAssertEqual(checked(purple), true)
        evidence("settings-purple-six-choice-grid", app: app)
        app.terminate()
        app.launch()
        openSettings(app, captureNavigationEvidence: false)
        openAppearance(app)
        XCTAssertEqual(checked(labelledToggle("Tím nhạt", app: app)), true, "The selected app palette must survive a native relaunch")
        let automatic = labelledToggle("Tự động", app: app)
        tap(automatic, app: app)
        XCTAssertEqual(checked(automatic), true)
        tap(labelledToggle("Trắng", app: app), app: app)
    }

    @MainActor
    func testMeditationPauseResumeSurvivesNativeBackgrounding() {
        let app = launchGuest()
        navigate("Tu tập", app: app)
        tap(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Thiền định")).firstMatch, app: app)
        tap(app.buttons["Bắt đầu thiền"].firstMatch, app: app)
        tap(app.buttons["Tạm dừng"].firstMatch, app: app)
        let resume = app.buttons["Tiếp tục"].firstMatch
        XCTAssertTrue(resume.waitForExistence(timeout: 10))
        evidence("meditation-paused", app: app)
        XCUIDevice.shared.press(.home)
        app.activate()
        XCTAssertTrue(resume.waitForExistence(timeout: 15), "Explicit pause must remain after native background/foreground")
        tap(resume, app: app)
        XCTAssertTrue(app.buttons["Tạm dừng"].firstMatch.waitForExistence(timeout: 10))
        evidence("meditation-resumed", app: app)
    }
}

final class NativePersonalReminderTests: NativeGuestUITestCase {
    @MainActor
    private func waitPersonalUntil(_ message: String, timeout: TimeInterval = 15, _ condition: @escaping () -> Bool) {
        if condition() { return }
        waitUntil(message, timeout: timeout, condition)
    }

    @MainActor
    private func existsOrWait(_ element: XCUIElement, timeout: TimeInterval) -> Bool {
        element.exists || element.waitForExistence(timeout: timeout)
    }

    @MainActor
    private func allowPersonalNotificationPermission(_ app: XCUIApplication) {
        allowExpectedNotificationPermission(app)
    }

    @MainActor
    private func openPersonal(_ app: XCUIApplication, requestPermission: Bool = false) {
        let enabled = app.switches["Ngày Lễ Cá Nhân"].firstMatch
        if !enabled.exists || enabled.frame.width == 0 || enabled.frame.height == 0 {
            let summary = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Ngày lễ cá nhân ")).firstMatch
            tap(summary, app: app)
        }
        if requestPermission { allowPersonalNotificationPermission(app) }
        reveal(enabled, app: app)
        XCTAssertEqual(checked(enabled), true, "Personal reminders must be enabled for these scenarios")
    }

    @MainActor
    private func relaunchPersonal(_ app: XCUIApplication, captureNavigationEvidence: Bool = true) {
        app.terminate()
        app.launch()
        XCTAssertTrue(existsOrWait(app.links["Cá nhân"].firstMatch, timeout: 30))
        openSettings(app, captureNavigationEvidence: captureNavigationEvidence)
        openPersonal(app)
    }

    @MainActor
    private func namedControl(_ caption: String, app: XCUIApplication) -> XCUIElement {
        dismissPersonalKeyboard(app)
        let matches = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", caption))
        // WebKit iOS có thể xuất HTML select thành Other có value, không nhất thiết PopUpButton.
        let findControl = { () -> XCUIElement? in
            var fallback: XCUIElement?
            for element in matches.allElementsBoundByIndex {
                // Caption Other không có value; select Other có value dù AX nội bộ chỉ cao 35pt.
                guard element.elementType != .staticText else { continue }
                let frame = element.frame
                guard frame.width > 0 && frame.height > 0 &&
                    ((element.value as? String).map { !$0.isEmpty } ?? false) else { continue }
                if fallback == nil { fallback = element }
                if element.label == caption { return element }
            }
            return fallback
        }
        var found = findControl()
        if found == nil {
            // Offscreen controls may be absent from AX until their caption is revealed.
            reveal(matches.firstMatch, app: app)
            found = findControl()
        }
        guard let control = found else {
            evidence("missing-labelled-control-" + caption, app: app)
            XCTFail("Expected a labelled interactive control for \(caption)")
            return matches.firstMatch
        }
        reveal(control, app: app)
        return control
    }

    @MainActor
    private func finishNativePicker(_ app: XCUIApplication) {
        let done = app.buttons.matching(NSPredicate(format: "label IN %@", ["Done", "Xong", "Hoàn tất"] as NSArray)).firstMatch
        XCTAssertTrue(existsOrWait(done, timeout: 10), "Native picker must expose its real Done action")
        done.tap()
    }

    @MainActor
    private func dismissPersonalKeyboard(_ app: XCUIApplication) {
        guard app.keyboards.firstMatch.exists else { return }
        // Nút Done của input accessory đóng bàn phím, không gửi form bằng phím Return.
        let done = app.toolbars.buttons.matching(NSPredicate(format: "label IN %@", ["Done", "Xong", "Hoàn tất"] as NSArray)).firstMatch
        XCTAssertTrue(existsOrWait(done, timeout: 5), "Focused personal-event field must expose keyboard Done")
        done.tap()
        waitPersonalUntil("Personal-event keyboard must dismiss before scrolling controls") { !app.keyboards.firstMatch.exists }
    }

    @MainActor
    private func selectPersonalSegment(_ label: String, app: XCUIApplication) {
        dismissPersonalKeyboard(app)
        // HTML button aria-pressed được WebKit iOS xuất thành Switch, không phải UIKit Button.
        let choice = labelledToggle(label, app: app)
        reveal(choice, app: app)
        let selected = checked(choice)
        XCTAssertNotNil(selected, "Personal segmented control must expose its actual pressed state")
        if selected != true { choice.tap() }
        waitPersonalUntil("Personal segmented control must retain selected state \(label)") { self.checked(app.switches[label].firstMatch) == true }
    }

    @MainActor
    private func selectWebDialogOption(_ dialog: XCUIElement, option: String, app: XCUIApplication) {
        let close = dialog.buttons["Đóng bảng chọn"].firstMatch
        XCTAssertTrue(existsOrWait(close, timeout: 5), "Actual web select dialog must expose its close control")
        let header = dialog.otherElements.matching(NSPredicate(format: "label IN %@", ["biểu ngữ", "banner"] as NSArray)).firstMatch
        XCTAssertTrue(existsOrWait(header, timeout: 5), "Web select must expose its complete fixed header")
        let choice = dialog.switches[option].firstMatch
        for _ in 0..<14 {
            let viewport = app.frame
            let bounds = dialog.frame.intersection(viewport)
            let headerFrame = header.frame
            guard headerFrame.width > 0 && headerFrame.height > 0 && bounds.contains(headerFrame) else {
                XCTFail("Actual web select header must remain fully visible")
                return
            }
            let contentTop = headerFrame.maxY
            if choice.exists {
                let frame = choice.frame
                if frame.width > 0 && frame.height > 0 && frame.minY >= contentTop
                    && bounds.contains(frame) && choice.isHittable {
                    choice.tap()
                    waitPersonalUntil("Selecting a web dialog option must close that dialog") { !dialog.exists }
                    return
                }
                // AX làm phẳng div overflow: chỉ kéo trong các hàng thực đang thấy và hittable,
                // không dùng toàn bộ backdrop làm khung cuộn. Bước nhỏ dần để không vượt hàng đích.
                let visibleRows = dialog.switches.allElementsBoundByIndex.compactMap { row -> CGRect? in
                    let rect = row.frame
                    return rect.width > 0 && rect.height > 0 && rect.minY >= contentTop &&
                        bounds.contains(rect) && row.isHittable ? rect : nil
                }
                let scrollArea = visibleRows.reduce(CGRect.null) { $0.union($1) }
                guard !scrollArea.isNull && scrollArea.height > 0 else {
                    evidence("missing-web-select-scroll-area", app: app)
                    XCTFail("Web select scrolling requires actual visible option rows below its fixed header")
                    return
                }
                if scrollArea.contains(frame) && choice.isHittable {
                    choice.tap()
                    waitPersonalUntil("Selecting a web dialog option must close that dialog") { !dialog.exists }
                    return
                }
                let movingUp: Bool
                if frame.maxY > scrollArea.maxY { movingUp = true }
                else if frame.minY < scrollArea.minY { movingUp = false }
                else {
                    XCTFail("Fully visible requested web select option must be hittable")
                    return
                }
                let delta = min(abs(frame.midY - scrollArea.midY), scrollArea.height / 2)
                guard delta > 0 else {
                    XCTFail("Centered requested web select option must be hittable")
                    return
                }
                let startY = scrollArea.midY + (movingUp ? delta / 2 : -delta / 2)
                let endY = scrollArea.midY + (movingUp ? -delta / 2 : delta / 2)
                let x = (scrollArea.midX - viewport.minX) / viewport.width
                let start = app.coordinate(withNormalizedOffset: CGVector(dx: x, dy: (startY - viewport.minY) / viewport.height))
                let end = app.coordinate(withNormalizedOffset: CGVector(dx: x, dy: (endY - viewport.minY) / viewport.height))
                start.press(forDuration: 0.05, thenDragTo: end)
            } else {
                XCTFail("Requested option must exist inside the actual web select dialog")
                return
            }
        }
        evidence("unreachable-web-select-option-" + option, app: app)
        XCTFail("Requested web select option must become visible and hittable within bounded scrolling")
    }

    @MainActor
    private func select(_ caption: String, option: String, app: XCUIApplication) {
        if caption == "Chu kỳ:" {
            selectPersonalSegment(option, app: app)
            return
        }
        let control = namedControl(caption, app: app)
        if (control.value as? String) == option { return }
        control.tap()
        let webDialog = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@ AND (label CONTAINS %@ OR label CONTAINS %@)", caption, "hộp thoại web", "web dialog")).firstMatch
        if existsOrWait(webDialog, timeout: 2) {
            selectWebDialogOption(webDialog, option: option, app: app)
            waitPersonalUntil("Web select must retain the chosen option \(option)") { (control.value as? String) == option }
            return
        }
        let wheel = app.pickerWheels.firstMatch
        if existsOrWait(wheel, timeout: 2) {
            wheel.adjust(toPickerWheelValue: option)
            finishNativePicker(app)
        } else {
            // Chọn nút thật trong popover của UIKit; không chạm option Switch ẩn của WebKit.
            let choice = app.buttons.matching(identifier: option).firstMatch
            XCTAssertTrue(existsOrWait(choice, timeout: 10), "Native select popover must expose the requested option button")
            choice.tap()
        }
        waitPersonalUntil("Native select must retain the chosen option \(option)") { (control.value as? String) == option }
    }

    @MainActor
    private func replaceName(_ text: String, app: XCUIApplication, editing: Bool = false) {
        let field = app.textFields["Tên ngày lễ *"].firstMatch
        if editing {
            // WK AX's inferred tap point can land outside the input after scrolling.
            revealContentControl(field, app: app)
            let bounds = field.frame
            let viewport = app.frame
            guard bounds.width > 0 && bounds.height > 0 && viewport.contains(bounds) && field.isHittable else {
                evidence("invalid-personal-title-first-gesture-geometry", app: app)
                XCTFail("The real title field must be visible before its single edit tap")
                return
            }
            app.coordinate(withNormalizedOffset: CGVector(dx: (bounds.midX - viewport.minX) / viewport.width,
                dy: (bounds.midY - viewport.minY) / viewport.height)).tap()
        } else {
            tap(field, app: app)
        }
        if let existing = field.value as? String, !existing.isEmpty, existing != (field.placeholderValue ?? "") {
            XCTAssertTrue(existsOrWait(app.keyboards.firstMatch, timeout: 10), "Editing an existing title requires the actual keyboard")
            let bounds = field.frame
            let viewport = app.frame
            let keyboard = app.keyboards.firstMatch.frame
            guard bounds.width > 0 && bounds.height > 0 && viewport.contains(bounds)
                && bounds.maxY <= keyboard.minY && field.isHittable else {
                evidence("invalid-personal-title-selection-target", app: app)
                XCTFail("Title selection must target the visible field above the keyboard")
                return
            }
            // These short fixture titles fit the visible field. Tap the trailing
            // padding to place the caret at the end, then use real keyboard Delete.
            app.coordinate(withNormalizedOffset: CGVector(dx: (bounds.maxX - 16 - viewport.minX) / viewport.width,
                dy: (bounds.midY - viewport.minY) / viewport.height)).tap()
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: existing.count))
            let titleIsEmpty = { () -> Bool in
                guard let value = field.value as? String else { return false }
                if value.isEmpty { return true }
                guard let placeholder = field.placeholderValue, !placeholder.isEmpty else { return false }
                // WK AX may expose the placeholder as the value of an empty field.
                return value == placeholder
            }
            waitPersonalUntil("Real keyboard deletion must clear the complete existing title") { titleIsEmpty() }
            XCTAssertTrue(titleIsEmpty(), "Replacement cannot continue with any old title characters")
        }
        field.typeText(text)
        waitPersonalUntil("Personal-event title must settle to the exact full typed value") { (field.value as? String) == text }
        XCTAssertEqual(field.value as? String, text)
    }

    @MainActor
    private func saveEvent(_ title: String, app: XCUIApplication) {
        dismissPersonalKeyboard(app)
        let saveButton = app.buttons["Lưu ngày lễ"].firstMatch
        reveal(saveButton, app: app)
        let appFrame = app.frame
        var contentFrame = appFrame
        let navigation = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@ OR label BEGINSWITH %@", "Điều hướng nhanh", "Quick navigation")).firstMatch
        if navigation.exists {
            let footer = navigation.frame
            if footer.minY > contentFrame.minY && footer.minY < contentFrame.maxY { contentFrame.size.height = footer.minY - contentFrame.minY }
        }
        let buttonFrame = saveButton.frame
        XCTAssertGreaterThanOrEqual(buttonFrame.width, 44, "Actual Save control must retain its minimum touch width")
        XCTAssertGreaterThanOrEqual(buttonFrame.height, 44, "Actual Save control must retain its minimum touch height")
        let keyboard = app.keyboards.firstMatch
        guard contentFrame.contains(buttonFrame), !keyboard.exists || !keyboard.frame.intersects(buttonFrame) else {
            evidence("personal-save-invalid-first-gesture-geometry", app: app)
            XCTFail("Actual Save control must be fully visible above the footer and clear of the keyboard")
            return
        }
        saveButton.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).press(forDuration: 0.08)
        let closed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            !app.textFields["Tên ngày lễ *"].firstMatch.exists
        }, object: NSObject())
        if XCTWaiter.wait(for: [closed], timeout: 15) != .completed {
            evidence("personal-save-first-tap-failed", app: app)
            // Lần chạm thứ hai chỉ lấy bằng chứng; không đổi thất bại lưu lần đầu thành PASS.
            let save = app.buttons["Lưu ngày lễ"].firstMatch
            if save.exists {
                var viewport = app.frame
                let navigation = app.otherElements.matching(NSPredicate(format: "label BEGINSWITH %@ OR label BEGINSWITH %@", "Điều hướng nhanh", "Quick navigation")).firstMatch
                if navigation.exists {
                    let footer = navigation.frame
                    if footer.minY > viewport.minY && footer.minY < viewport.maxY { viewport.size.height = footer.minY - viewport.minY }
                }
                let frame = save.frame
                if frame.width > 0 && frame.height > 0 && viewport.contains(frame) {
                    let appFrame = app.frame
                    app.coordinate(withNormalizedOffset: CGVector(dx: (frame.midX - appFrame.minX) / appFrame.width,
                        dy: (frame.midY - appFrame.minY) / appFrame.height)).tap()
                    let diagnosticClosed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
                        !app.textFields["Tên ngày lễ *"].firstMatch.exists
                    }, object: NSObject())
                    let didClose = XCTWaiter.wait(for: [diagnosticClosed], timeout: 10) == .completed
                    evidence("personal-save-diagnostic-second-tap-\(didClose ? "closed" : "open")", app: app)
                } else {
                    evidence("personal-save-diagnostic-invalid-geometry", app: app)
                }
            }
            XCTFail("A valid save must close the draft after one tap; diagnostic second tap cannot satisfy this requirement")
            return
        }
        XCTAssertTrue(existsOrWait(app.staticTexts[title].firstMatch, timeout: 15))
    }

    @MainActor
    private func addEvent(_ title: String, solar: Bool, day: Int, month: Int, app: XCUIApplication) {
        tap(app.buttons["+ Thêm ngày lễ kỷ niệm"].firstMatch, app: app)
        replaceName(title, app: app)
        if solar { selectPersonalSegment("Dương lịch", app: app) }
        select("Chu kỳ:", option: "Hàng năm", app: app)
        select(solar ? "Ngày Dương:" : "Ngày Âm lịch:", option: String(day), app: app)
        select(solar ? "Tháng Dương:" : "Tháng Âm lịch:", option: "Tháng \(month)", app: app)
        saveEvent(title, app: app)
    }

    @MainActor
    private func editEvent(_ title: String, app: XCUIApplication) {
        let name = app.textFields["Tên ngày lễ *"].firstMatch
        if name.exists && (name.value as? String) == title { return }
        let summary = app.staticTexts[title].firstMatch
        reveal(summary, app: app)
        let titleFrame = summary.frame
        let viewport = app.frame
        let edits = app.buttons.matching(identifier: "Sửa").allElementsBoundByIndex
        let matchingEdits = edits.filter {
            let frame = $0.frame
            return frame.width > 0 && frame.height > 0 && viewport.contains(frame) &&
                frame.minY < titleFrame.maxY && frame.maxY > titleFrame.minY &&
                frame.midX > titleFrame.midX && $0.isHittable
        }
        guard matchingEdits.count == 1, let edit = matchingEdits.first else {
            XCTFail("Expected Edit action beside the named QA event \(title)")
            return
        }
        revealContentControl(edit, app: app)
        let revealedTitleFrame = summary.frame
        let revealedEditFrame = edit.frame
        guard summary.exists && edit.exists && revealedTitleFrame.width > 0 && revealedTitleFrame.height > 0
            && revealedEditFrame.minY < revealedTitleFrame.maxY && revealedEditFrame.maxY > revealedTitleFrame.minY
            && revealedEditFrame.midX > revealedTitleFrame.midX && edit.isHittable else {
            evidence("lost-personal-edit-row-association", app: app)
            XCTFail("Revealed Edit must remain beside the named QA event \(title)")
            return
        }
        // Tap the measured row after scrolling. A WebKit accessibility tap can scroll
        // again while resolving a repeated button label.
        let origin = app.coordinate(withNormalizedOffset: .zero)
        origin.withOffset(CGVector(dx: revealedEditFrame.midX - viewport.minX,
                                   dy: revealedEditFrame.midY - viewport.minY)).tap()
        guard existsOrWait(name, timeout: 15) else {
            evidence("personal-edit-did-not-open", app: app)
            XCTFail("One tap must open the editor for \(title)")
            return
        }
        XCTAssertEqual(name.value as? String, title)
    }

    @MainActor
    private func collapseEditor(_ app: XCUIApplication) {
        tap(app.buttons["Thu gọn"].firstMatch, app: app)
        waitPersonalUntil("Unchanged editor must collapse") { !app.textFields["Tên ngày lễ *"].firstMatch.exists }
    }

    @MainActor
    private func deleteEvent(_ title: String, cancelFirst: Bool = false, app: XCUIApplication) {
        editEvent(title, app: app)
        tap(app.buttons["Xóa ngày lễ"].firstMatch, app: app)
        XCTAssertTrue(existsOrWait(app.staticTexts["Xóa lịch cá nhân?"].firstMatch, timeout: 10))
        if cancelFirst {
            tap(app.buttons["Giữ lại"].firstMatch, app: app)
            XCTAssertTrue(app.staticTexts[title].firstMatch.exists, "Cancel deletion must retain the named saved event")
            tap(app.buttons["Xóa ngày lễ"].firstMatch, app: app)
            XCTAssertTrue(existsOrWait(app.staticTexts["Xóa lịch cá nhân?"].firstMatch, timeout: 10))
        }
        tap(app.buttons["Xóa lịch"].firstMatch, app: app)
        waitPersonalUntil("Confirmed deletion must remove only the named QA event") { !app.staticTexts[title].firstMatch.exists }
    }

    @MainActor
    func testPersonalLunarSolarCrudInvalidDateCancelAndPersistence() {
        let app = launchGuest()
        openSettings(app, captureNavigationEvidence: false)
        openPersonal(app, requestPermission: true)
        let lunar = "QA ngày âm cá nhân"
        let solar = "QA ngày dương cá nhân"
        let edited = "QA ngày dương đã sửa"
        addEvent(lunar, solar: false, day: 15, month: 7, app: app)
        addEvent(solar, solar: true, day: 7, month: 11, app: app)
        editEvent(solar, app: app)
        replaceName(edited, app: app, editing: true)
        saveEvent(edited, app: app)
        relaunchPersonal(app, captureNavigationEvidence: false)
        editEvent(lunar, app: app)
        XCTAssertEqual(namedControl("Ngày Âm lịch:", app: app).value as? String, "15")
        XCTAssertEqual(namedControl("Tháng Âm lịch:", app: app).value as? String, "Tháng 7")
        collapseEditor(app)
        editEvent(edited, app: app)
        XCTAssertEqual(namedControl("Ngày Dương:", app: app).value as? String, "7")
        XCTAssertEqual(namedControl("Tháng Dương:", app: app).value as? String, "Tháng 11")
        select("Ngày Dương:", option: "31", app: app)
        select("Tháng Dương:", option: "Tháng 2", app: app)
        tap(app.buttons["Lưu ngày lễ"].firstMatch, app: app)
        let invalid = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "Ngày dương lịch không hợp lệ.")).firstMatch
        XCTAssertTrue(existsOrWait(invalid, timeout: 15), "31 February must be rejected through the actual UI")
        tap(app.buttons["Đóng"].firstMatch, app: app)
        XCTAssertEqual(app.textFields["Tên ngày lễ *"].firstMatch.value as? String, edited, "Invalid draft must retain its text")
        evidence("personal-invalid-date-retained", app: app)
        // Rời bản nháp chưa hợp lệ không được ghi đè ngày đã lưu trước đó.
        relaunchPersonal(app, captureNavigationEvidence: false)
        editEvent(edited, app: app)
        XCTAssertEqual(namedControl("Ngày Dương:", app: app).value as? String, "7")
        XCTAssertEqual(namedControl("Tháng Dương:", app: app).value as? String, "Tháng 11")
        collapseEditor(app)
        let before = namedControl("Nhắc trước ngày lễ", app: app)
        XCTAssertEqual(checked(before), true)
        before.tap()
        waitPersonalUntil("The timing draft must reflect the disabled before-reminder checkbox") { self.checked(before) == false }
        tap(app.buttons["Hủy"].firstMatch, app: app)
        waitPersonalUntil("Cancel timing changes must restore the saved setting") { self.checked(before) == true }
        deleteEvent(edited, cancelFirst: true, app: app)
        XCTAssertTrue(app.staticTexts[lunar].firstMatch.exists, "Deleting solar event must preserve the other lunar event")
        deleteEvent(lunar, app: app)
        relaunchPersonal(app, captureNavigationEvidence: false)
        XCTAssertFalse(app.staticTexts[edited].firstMatch.exists)
        XCTAssertFalse(app.staticTexts[lunar].firstMatch.exists)
        evidence("personal-crud-persisted-deletion", app: app)
    }

    @MainActor
    private func setReminderTime(_ fire: Date, app: XCUIApplication) {
        let field = namedControl("Nhắc trong ngày lễ — Giờ nhắc:", app: app)
        field.tap()
        let wheels = app.pickerWheels
        XCTAssertTrue(existsOrWait(wheels.firstMatch, timeout: 10), "Actual native time picker must open")
        XCTAssertTrue(wheels.count == 2 || wheels.count == 3, "Time picker must expose hour/minute and optional AM/PM wheels")
        let calendar = Calendar.current
        let hour = calendar.component(.hour, from: fire)
        let minute = calendar.component(.minute, from: fire)
        let hourWheel = wheels.element(boundBy: 0)
        let minuteWheel = wheels.element(boundBy: 1)
        let selectedHour = wheels.count == 3 ? (hour % 12 == 0 ? 12 : hour % 12) : hour
        let currentHour = (hourWheel.value as? String) ?? ""
        hourWheel.adjust(toPickerWheelValue: currentHour.hasPrefix("0") ? String(format: "%02d", selectedHour) : String(selectedHour))
        minuteWheel.adjust(toPickerWheelValue: String(format: "%02d", minute))
        if wheels.count == 3 {
            let period = wheels.element(boundBy: 2)
            let current = ((period.value as? String) ?? "").uppercased()
            let vietnamese = current.contains("SA") || current.contains("CH")
            period.adjust(toPickerWheelValue: hour < 12 ? (vietnamese ? "SA" : "AM") : (vietnamese ? "CH" : "PM"))
        }
        finishNativePicker(app)
        let expected = String(format: "%02d:%02d", hour, minute)
        let value = (field.value as? String) ?? ""
        let normalized = value.replacingOccurrences(of: "\u{202f}", with: " ").replacingOccurrences(of: "\u{00a0}", with: " ")
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.dateFormat = "h:mm a"
        let parsed = format.date(from: normalized)
        XCTAssertTrue(normalized == expected || (parsed.map { calendar.component(.hour, from: $0) == hour && calendar.component(.minute, from: $0) == minute } ?? false), "Native time field must show the requested local hour and minute")
        tap(app.buttons["Lưu cài đặt"].firstMatch, app: app)
        XCTAssertTrue(existsOrWait(app.buttons["Lưu cài đặt"].firstMatch, timeout: 30), "Personal reminder scheduling must finish")
        XCTAssertTrue(existsOrWait(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "Nhắc trong ngày lúc " + expected)).firstMatch, timeout: 10))
    }

    @MainActor
    func testPersonalNearFutureReminderDeliversToSystemAndCanBeDeleted() {
        let app = launchGuest()
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        openSettings(app)
        openPersonal(app, requestPermission: true)
        let name = "QA thông báo ngày lễ riêng"
        // Dư bốn phút để thao tác picker thật trên CI; scheduler dùng cùng giờ địa phương của thiết bị.
        let calendar = Calendar.current
        let candidate = Date().addingTimeInterval(240)
        guard let fire = calendar.date(bySetting: .second, value: 0, of: candidate) else {
            XCTFail("Unable to construct the device-local reminder time")
            return
        }
        addEvent(name, solar: true, day: calendar.component(.day, from: fire), month: calendar.component(.month, from: fire), app: app)
        setReminderTime(fire, app: app)
        XCTAssertGreaterThan(fire.timeIntervalSinceNow, 15, "Real UI setup must finish before the scheduled local time")
        evidence("personal-reminder-scheduled", app: app)
        XCUIDevice.shared.press(.home)
        XCTAssertTrue(springboard.wait(for: .runningForeground, timeout: 10))
        let top = springboard.coordinate(withNormalizedOffset: CGVector(dx: 0.12, dy: 0.01))
        let bottom = springboard.coordinate(withNormalizedOffset: CGVector(dx: 0.12, dy: 0.80))
        top.press(forDuration: 0.1, thenDragTo: bottom)
        let title = "🗓️ Hôm nay: Ngày lễ cá nhân"
        let deliveredTitle = springboard.staticTexts[title].firstMatch
        let deliveredBody = springboard.staticTexts[name].firstMatch
        let grouped = springboard.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@ AND label CONTAINS %@", title, name)).firstMatch
        waitPersonalUntil("iOS must actually deliver the personal anniversary notification with its exact QA event name", timeout: max(30, fire.timeIntervalSinceNow + 45)) {
            (deliveredTitle.exists && deliveredBody.exists) || grouped.exists
        }
        evidence("personal-reminder-system-received", app: springboard)
        app.activate()
        openSettings(app)
        openPersonal(app)
        deleteEvent(name, app: app)
        relaunchPersonal(app)
        XCTAssertFalse(app.staticTexts[name].firstMatch.exists, "Cleanup must remove only the synthetic QA anniversary")
        evidence("personal-reminder-cleanup", app: app)
    }
}

final class NativeSimulatorSnapshotTests: NativeGuestUITestCase {
    @MainActor
    func testRosaryManualAndAutomaticAudioProgress() {
        let app = launchGuest()
        navigate("Tu tập", app: app)
        openRosaryFromCatalog(app)

        let baseline = rosaryCount(app)
        tap(app.buttons["Niệm một hạt"].firstMatch, app: app)
        waitUntil("One manual tap must add exactly one bead") {
            app.staticTexts[String(baseline + 1)].firstMatch.exists
        }
        XCTAssertEqual(rosaryCount(app), baseline + 1)

        let expanded = app.buttons["Thu gọn"].firstMatch
        if !expanded.exists {
            tap(app.buttons["Tùy chọn"].firstMatch, app: app)
        }
        XCTAssertTrue(expanded.waitForExistence(timeout: 10), "Rosary settings must be expanded")
        tap(labelledToggle("Tự động gõ mõ", app: app), app: app)
        tap(app.buttons["Bắt đầu gõ mõ"].firstMatch, app: app)

        let pause = app.buttons["Tạm dừng"].firstMatch
        XCTAssertTrue(pause.waitForExistence(timeout: 30), "The native audio transport must start")
        waitUntil("Native playback must advance the bead counter", timeout: 20) {
            self.rosaryCount(app) >= baseline + 4
        }
        evidence("rosary-auto-playing", app: app)
        tap(pause, app: app)

        let resume = app.buttons["Tiếp tục"].firstMatch
        XCTAssertTrue(resume.waitForExistence(timeout: 12))
        let pausedCount = rosaryCount(app)
        let unexpectedlyAdvanced = XCTNSPredicateExpectation(
            predicate: NSPredicate { _, _ in self.rosaryCount(app) != pausedCount },
            object: NSObject()
        )
        unexpectedlyAdvanced.isInverted = true
        XCTAssertEqual(XCTWaiter.wait(for: [unexpectedlyAdvanced], timeout: 4), .completed)

        tap(resume, app: app)
        waitUntil("Playback must advance again after resume", timeout: 20) {
            self.rosaryCount(app) >= pausedCount + 2
        }
        tap(app.buttons["Dừng nhịp"].firstMatch, app: app)
        XCTAssertTrue(app.buttons["Bắt đầu gõ mõ"].firstMatch.waitForExistence(timeout: 10))
    }

    @MainActor
    func testVegetarianMenuTabsAndRecipeRemainReachable() {
        let app = launchGuest()
        navigate("Tu tập", app: app)
        let title = "Ăn Chay Thanh Tịnh"
        let label = NSPredicate(format: "label BEGINSWITH[c] %@", title)
        let card = app.links.matching(label).firstMatch
        revealContentControl(card, app: app)
        card.tap()
        XCTAssertTrue(app.staticTexts[title].firstMatch.waitForExistence(timeout: 20), "The menu page must open")

        tap(labelledToggle("Lịch tuần & Đi chợ", app: app), app: app)
        let menu = labelledToggle("Menu món", app: app)
        tap(menu, app: app)
        XCTAssertTrue(insideViewport(menu, app: app))
        XCTAssertTrue(app.searchFields["Tìm tên món hoặc nguyên liệu"].firstMatch.waitForExistence(timeout: 15))
        evidence("vegetarian-menu-tabs", app: app)

        tap(labelledToggle("Hôm nay", app: app), app: app)
        let recipe = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Xem")).firstMatch
        tap(recipe, app: app)
        XCTAssertTrue(app.staticTexts["Nguyên liệu"].firstMatch.waitForExistence(timeout: 15))
        evidence("vegetarian-offline-recipe", app: app)
    }
}
