import Foundation
import Testing
@testable import AuraKit

/// The Unified Command Stream in the app (docs/unified-command-stream.md): turns that didn't start
/// here (the owner's Slack commands) are drawn from the mirror feed, live.
@MainActor
struct MirrorTests {
    private func frame(_ kind: String, _ seq: Int, turn: String? = "t-s", origin: String? = "slack", said: String? = nil,
                       event: AuraEvent? = nil, approval: String? = nil) -> MirrorFrame {
        MirrorFrame(kind: kind, seq: seq, turnID: turn, origin: origin, said: said, place: nil, event: event, approvalID: approval)
    }

    private func store(_ fake: FakeTransport = FakeTransport()) -> ChatStore {
        let s = ChatStore()
        s.transport = fake
        return s
    }

    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<10_000 where !condition() { await Task.yield() }
        return condition()
    }

    @Test func theParserReadsTurnTextAndCardFrames() {
        var p = MirrorParser()
        let lines = [
            "event: turn", ##"data: {"kind": "slack", "text": "hi", "place": "#aura-chat", "seq": 4, "turn_id": "t-1", "origin": "slack"}"##, "",
            ": waiting", "",
            "event: text", #"data: {"text": "Hello", "seq": 5, "turn_id": "t-1", "origin": "slack"}"#, "",
            "event: approval", #"data: {"actions": [{"tool": "create_folder", "description": "Create Plans"}], "approval_id": "a-9", "seq": 6, "turn_id": "t-1", "origin": "slack"}"#,
        ]
        let frames = lines.compactMap { p.feed($0) }
        #expect(frames.map(\.kind) == ["turn", "text", "approval"])
        #expect(frames[0].said == "hi" && frames[0].place == "#aura-chat" && frames[0].event == nil && frames[0].seq == 4)
        #expect(frames[1].event == .text("Hello") && frames[1].said == nil)
        #expect(frames[2].approvalID == "a-9" && frames[2].origin == "slack")
    }

    @Test func aSlackTurnIsDrawnLiveWithABadgeAndTheBoxWaits() {
        let s = store()
        s.applyMirror(frame("turn", 1, said: "What's on today?"))
        #expect(s.items.last?.role == .user && s.items.last?.origin == "slack" && s.items.last?.text == "What's on today?")
        #expect(s.isAnsweringElsewhere && !s.canSend)
        s.applyMirror(frame("text", 2, event: .text("Two meetings.")))
        s.applyMirror(frame("done", 3))
        #expect(s.items.map(\.role) == [.user, .aura] && s.items[1].text == "Two meetings.")
        #expect(s.phase == .idle && !s.isAnsweringElsewhere && s.canSend)
    }

    @Test func theMacsOwnTurnsAndUnknownTurnsAreNotDrawnTwice() {
        let s = store()
        s.applyMirror(frame("turn", 1, origin: "mac", said: "typed here"))
        s.applyMirror(frame("text", 2, turn: "t-other", event: .text("from a turn begun before we joined")))
        #expect(s.items.isEmpty && s.phase == .idle && s.mirrorSeq == 2)
    }

    @Test func aSlackCardCanBeAnsweredOnTheMac() async {
        let fake = FakeTransport(approve: [[.text("Created it."), .done]])
        let s = store(fake)
        s.applyMirror(frame("turn", 1, said: "Make a Plans folder."))
        s.applyMirror(frame("approval", 2, event: .approval([PendingAction(tool: "create_folder", description: "Create Plans")]),
                            approval: "a-1"))
        s.applyMirror(frame("done", 3))
        #expect(s.phase == .awaitingApproval && !s.isAnsweringElsewhere)
        s.decide([true])
        await s.turnTask?.value
        #expect(fake.approvals == [[true]] && s.items.last?.text == "Created it." && s.phase == .idle)
    }

    @Test func aCardSlackAnswersItselfSaysSo() {
        let s = store()
        s.applyMirror(frame("turn", 1, said: "Make a Plans folder."))
        s.applyMirror(frame("approval", 2, event: .approval([PendingAction(tool: "create_folder", description: "Create Plans")])))
        s.applyMirror(frame("done", 3))
        s.applyMirror(frame("turn", 4, turn: "t-s2", said: "allow"))
        s.applyMirror(frame("answered", 5, turn: "t-s2"))
        let card = s.items.first { $0.role == .approval }
        #expect(card?.decisions == [] && card?.answeredElsewhere == "slack")
        #expect(s.items.last?.text == "allow" && s.items.last?.origin == "slack")
    }

    @Test func stopStopsTheSlackTurnByItsID() async {
        let fake = FakeTransport()
        let s = store(fake)
        s.applyMirror(frame("turn", 1, said: "Write me an essay."))
        s.stop()
        #expect(s.isStopping)
        #expect(await eventually { fake.stoppedTurns == ["t-s"] })
        s.applyMirror(frame("stopped", 2, event: .stopped(partial: false)))
        s.applyMirror(frame("done", 3))
        #expect(s.phase == .idle && s.items.last?.text == "Stopped.")
    }

    @Test func aDroppedFeedReconnectsFromTheLastFrameWithoutAWord() async {
        let fake = FakeTransport()
        fake.mirrorScripts = [[frame("ready", 7, turn: nil, origin: nil), frame("turn", 8, said: "hi")],
                              [frame("text", 9, event: .text("Hello."))]]
        let s = store(fake)
        s.mirrorRetry = 0.001
        s.followMirror()
        for _ in 0..<200 where fake.mirrorAfters.count < 3 { try? await Task.sleep(for: .milliseconds(10)) }
        #expect(fake.mirrorAfters.count >= 3)  // two drops, then a quiet connection
        #expect(Array(fake.mirrorAfters.prefix(3)) == [-1, 8, 9])
        #expect(s.items.map(\.text) == ["hi", "Hello."] && s.connection == .connected)
        s.stopMirror()
    }
}

