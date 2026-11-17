import AppKit
import UserNotifications

@MainActor
final class PackNotifications: NSObject, UNUserNotificationCenterDelegate {
    static let shared = PackNotifications()
    private weak var state: AppState?
    private var started = false
    private let center = UNUserNotificationCenter.current()

    func start(state: AppState) {
        self.state = state
        guard !started else { return }
        started = true
        center.delegate = self
        Task {
            let settings = await center.notificationSettings()
            if settings.authorizationStatus == .notDetermined {
                _ = try? await center.requestAuthorization(options: [.alert, .sound])
            }
            await refreshStatus()
        }
    }

    func refreshStatus() async {
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral: state?.notificationStatus = "系统通知已允许"
        case .denied: state?.notificationStatus = "系统通知已关闭，请在系统设置中开启"
        case .notDetermined: state?.notificationStatus = "系统通知尚未授权"
        @unknown default: state?.notificationStatus = "系统通知状态未知"
        }
    }

    func send(kind: String, room: String, id: Int? = nil, member: String? = nil, title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        var info: [String: Any] = ["kind": kind, "room": room]
        if let id { info["id"] = id }
        if let member { info["member"] = member }
        content.userInfo = info
        center.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)) { _ in }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let kind = info["kind"] as? String ?? ""
        let room = info["room"] as? String ?? ""
        let id = info["id"] as? Int
        let member = info["member"] as? String
        Task { @MainActor in
            NSApp.activate(ignoringOtherApps: true)
            PackNotifications.shared.state?.openNotification(kind: kind, room: room, id: id, member: member)
        }
        completionHandler()
    }
}
