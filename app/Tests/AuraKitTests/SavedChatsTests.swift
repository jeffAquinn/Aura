import Foundation
import Testing
@testable import AuraKit

/// Serves saved chats the way the Aura server does; records which chat was opened.
private final class ChatsTransport: AuraTransport, @unchecked Sendable {
    var saved = SavedChats(current: "now", chats: [
        SavedChat(id: "now", title: "Remember milk.", updated: "2026-11-27T20:00:00+00:00", file: "Chats/a.md"),
        SavedChat(id: "old", title: "Weather in London?", updated: "2026-11-26T09:00:00+00:00", file: "Chats/b.md"),
    ])
    private(set) var opened: [String] = []
    private(set) var newChats = 0

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws { newChats += 1 }
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
    func chats() async throws -> SavedChats { saved }
    func openChat(_ id: String) async throws -> ConversationInfo {
        opened.append(id)
        saved = SavedChats(current: id, chats: saved.chats)
        return ConversationInfo(messages: [.init(role: "user", text: "Weather in London?"),
                                           .init(role: "aura", text: "Sunny.")], pending: nil, recoveryNote: nil)
    }
}

@MainActor
struct SavedChatsTests {
    @Test func openingAChatShowsItsMessagesAndMarksItCurrent() async {
        let transport = ChatsTransport()
        let store = ChatStore(items: [ChatStore.Item(role: .user, text: "Remember milk.")])
        store.transport = transport
        await store.loadChats()
        #expect(store.savedChats?.chats.map(\.title) == ["Remember milk.", "Weather in London?"])

        await store.openChat("old")
        #expect(store.items.map(\.text) == ["Weather in London?", "Sunny."])  // none of the other chat's bubbles
        #expect(store.savedChats?.current == "old" && store.phase == .idle)

        await store.openChat("old")  // the open one: nothing happens
        #expect(transport.opened == ["old"])
    }

    @Test func newChatClearsTheTranscriptAndRefreshesTheList() async {
        let transport = ChatsTransport()
        let store = ChatStore(items: [ChatStore.Item(role: .user, text: "Remember milk.")])
        store.transport = transport
        await store.newConversation()
        #expect(store.items.isEmpty && transport.newChats == 1 && store.savedChats != nil)
    }

    @Test func aSavedChatSaysWhenItWasLastUsed() {
        let chat = SavedChat(id: "x", title: "t", updated: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-7200)), file: "")
        #expect(chat.when.contains("2 hours"))
    }
}

/// Saves a model's settings, or refuses them the way the server does (a value out of range).
private final class SettingsSaveTransport: AuraTransport, @unchecked Sendable {
    private(set) var sent: [[String: JSONValue]] = []
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
    func updateModelSettings(_ choice: ModelChoice, _ values: [String: JSONValue]) async throws -> ModelSettingsInfo {
        sent.append(values)
        if case let .number(t)? = values["temperature"], t > 2 {
            throw AuraClientError.http(status: 422, message: "temperature must be between 0 and 2.")
        }
        return ModelSettingsInfo(provider: choice.provider, model: choice.model, saved: values, effective: values,
                                 recommended: [:], accepted: ["temperature"], efforts: [], ranges: [:])
    }
}

@MainActor
struct ModelSettingsStoreTests {
    @Test func savingSettingsReturnsThemOrTheServersReason() async {
        let transport = SettingsSaveTransport()
        let store = ChatStore(items: [])
        store.transport = transport
        let qwen = ModelChoice(provider: "ollama", model: "qwen3.5:9b")
        guard case let .success(info) = await store.saveModelSettings(qwen, ["temperature": .number(0.6)]) else {
            Issue.record("the save failed"); return
        }
        #expect(info.saved == ["temperature": .number(0.6)])
        guard case let .failure(error) = await store.saveModelSettings(qwen, ["temperature": .number(3)]) else {
            Issue.record("an out-of-range value was saved"); return
        }
        #expect(error.message == "temperature must be between 0 and 2.")
    }
}
