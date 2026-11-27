import AppKit
@testable import AuraKit
import SwiftUI
import Testing
@testable import AuraUI

/// The Observer window, drawn the way the app draws it (an NSHostingView in a window; nothing on
/// screen is captured), with a representative stream of Aura's activity.
@MainActor
struct ObserverViewTests {
    private func entry(_ event: String, _ fields: [(String, String)], at time: String) -> MirrorFrame {
        MirrorFrame(kind: "activity", seq: 0, turnID: nil, origin: "aura", said: nil, place: nil, event: nil, approvalID: nil,
                    activity: ActivityEntry(ts: "2026-11-27T06:\(time)+00:00", event: event, category: category(event),
                                            fields: fields.map { ActivityEntry.Field(key: $0.0, value: $0.1) }))
    }

    /// As the server names them (aura.audit.category), for these sample events.
    private func category(_ event: String) -> String {
        if event.hasPrefix("egress_") { return "egress" }
        if event.hasPrefix("container_") { return "containers" }
        if event.hasPrefix("vault_") { return "vault" }
        return event == "skill_flagged" ? "alerts" : "actions"
    }

    @Test func theWindowShowsAColourCodedLog() {
        let store = ObserverStore()
        store.apply(MirrorFrame(kind: "turn", seq: 1, turnID: "t-1", origin: "slack", said: "weather?", place: nil, event: nil,
                                approvalID: nil))
        store.apply(MirrorFrame(kind: "approval", seq: 2, turnID: "t-1", origin: "slack", said: nil, place: nil,
                                event: .approval([PendingAction(tool: "web_search", description: "search: weather London")]),
                                approvalID: "a-1"))
        store.apply(entry("approval", [("tool", "web_search"), ("decision", "approved")], at: "12:03"))
        store.apply(entry("egress_granted", [("tool", "web_search"), ("query", "weather London")], at: "12:03"))
        store.apply(entry("egress_used", [("tool", "web_search"), ("query", "weather London")], at: "12:04"))
        store.apply(entry("container_started", [("kind", "sandbox"), ("network", "none"), ("memory", "512m"), ("cpus", "1")], at: "12:05"))
        store.apply(entry("container_finished", [("kind", "sandbox"), ("exit_code", "0"), ("seconds", "1.42")], at: "12:07"))
        store.apply(entry("vault_change_applied", [("path", "Reports/q3.md")], at: "12:09"))
        store.apply(entry("skill_flagged", [("skill", "csv_chart"), ("reason", "new advisory")], at: "12:10"))
        store.apply(MirrorFrame(kind: "done", seq: 3, turnID: "t-1", origin: "slack", said: nil, place: nil, event: .done,
                                approvalID: nil))
        #expect(store.visible.count == 10)
        #expect(store.visible.first { $0.category == .vault }?.title == "Vault change applied · Reports/q3.md")
        let utc = ISO8601DateFormatter().date(from: "2026-11-27T23:12:09+00:00")!
        let expected = { let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f.string(from: utc) }()
        #expect(ObserverEvent.clock("2026-11-27T23:12:09+00:00") == expected && expected.count == 8)  // 24-hour

        let host = NSHostingView(rootView: ObserverView(store: store).frame(width: 760, height: 420))
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 760, height: 420), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.contentView = host
        window.appearance = NSAppearance(named: .aqua)  // drawn light, whatever the Mac's setting
        host.layoutSubtreeIfNeeded()
        guard let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { Issue.record("no bitmap"); return }
        host.cacheDisplay(in: host.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: SnapshotTests.outDir.appending(path: "observer.png"))
    }
}
