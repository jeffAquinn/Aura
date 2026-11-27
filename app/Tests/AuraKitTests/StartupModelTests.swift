import Foundation
import Testing
@testable import AuraKit

private let qwen = ModelChoice(provider: "ollama", model: "qwen3.5:9b")
private let gemma = ModelChoice(provider: "ollama", model: "gemma4:26b")
private let bonsai = ModelChoice(provider: "llamacpp", model: "bonsai-2.5-27b")

private func catalog(startup: ModelChoice, problem: String? = nil) -> ModelCatalog {
    ModelCatalog(roles: ["startup": startup, "chat": gemma, "deep_research": gemma, "web_search": qwen,
                         "coding": ModelChoice(provider: "anthropic", model: "claude-fable-5-2")],
                 roleLabels: [:], ollamaModels: ["gemma4:26b", "qwen3.5:9b"], anthropicModels: [],
                 anthropicKeySet: false, codingEngine: "claude", sleepMode: SleepMode(model: ""),
                 startupModelProblem: problem, llamacppModels: ["bonsai-2.5-27b"])
}

/// Serves saved settings the way the Aura server does; records the models a message was sent with.
private final class SettingsTransport: AuraTransport, @unchecked Sendable {
    var saved: ModelCatalog
    private(set) var sentWith: [ModelChoice?] = []
    private(set) var prepared: [ModelChoice] = []

    init(_ saved: ModelCatalog) { self.saved = saved }

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { chat(text, developer: nil, model: nil) }
    func chat(_ text: String, developer: String?, model: ModelChoice?) -> AsyncThrowingStream<AuraEvent, Error> {
        sentWith.append(model)
        return AsyncThrowingStream { $0.finish() }
    }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
    func settings() async throws -> AppSettings {
        AppSettings(voice: VoiceProfile(), persona: .initial, voicesRegistered: 0,
                    modelRouting: saved.roles, startupModelProblem: saved.startupModelProblem)
    }
    func models() async throws -> ModelCatalog { saved }
    func prepareModel(_ choice: ModelChoice) async throws { prepared.append(choice) }
    func updateModels(_ update: ModelRoutingUpdate) async throws -> ModelCatalog {
        saved = ModelCatalog(roles: update.roles, roleLabels: saved.roleLabels, ollamaModels: saved.ollamaModels,
                             anthropicModels: saved.anthropicModels, anthropicKeySet: saved.anthropicKeySet,
                             codingEngine: saved.codingEngine, sleepMode: saved.sleepMode,
                             startupModelProblem: nil, llamacppModels: saved.llamacppModels)
        return saved
    }
}

@MainActor
struct StartupModelTests {
    @Test func theChatBoxStartsOnTheSavedStartupModel() async {
        let transport = SettingsTransport(catalog(startup: bonsai))
        let store = ChatStore()
        store.transport = transport
        await store.loadSettings()
        #expect(store.chatModel == bonsai)

        store.send("hello")
        await store.turnTask?.value
        #expect(transport.sentWith == [bonsai])  // the first message runs on it, not on Auto's Chat role
    }

    @Test func changingTheStartupModelInSettingsMovesTheChatBoxWithIt() async {
        let transport = SettingsTransport(catalog(startup: qwen))
        let store = ChatStore()
        store.transport = transport
        await store.loadSettings()
        var roles = transport.saved.roles
        roles["startup"] = bonsai
        #expect(await store.updateModels(roles) == nil)
        #expect(store.chatModel == bonsai)
    }

    @Test func aModelPickedInTheChatBoxStaysWhenSettingsChange() async {
        let transport = SettingsTransport(catalog(startup: qwen))
        let store = ChatStore()
        store.transport = transport
        await store.loadSettings()
        store.chatModel = nil  // the user chose "Automatic"
        var roles = transport.saved.roles
        roles["startup"] = bonsai
        _ = await store.updateModels(roles)
        #expect(store.chatModel == nil)

        store.chatModel = gemma  // or another model
        roles["startup"] = qwen
        _ = await store.updateModels(roles)
        #expect(store.chatModel == gemma)
    }

    @Test func aStartupModelTheServerCannotRunLeavesTheChatBoxOnAutomatic() async {
        let store = ChatStore()
        store.transport = SettingsTransport(catalog(startup: bonsai, problem: "llama-server is not serving it."))
        await store.loadSettings()
        #expect(store.chatModel == nil)  // Settings shows the problem; a turn would only fail
    }

    @Test func pickingAModelLoadsItRightAway() async throws {
        let transport = SettingsTransport(catalog(startup: qwen))
        let store = ChatStore()
        store.transport = transport
        await store.loadSettings()  // the Startup model goes in the chat box (the server loads it)
        store.pickChatModel(bonsai)  // a llama.cpp model: Aura starts llama-server for it
        store.pickChatModel(bonsai)  // the same pick again: nothing more
        store.pickChatModel(nil)  // Automatic: the next message decides
        for _ in 0..<100 where transport.prepared.isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        #expect(transport.prepared == [bonsai])
    }
}
