import Foundation
import Testing
@testable import AuraKit

/// The Observer (docs/observer.md): Aura's activity, history then live, with pause and filters.
@MainActor
struct ObserverTests {
    private func entry(_ event: String, _ fields: [(String, String)] = [], kind: String = "other") -> ActivityEntry {
        ActivityEntry(ts: "2026-11-27T06:00:00+00:00", event: event, category: kind,
                      fields: fields.map { ActivityEntry.Field(key: $0.0, value: $0.1) })
    }

    private func activity(_ seq: Int, _ e: ActivityEntry) -> MirrorFrame {
        MirrorFrame(kind: "activity", seq: seq, turnID: nil, origin: "aura", said: nil, place: nil, event: nil,
                    approvalID: nil, activity: e)
    }

    @Test func anAuditEntryIsDecodedWhateverItsShape() throws {
        let json = #"{"ts": "t", "event": "container_started", "category": "containers", "memory": "512m", "allowed_hosts": ["a.com", "b.com"], "n": 3, "ok": true, "x": null}"#
        let e = try JSONDecoder().decode(ActivityEntry.self, from: Data(json.utf8))
        #expect(e.event == "container_started" && e.field("memory") == "512m" && e.field("allowed_hosts") == "a.com, b.com")
        #expect(e.category == "containers" && e.field("category") == nil)
        #expect(e.field("n") == "3" && e.field("ok") == "yes" && e.field("x") == "none")
        var parser = MirrorParser()
        let frame = ["event: activity", #"data: {"entry": {"ts": "t", "event": "egress_used", "query": "weather"}, "seq": 9, "turn_id": null, "origin": "aura"}"#]
            .compactMap { parser.feed($0) }.first
        #expect(frame?.activity?.event == "egress_used" && frame?.seq == 9 && frame?.event == nil)
    }

    @Test func theKindComesFromTheServer() {
        #expect(["containers", "Egress", "alerts", "vault", "connections", "actions", "something new"]
            .map { ObserverEvent.category(named: $0) } == [.containers, .egress, .alerts, .vault, .connections, .actions, .other])
        let e = ActivityEntry(ts: "2026-11-27T06:00:00+00:00", event: "container_started", category: "containers",
                              fields: [.init(key: "kind", value: "sandbox"), .init(key: "network", value: "none"),
                                       .init(key: "memory", value: "512m")])
        let row = ObserverEvent.from(e, id: 1)
        #expect(row.category == .containers && row.title == "Container started · sandbox · none · 512m")
        #expect(row.clock.count == 8)  // worked out once, when the event arrives
    }

    @Test func historyThenLiveWithoutAGap() async {
        let fake = FakeTransport()
        fake.activityPage = ActivityPage(entries: [entry("skill_run"), entry("approval", [("decision", "approved")])], latest: 41)
        fake.mirrorScripts = [[activity(42, entry("egress_used", [("query", "weather")]))]]
        let o = ObserverStore()
        o.retry = 0.001
        o.transport = fake
        await o.start()
        for _ in 0..<200 where o.events.count < 3 { try? await Task.sleep(for: .milliseconds(5)) }
        #expect(o.events.map(\.title) == ["Skill run", "Approval · approved", "Egress used · weather"])
        #expect(fake.mirrorAfters.first == 41)  // followed from exactly where the history ended
        o.stop()
    }

    @Test func aResetWhilePausedKeepsTheLiveOnlyRowsAndDoesntRepeatTheRest() async {
        let fake = FakeTransport()
        fake.activityPage = ActivityPage(entries: [entry("skill_run"), entry("code_run")], latest: 5)
        let o = ObserverStore()
        o.transport = fake
        o.pause()
        o.apply(MirrorFrame(kind: "turn", seq: 1, turnID: "t-1", origin: "mac", said: "hi", place: nil, event: nil, approvalID: nil))
        o.apply(activity(2, entry("code_run")))  // also in the history the reload brings
        o.apply(MirrorFrame(kind: "reset", seq: 5, turnID: nil, origin: "aura", said: nil, place: nil, event: nil, approvalID: nil))
        for _ in 0..<200 where o.events.count < 2 { try? await Task.sleep(for: .milliseconds(5)) }
        #expect(o.held.map(\.title) == ["Turn started · on the Mac"])
        o.resume()
        #expect(o.events.map(\.title) == ["Skill run", "Code run", "Turn started · on the Mac"])
        o.stop()
    }

    @Test func pauseHoldsEventsAndResumeShowsThemInOrder() {
        let o = ObserverStore()
        o.apply(activity(1, entry("skill_run")))
        o.pause()
        o.apply(activity(2, entry("code_run")))
        o.apply(activity(3, entry("egress_used")))
        #expect(o.events.count == 1 && o.held.count == 2)
        o.resume()
        #expect(o.events.map(\.title) == ["Skill run", "Code run", "Egress used"] && o.held.isEmpty)
    }

    @Test func turnsAndApprovalsComeFromTheFeedAndReplyTextDoesnt() {
        let o = ObserverStore()
        o.apply(MirrorFrame(kind: "turn", seq: 1, turnID: "t-1", origin: "slack", said: "hi", place: nil, event: nil, approvalID: nil))
        o.apply(MirrorFrame(kind: "text", seq: 2, turnID: "t-1", origin: "slack", said: nil, place: nil, event: .text("Hello"), approvalID: nil))
        o.apply(MirrorFrame(kind: "approval", seq: 3, turnID: "t-1", origin: "slack", said: nil, place: nil,
                            event: .approval([PendingAction(tool: "web_search", description: "search: weather")]), approvalID: "a-1"))
        o.apply(MirrorFrame(kind: "done", seq: 4, turnID: "t-1", origin: "slack", said: nil, place: nil, event: .done, approvalID: nil))
        #expect(o.events.map(\.title) == ["Turn started · from Slack", "Approval asked · web_search", "Turn ended · from Slack"])
    }

    @Test func aFilterShowsOnlyTheChosenKinds() {
        let o = ObserverStore()
        o.apply(activity(1, entry("container_started", kind: "containers")))
        o.apply(activity(2, entry("egress_used", kind: "egress")))
        o.shown = [.egress]
        #expect(o.visible.map(\.category) == [.egress] && o.events.count == 2)
    }

    @Test func theVisibleListFollowsArrivalsAndTheBound() {
        let o = ObserverStore()
        o.shown = [.egress]
        for seq in 1...(ObserverStore.keep + 10) {
            o.apply(activity(seq, entry(seq.isMultiple(of: 2) ? "egress_used" : "container_started",
                                        kind: seq.isMultiple(of: 2) ? "egress" : "containers")))
        }
        #expect(o.events.count == ObserverStore.keep)
        #expect(o.visible == o.events.filter { $0.category == .egress })
        o.shown = Set(ObserverEvent.Category.allCases)
        #expect(o.visible == o.events)
    }
}
