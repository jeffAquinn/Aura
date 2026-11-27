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
    private(set) var developers: [String?] = []  // the chat box's choice, per message
    private(set) var selectedModels: [ModelChoice?] = []  // explicit one-turn Aura model overrides
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
        developers.append(nil)
        return play(chatScripts.isEmpty ? [] : chatScripts.removeFirst())
    }

    func chat(_ text: String, developer: String?) -> AsyncThrowingStream<AuraEvent, Error> {
        sent.append(text)
        developers.append(developer)
        selectedModels.append(nil)
        return play(chatScripts.isEmpty ? [] : chatScripts.removeFirst())
    }

    func chat(_ text: String, developer: String?, model: ModelChoice?) -> AsyncThrowingStream<AuraEvent, Error> {
        sent.append(text)
        developers.append(developer)
        selectedModels.append(model)
        return play(chatScripts.isEmpty ? [] : chatScripts.removeFirst())
    }

    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> {
        self.approvals.append(approvals)
        return play(approveScripts.isEmpty ? [] : approveScripts.removeFirst())
    }

    func newConversation() async throws { newConversations += 1 }
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }

    // The mirror feed (MirrorTests): each connection plays one script, then drops; when the
    // scripts run out, the connection stays open and quiet.
    var mirrorScripts: [[MirrorFrame]] = []
    private(set) var mirrorAfters: [Int] = []
    private(set) var stoppedTurns: [String] = []

    func mirror(after: Int) -> AsyncThrowingStream<MirrorFrame, Error> {
        mirrorAfters.append(after)
        let (stream, continuation) = AsyncThrowingStream<MirrorFrame, Error>.makeStream()
        guard !mirrorScripts.isEmpty else { return stream }
        mirrorScripts.removeFirst().forEach { continuation.yield($0) }
        continuation.finish(throwing: URLError(.networkConnectionLost))
        return stream
    }

    func stopTurn(id: String) async throws -> Bool {
        stoppedTurns.append(id)
        return true
    }
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

    @Test func aMessageCanGoToClaudeCodeAndTheChoiceStays() async {
        let fake = FakeTransport(chat: [[.thinking, .done], [.thinking, .done]])
        let s = store(fake)
        s.developer = "claude"
        s.send("Tidy my notes.")
        await s.turnTask?.value
        s.send("And the Map.")
        await s.turnTask?.value
        #expect(fake.developers == ["claude", "claude"] && s.developer == "claude")
        #expect(s.items[0].developer == "claude")
        #expect(s.conversationText().hasPrefix("You (to Claude Code): Tidy my notes."))
        s.developer = nil
        s.send("Thanks.")
        await s.turnTask?.value
        #expect(fake.developers.last == .some(nil) && s.items.last { $0.role == .user }?.developer == nil)
    }

    @Test func aChatModelOverrideAppliesToAuraButNotDeveloperTurns() async {
        let fake = FakeTransport(chat: [[.thinking, .done], [.thinking, .done]])
        let s = store(fake)
        s.chatModel = ModelChoice(provider: "ollama", model: "qwen3:8b")
        s.send("Use the local model")
        await s.turnTask?.value
        #expect(fake.selectedModels == [ModelChoice(provider: "ollama", model: "qwen3:8b")])
        s.developer = "codex"
        s.send("Use the Developer")
        await s.turnTask?.value
        #expect(fake.selectedModels.count == 2 && fake.selectedModels[1] == nil)
    }

    @Test func aSentTurnKeepsItsModelWhenThePickerChangesDuringStreaming() async {
        let fake = FakeTransport(chat: [[.thinking, .done]])
        let s = store(fake)
        let sent = ModelChoice(provider: "ollama", model: "qwen3:8b")
        s.chatModel = sent

        s.send("Explain recursion.")
        s.chatModel = ModelChoice(provider: "ollama", model: "gemma4:12b")
        await s.turnTask?.value

        #expect(fake.selectedModels == [sent])
    }

    @Test func automaticTurnsExposeTheModelSelectedByTheServer() async {
        let chosen = ModelChoice(provider: "ollama", model: "qwen3:8b")
        let s = store(FakeTransport(chat: [[.model(chosen), .thinking, .done]]))
        s.send("Hello")
        await s.turnTask?.value
        #expect(s.automaticModel == chosen)
    }

    @Test func anExplicitModelChoiceIsNotReplacedByAnAutomaticModelEvent() async {
        let explicit = ModelChoice(provider: "ollama", model: "qwen3.5:9b")
        let reported = ModelChoice(provider: "ollama", model: "gemma4:12b")
        let s = store(FakeTransport(chat: [[.model(reported), .thinking, .done]]))
        s.chatModel = explicit

        s.send("Hello")
        await s.turnTask?.value

        #expect(s.chatModel == explicit)
        #expect(s.automaticModel == nil)
    }

    @Test func aCompletedLocalInferencePublishesItsMeasuredPerformance() async {
        let measured = LocalModelPerformance(totalDuration: 57.81, promptDuration: 0.13, promptTokens: 34,
                                              promptTokensPerSecond: 254.4, generationDuration: 57.67,
                                              outputTokens: 1657, outputTokensPerSecond: 28.73)
        let s = store(FakeTransport(chat: [[.thinking, .performance(measured), .done]]))

        s.send("Hello")
        await s.turnTask?.value

        #expect(s.localPerformance == measured)
    }

    @Test func aDevelopersProgressIsOneLineThatKeepsUpdating() async {
        let s = store(FakeTransport(chat: [[.thinking,
            .status("[Tool: Developer] Handing the task to Claude Code..."),
            .status("[Tool: Developer] · Claude Code: Reading Map_of_the_Ascent.md"),
            .status("[Tool: Developer] · Claude Code: Writing The_Absolute_R/forms.md"),
            .text("Done."), .done]]))
        s.send("Go.")
        await s.turnTask?.value
        #expect(roles(s) == [.user, .status, .status, .aura])
        #expect(s.items[2].text == "[Tool: Developer] · Claude Code: Writing The_Absolute_R/forms.md")
    }

    @Test func filesADeveloperChangedArriveAsOneCardWithAnEntryPerFile() async {
        let actions = [
            PendingAction(tool: "apply_change", description: "change 'Map_of_the_Ascent.md' (+1 −0):\n    │ @@ -1,2 +1,3 @@\n    │  # Map\n    │ +- Forms",
                          args: ["path": .string("Map_of_the_Ascent.md"), "change": .string("modify"), "engine": .string("claude")]),
            PendingAction(tool: "apply_change", description: "delete 'forms.md' (31 B, −2 lines)",
                          args: ["path": .string("forms.md"), "change": .string("delete"), "engine": .string("claude")]),
        ]
        let fake = FakeTransport(chat: [[.thinking, .approval(actions), .done]], approve: [[.thinking, .done]])
        let s = store(fake)
        s.send("Go.")
        await s.turnTask?.value
        let card = s.items.last!
        #expect(card.isFileChanges && s.phase == .awaitingApproval)
        let changes = card.actions.compactMap(FileChange.init)
        #expect(changes.map(\.path) == ["Map_of_the_Ascent.md", "forms.md"] && changes.map(\.kind) == [.modify, .delete])
        #expect(changes[0].diff == ["@@ -1,2 +1,3 @@", " # Map", "+- Forms"] && changes[0].detail == "+1 −0")
        #expect(changes[1].diff.isEmpty && changes[1].detail == "31 B, −2 lines" && changes[1].verb == "Deleted")
        s.decide([true, false])  // each file on its own
        await s.turnTask?.value
        #expect(fake.approvals == [[true, false]])
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

    @Test func messagesAndTheConversationCopyAsPlainText() {
        let s = ChatStore(items: [
            ChatStore.Item(role: .user, text: "What's in budget.csv?"),
            ChatStore.Item(role: .status, text: "[Tool: File_System_Tool] Reading 'budget.csv'..."),
            ChatStore.Item(role: .aura, text: "Rent is **1200**."),
            ChatStore.Item(role: .approval, text: "", actions: [PendingAction(tool: "delete_path", description: "delete file 'a.txt' (6 B)")]),
        ])
        #expect(ChatStore.copyText(s.items[2]) == "Rent is 1200.")
        #expect(ChatStore.copyText(s.items[0]) == "What's in budget.csv?")
        #expect(s.conversationText() == """
        You: What's in budget.csv?

          [Reading 'budget.csv'...]

        Aura: Rent is 1200.

          [Approval: delete file 'a.txt' (6 B)]
        """)
    }

    @Test func holOutputIsReadOnlyTranscriptAndIsBounded() {
        let s = ChatStore()
        s.mirrorHOLTranscript(["Session started", "Running approved task"])
        s.mirrorHOLTranscript(["Finished safely"])
        #expect(roles(s) == [.hol])
        #expect(s.items[0].text == "Session started\nRunning approved task\nFinished safely")
        #expect(s.conversationText().contains("[HOL session]\nSession started"))

        s.mirrorHOLTranscript([String(repeating: "x", count: 25_000)])
        #expect(s.items[0].text.hasPrefix("… earlier HOL output omitted …\n"))
        #expect(s.items[0].text.count <= 24_034)
    }

    // MARK: - live messages

    @Test func liveMessagesBecomeCardsButNeverSplitAReply() async {
        let transport = FakeTransport(chat: [[.text("Hello"), .text(" there")]])
        transport.hangChat = true  // the reply is still streaming
        let s = store(transport)
        let ping = IncomingMessage(id: 1, source: "Slack", place: "#aura-chat", author: "Jeff", text: "hi Aura", kind: "mention")
        s.receive([ping])
        #expect(roles(s) == [.incoming] && s.items[0].incoming == ping)
        #expect(s.items[0].text == "Jeff in Slack · #aura-chat: hi Aura" && ChatStore.copyText(s.items[0]) == s.items[0].text)
        s.send("Anything else?")
        try? await Task.sleep(for: .milliseconds(120))  // the second token is intentionally batched
        #expect(await eventually { s.items.last?.text == "Hello there" })
        let dm = IncomingMessage(id: 2, source: "Discord", place: "a direct message", author: "Sam", text: "lunch?", kind: "dm")
        s.receive([dm])  // mid-reply: held back
        #expect(roles(s) == [.incoming, .user, .aura])
        s.stop()
        #expect(await eventually { s.phase == .idle && s.items.count == 4 })
        #expect(roles(s) == [.incoming, .user, .aura, .incoming] && s.items[3].incoming?.heading == "Discord · direct message")
        #expect(s.conversationText().contains("  [Sam in Discord · direct message: lunch?]"))
    }

    @Test func theFeedFollowsTheServerFromItsLatestMessage() async {
        let transport = FeedTransport(pages: [
            MessagesPage(messages: [IncomingMessage(id: 1, source: "Slack", place: "#a", author: "J", text: "one")], latest: 1),
            MessagesPage(messages: [], latest: 1),
            MessagesPage(messages: [IncomingMessage(id: 2, source: "Discord", place: "#b", author: "K", text: "two")], latest: 2),
        ])
        let feed = MessageFeed(wait: 0, pause: .zero)
        var got: [Int] = []
        feed.onMessages = { got += $0.map(\.id) }
        feed.start(transport)
        #expect(await eventually { got == [1, 2] })
        feed.stop()
        #expect(transport.asked.prefix(3) == [0, 1, 1] && feed.after == 2 && !feed.isRunning)
    }
}

/// Hands out one page of live messages per call, recording the `after` it was asked for.
final class FeedTransport: AuraTransport, @unchecked Sendable {
    var pages: [MessagesPage]
    private(set) var asked: [Int] = []

    init(pages: [MessagesPage]) { self.pages = pages }

    func messages(after: Int, wait: Int) async throws -> MessagesPage {
        asked.append(after)
        if pages.isEmpty { try await Task.sleep(for: .seconds(30)); throw CancellationError() }
        return pages.removeFirst()
    }

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }
}
