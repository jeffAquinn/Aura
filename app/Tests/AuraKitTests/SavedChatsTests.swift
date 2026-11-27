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