extension MirrorTests {
    @Test func aSlackAnswerIsHandedOnForANotificationOnce() {
        let s = store()
        var answers: [IncomingMessage] = []
        s.onRemoteAnswer = { answers.append($0) }
        s.applyMirror(MirrorFrame(kind: "turn", seq: 1, turnID: "t-s", origin: "slack", said: "hi", place: "#aura-chat",
                                  event: nil, approvalID: nil))
        s.applyMirror(frame("text", 2, event: .text("Hello there.")))
        s.applyMirror(frame("done", 3))
        s.applyMirror(frame("done", 4))  // a stray repeat: the turn is over
        #expect(answers.map(\.text) == ["Hello there."] && answers[0].kind == "reply" && answers[0].heading == "Slack · #aura-chat")
    }

    @Test func aMacTurnIsNeverAnnounced() {
        let s = store()
        var answers: [IncomingMessage] = []
        s.onRemoteAnswer = { answers.append($0) }
        s.applyMirror(frame("turn", 1, origin: "mac", said: "typed here"))
        s.applyMirror(frame("done", 2, origin: "mac"))
        #expect(answers.isEmpty)
    }
}

extension MirrorTests {
    @Test func aSlackMessageThatAnswersNothingLeavesTheMacsCardWaiting() {
        let s = ChatStore(items: [ChatStore.Item(role: .approval, text: "",
                                                 actions: [PendingAction(tool: "create_folder", description: "Create Plans")])],
                          phase: .awaitingApproval)  // a card raised at the Mac
        s.transport = FakeTransport()
        s.applyMirror(frame("turn", 1, said: "hi"))
        s.applyMirror(frame("text", 2, event: .text("A request from the Mac is waiting there. Answer it on the Mac.")))
        s.applyMirror(frame("done", 3))
        let card = s.items.first { $0.role == .approval }
        #expect(card?.decisions == nil && card?.answeredElsewhere == nil)  // still answerable here
        #expect(s.phase == .awaitingApproval)
    }

    @Test func aSlackTurnThatStartsAsTheMacsTurnEndsIsStillDrawn() async {
        let fake = FakeTransport(chat: [[.thinking, .text("Done here."), .done]])
        fake.hangChat = true  // the Mac's stream hasn't delivered `done` yet
        let s = store(fake)
        s.send("Tidy my notes.")
        #expect(s.isBusy)
        s.applyMirror(frame("turn", 1, said: "and from my phone"))  // the server already let the Slack turn start
        s.applyMirror(frame("text", 2, event: .text("Phone reply.")))
        s.applyMirror(frame("done", 3))
        #expect(!s.items.contains { $0.origin == "slack" })  // held, not dropped
        s.turnTask?.cancel()  // the Mac's stream ends
        await s.turnTask?.value
        #expect(s.items.contains { $0.origin == "slack" && $0.text == "and from my phone" })
        #expect(s.items.last?.text == "Phone reply." && s.phase == .idle)
    }
}
