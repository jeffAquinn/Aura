import Foundation
import Testing
@testable import AuraKit

private final class HOLOverviewOnlyTransport: AuraTransport, @unchecked Sendable {
    let nextOverview: HOLOverview

    init(nextOverview: HOLOverview) { self.nextOverview = nextOverview }

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }
    func hol() async throws -> HOLOverview { nextOverview }
    // The AuraTransport default intentionally makes the independent model request fail.
}

private final class HOLMutableTransport: AuraTransport, @unchecked Sendable {
    var nextOverview: HOLOverview
    var nextCatalog: ModelCatalog?

    init(nextOverview: HOLOverview, nextCatalog: ModelCatalog?) {
        self.nextOverview = nextOverview
        self.nextCatalog = nextCatalog
    }

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }
    func hol() async throws -> HOLOverview { nextOverview }
    func models() async throws -> ModelCatalog {
        guard let nextCatalog else { throw URLError(.cannotConnectToHost) }
        return nextCatalog
    }
}

private final class HOLFailingTransport: AuraTransport, @unchecked Sendable {
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }
    // The AuraTransport defaults make both HOL endpoints fail.
}

private final class OutOfOrderHOLTransport: AuraTransport, @unchecked Sendable {
    let older: HOLOverview
    let newer: HOLOverview
    var holCalls = 0

    init(older: HOLOverview, newer: HOLOverview) {
        self.older = older
        self.newer = newer
    }

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }
    func hol() async throws -> HOLOverview {
        holCalls += 1
        if holCalls == 1 {
            try await Task.sleep(nanoseconds: 50_000_000)
            return older
        }
        return newer
    }
}

@MainActor
struct HOLStoreTests {
    @Test func refreshKeepsFreshSessionsWhenTheModelCatalogIsUnavailable() async {
        let expected = HOLOverview(
            vaultProject: "/vault/Aura",
            sessions: [HOLSession(name: "aura_session_review", purpose: "New purpose.", runner: "codex", prompt: "")],
            savepoints: [],
            defaults: HOLDefaults(model: ModelChoice(provider: "ollama", model: "gemma4:26b"), effort: "medium"),
            notice: ""
        )
        let store = HOLStore { HOLOverviewOnlyTransport(nextOverview: expected) }

        await store.refresh()

        #expect(store.overview == expected)
        #expect(store.selectedModel == expected.defaults.model)
        #expect(store.effort == "medium")
        #expect(store.coherence == .degraded)
        #expect(store.lastError != nil)
    }

    @Test func aPartialRefreshWithholdsAModelCatalogFromAnEarlierGeneration() async {
        let defaults = HOLDefaults(model: ModelChoice(provider: "ollama", model: "gemma4:26b"), effort: "medium")
        let first = HOLOverview(vaultProject: "/vault/Aura", sessions: [], savepoints: [], defaults: defaults, notice: "")
        let catalog = ModelCatalog(roles: [:], roleLabels: [:], ollamaModels: ["gemma4:26b"], anthropicModels: [], anthropicKeySet: false, codingEngine: "claude", sleepMode: SleepMode(model: ""), startupModelProblem: nil)
        let transport = HOLMutableTransport(nextOverview: first, nextCatalog: catalog)
        let store = HOLStore { transport }

        await store.refresh()
        #expect(store.coherence == .complete && store.catalog == catalog)

        let refreshed = HOLOverview(vaultProject: "/vault/Aura", sessions: [HOLSession(name: "new", purpose: "New.", runner: "codex", prompt: "")], savepoints: [], defaults: defaults, notice: "")
        transport.nextOverview = refreshed
        transport.nextCatalog = nil
        await store.refresh()

        #expect(store.overview == refreshed)
        #expect(store.catalog == nil)
        #expect(store.coherence == .degraded)
        #expect(store.lastError != nil)
    }

    @Test func failedHOLAndModelRequestsAreMarkedUnavailable() async {
        let store = HOLStore { HOLFailingTransport() }

        await store.refresh()

        #expect(store.overview == nil && store.catalog == nil)
        #expect(store.coherence == .unavailable)
        #expect(store.lastError != nil)
    }

    @Test func anOlderRefreshCannotOverwriteANewerOverview() async {
        let defaults = HOLDefaults(model: ModelChoice(provider: "ollama", model: "gemma4:26b"), effort: "medium")
        let older = HOLOverview(vaultProject: "/vault/Aura", sessions: [HOLSession(name: "old", purpose: "Old.", runner: "codex", prompt: "")], savepoints: [], defaults: defaults, notice: "")
        let newer = HOLOverview(vaultProject: "/vault/Aura", sessions: [HOLSession(name: "new", purpose: "New.", runner: "codex", prompt: "")], savepoints: [], defaults: defaults, notice: "")
        let transport = OutOfOrderHOLTransport(older: older, newer: newer)
        let store = HOLStore { transport }

        let first = Task { await store.refresh() }
        for _ in 0..<10_000 where transport.holCalls == 0 { await Task.yield() }
        await store.refresh()
        await first.value

        #expect(store.overview == newer)
    }

    @Test func anInFlightRefreshIsExplicitlyMarkedRefreshing() async {
        let defaults = HOLDefaults(model: ModelChoice(provider: "ollama", model: "gemma4:26b"), effort: "medium")
        let overview = HOLOverview(vaultProject: "/vault/Aura", sessions: [], savepoints: [], defaults: defaults, notice: "")
        let transport = OutOfOrderHOLTransport(older: overview, newer: overview)
        let store = HOLStore { transport }

        let refresh = Task { await store.refresh() }
        for _ in 0..<10_000 where transport.holCalls == 0 { await Task.yield() }
        #expect(store.coherence == .refreshing)
        await refresh.value

        #expect(store.coherence == .degraded)
    }
}
