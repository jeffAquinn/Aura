import Foundation
import Testing
@testable import AuraKit

/// Plays scripted event streams; records what the store asked for.
final class FakeTransport: AuraTransport, @unchecked Sendable {
    var chatScripts: [[AuraEvent]]
    var approveScripts: [[AuraEvent]]
    var failChat: Error?
    var hangChat = false
    private(set) var sent: [String] = []
    private(set) var approvals: [[Bool]] = []
    private(set) var newConversations = 0

    init(chat: [[AuraEvent]] = [], approve: [[AuraEvent]] = []) {
        chatScripts = chat
        approveScripts = approve
    }

    private func play(_ events: [AuraEvent]) -> AsyncThrowingStream<AuraEvent, Error> {
        let (stream, continuation) = AsyncThrowingStream<AuraEvent, Error>.makeStream()
        events.forEach { continuation.yield($0) }
        if let failChat { continuation.finish(throwing: failChat) }
        else if !hangChat { continuation.finish() }
        return stream
    }

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> {
        sent.append(text)
        return play(chatScripts.isEmpty ? [] : chatScripts.removeFirst())
    }

    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> {
        self.approvals.append(approvals)
        return play(approveScripts.isEmpty ? [] : approveScripts.removeFirst())
    }

    func newConversation() async throws { newConversations += 1 }
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
}

@MainActor
struct ChatStoreTests {
    private func store(_ transport: FakeTransport) -> ChatStore {
        let s = ChatStore()
        s.transport = transport
        return s
    }

    private func roles(_ s: ChatStore) -> [ChatStore.Item.Role] { s.items.map(\.role) }

    /// Wait (bounded) for the store's turn task to reach a state; tests run in parallel on the
    /// main actor, so a fixed number of yields is not enough.
    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<10_000 where !condition() { await Task.yield() }
        return condition()
    }

    @Test func streamsTextIntoOneBubbleAndReturnsToIdle() async {
        let s = store(FakeTransport(chat: [[.thinking, .text("Hel"), .text("lo."), .done]]))
        s.send("  Hi  ")
        #expect(s.phase == .thinking)
        await s.turnTask?.value
        #expect(roles(s) == [.user, .aura])
        #expect(s.items.map(\.text) == ["Hi", "Hello."])
        #expect(s.phase == .idle)
    }

    @Test func toolStatusSplitsTheReplyIntoChipAndBubble() async {
        let s = store(FakeTransport(chat: [[.thinking, .status("[Tool: Web_Search_Tool] Searching…"), .text("12°C"), .done]]))
        s.send("Weather?")
        await s.turnTask?.value
        #expect(roles(s) == [.user, .status, .aura])
    }

    @Test func approvalRoundTripDefaultDenyPath() async {
        let fake = FakeTransport(
            chat: [[.thinking, .approval([PendingAction(tool: "delete_path", description: "delete file 'a.txt'")]), .done]],
            approve: [[.thinking, .text("[Action Denied by User]. I will not perform that action. How else can I help?"), .done]]
        )
        let s = store(fake)
        s.send("Delete a.txt")
        await s.turnTask?.value
        #expect(s.phase == .awaitingApproval)
        #expect(s.items.last?.decisions == nil)

        s.send("ignored while a card is open")  // the card must be answered first
        #expect(fake.sent == ["Delete a.txt"])

        s.decide([true, false])  // wrong count: ignored
        #expect(fake.approvals.isEmpty)

        s.decide([false])
        await s.turnTask?.value
        #expect(fake.approvals == [[false]])
        #expect(s.items[1].decisions == [false])
        #expect(s.items.last?.text.hasPrefix("[Action Denied by User]") == true)
        #expect(s.phase == .idle)
    }

    @Test func wakingStateLastsUntilTheFirstWord() async {
        let s = store(FakeTransport(chat: [[.thinking, .waking]]))
        let fake = s.transport as! FakeTransport
        fake.hangChat = true
        s.send("Hi")
        #expect(await eventually { s.phase == .waking })
        s.stop()
        await s.turnTask?.value
        #expect(s.phase == .idle)
    }

    @Test func transportFailureBecomesAnErrorItem() async {
        let fake = FakeTransport(chat: [[.thinking]])
        fake.failChat = AuraClientError.http(status: 401, message: "Missing or invalid token.")
        let s = store(fake)
        s.send("Hi")
        await s.turnTask?.value
        #expect(roles(s) == [.user, .error])
        #expect(s.items.last?.text == FriendlyMessage.turnFailed(fake.failChat!))  // plain language
        #expect(s.items.last?.text.contains("401") == false)
        #expect(s.phase == .idle)
    }

    @Test func stopEndsTheTurnWithoutAnError() async {
        let fake = FakeTransport(chat: [[.thinking, .text("partial")]])
        fake.hangChat = true
        let s = store(fake)
        s.send("Tell me a long story")
        #expect(await eventually { s.phase == .streaming })
        s.stop()
        await s.turnTask?.value
        #expect(roles(s) == [.user, .aura])
        #expect(s.phase == .idle)
    }

    @Test func nothingIsSentWithoutAConnectionOrText() {
        let s = ChatStore()
        s.send("Hi")  // no transport yet
        #expect(s.items.isEmpty)
        s.transport = FakeTransport()
        s.send("   ")
        #expect(s.items.isEmpty)
        #expect(s.connection == .connected)
    }

    @Test func newConversationClearsTheTranscript() async {
        let fake = FakeTransport(chat: [[.text("hi"), .done]])
        let s = store(fake)
        s.send("Hello")
        await s.turnTask?.value
        await s.newConversation()
        #expect(s.items.isEmpty && fake.newConversations == 1)
    }
}
