import AppKit
import AuraKit
import UserNotifications

/// Mac notifications when Aura answers the user in Slack (or another messaging app). macOS asks
/// the user once, the first time; clicking a notification opens Aura's panel.
@MainActor
final class Notifier: NSObject, UNUserNotificationCenterDelegate {
    var onOpen: () -> Void = {}
    private var asked = false

    /// Only inside Aura.app: the notification center needs a bundled app (not `swift run`).
    private var center: UNUserNotificationCenter? {
        Bundle.main.bundleURL.pathExtension == "app" ? UNUserNotificationCenter.current() : nil
    }

    /// Aura's answer to the owner's Slack command (from the mirror feed).
    func post(_ message: IncomingMessage) {
        guard let center else { return }
        center.delegate = self
        let first = !asked
        asked = true
        Task {
            if first {
                let granted = (try? await center.requestAuthorization(options: [.alert, .sound])) ?? false
                AppLog.write("notifications: \(granted ? "allowed" : "not allowed")")
            }
            let content = UNMutableNotificationContent()
            content.title = "Aura answered you in \(message.source)"  // only Aura's answers notify
            content.subtitle = message.heading
            content.body = message.text.replacingOccurrences(of: "**", with: "")  // Markdown marks read as noise
            content.sound = .default
            content.threadIdentifier = message.heading  // one stack per channel
            try? await center.add(UNNotificationRequest(identifier: "aura-message-\(UUID().uuidString)",
                                                        content: content, trigger: nil))
        }
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions {
        [.banner, .sound, .list]  // show it even while Aura is the active app
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        await MainActor.run { onOpen() }
    }
}
