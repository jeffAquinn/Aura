import Foundation
import Testing
@testable import AuraKit

/// A transport whose turns can be stopped through the server and that kept a conversation.
final class StoppableTransport: AuraTransport, @unchecked Sendable {
    var kept: ConversationInfo?
    private(set) var stops = 0
    private var continuation: AsyncThrowingStream<AuraEvent, Error>.Continuation?

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> {
        let (stream, continuation) = AsyncThrowingStream<AuraEvent, Error>.makeStream()
        continuation.yield(.thinking)
        continuation.yield(.status("[Tool: Developer] Handing the task to Claude Code..."))
        self.continuation = continuation  // hangs until stopped
        return stream
    }

    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }

    /// The server: stops the turn, cleans it up, then ends the stream with `stopped` and `done`.
    func stopTurn() async throws -> Bool {
        stops += 1
        continuation?.yield(.stopped(partial: true))
        continuation?.yield(.done)
        continuation?.finish()
        return true
    }

    func conversation() async throws -> ConversationInfo {
        guard let kept else { throw AuraClientError.badResponse }
        return kept
    }

    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
}

@MainActor
struct RecoveryTests {
    @Test func stopAsksTheServerAndShowsWhatAlreadyRan() async {
        let fake = StoppableTransport()
        let s = ChatStore()
        s.transport = fake
        s.send("Tidy my vault")
        #expect(await eventually { s.phase == .streaming })
        s.stop()
        #expect(s.isStopping)
        s.stop()  // one tap is one server request, even before the first response returns
        await waitForTurn(s)
        #expect(fake.stops == 1)
        #expect(s.items.last?.text == "Stopped. Whatever Aura had already done stays done.")
        #expect(s.phase == .idle)
        #expect(!s.items.contains { $0.role == .error })
    }

    @Test func aRestartedServersConversationAndWaitingApprovalComeBack() async {
        let fake = StoppableTransport()
        let action = PendingAction(tool: "create_folder", description: "create folder 'Plans/'")
        fake.kept = ConversationInfo(
            messages: [.init(role: "user", text: "Make a Plans folder."), .init(role: "aura", text: "Sure.")],
            pending: PendingApproval(approvalId: "a-1", actions: [action]),
            recoveryNote: nil)
        let s = ChatStore()
        s.transport = fake
        await s.restoreConversation()
        #expect(s.items.map(\.role) == [.user, .aura, .approval])
        #expect(s.items.last?.actions == [action])
        #expect(s.phase == .awaitingApproval)
        await s.restoreConversation()  // only ever into an empty chat
        #expect(s.items.count == 3)
    }

    @Test func whatARestartCutShortIsSaid() async {
        let fake = StoppableTransport()
        fake.kept = ConversationInfo(messages: [], pending: nil, recoveryNote: "Aura restarted while it was working.")
        let s = ChatStore()
        s.transport = fake
        await s.restoreConversation()
        #expect(s.items.map(\.text) == ["Aura restarted while it was working."])
        #expect(s.phase == .idle)
    }

    @Test func theParserKeepsTheTurnAndApprovalIDsAndReadsStopped() {
        var parser = SSEParser()
        let lines = [
            "event: thinking", #"data: {"turn_id": "t-123"}"#,
            "event: approval", #"data: {"actions": [], "approval_id": "a-9"}"#,
            "event: stopped", #"data: {"turn_id": "t-123", "tools": 1, "partial": true}"#,
        ]
        let events = lines.compactMap { parser.feed($0) }
        #expect(events == [.thinking, .approval([]), .stopped(partial: true)])
        #expect(parser.turnID == "t-123")
        #expect(parser.approvalID == "a-9")
    }

    @Test func recoveryItemsDecodeAndOlderServersStillDecode() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = #"""
        {"engines": [], "builds": [], "budget_usd": 2, "repair_rounds": 2, "timeout_minutes": 10,
         "recovery": [{"kind": "build", "id": "b-1", "title": "Build of 'x' (Claude Code)", "message": "Aura restarted.",
                       "when": "", "scratch_kept": true, "actions": ["retry", "dismiss"]}]}
        """#
        let info = try decoder.decode(DeveloperInfo.self, from: Data(json.utf8))
        #expect(info.recovery?.first?.actions == ["retry", "dismiss"])
        #expect(info.recovery?.first?.scratchKept == true)
        let old = #"{"engines": [], "builds": [], "budget_usd": 2, "repair_rounds": 2, "timeout_minutes": 10}"#
        #expect(try decoder.decode(DeveloperInfo.self, from: Data(old.utf8)).recovery == nil)
    }

    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<10_000 where !condition() { await Task.yield() }
        return condition()
    }

    private func waitForTurn(_ s: ChatStore) async {
        for _ in 0..<200 where s.isBusy { try? await Task.sleep(for: .milliseconds(10)) }
        await s.turnTask?.value
    }
}
