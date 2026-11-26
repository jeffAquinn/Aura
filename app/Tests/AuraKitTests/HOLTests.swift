import Foundation
import Testing
@testable import AuraKit

private let catalog = ModelCatalog(roles: [:], roleLabels: [:], ollamaModels: ["gemma4:26b"], anthropicModels: [],
                                   anthropicKeySet: false, codingEngine: "claude", sleepMode: SleepMode(model: ""),
                                   startupModelProblem: nil)

private func overview(_ session: String = "aura_session_review", snapshot: String = String(repeating: "a", count: 64)) -> HOLOverview {
    HOLOverview(snapshot: snapshot, vaultProject: "/vault/Aura",
                sessions: [HOLSession(name: session, purpose: "Review Aura.", runner: "codex", prompt: "")],
                savepoints: [], defaults: HOLDefaults(model: ModelChoice(provider: "ollama", model: "gemma4:26b"), effort: "medium"),
                catalog: catalog, notice: "")
}

private final class HOLMutableTransport: AuraTransport, @unchecked Sendable {
    var nextOverview: HOLOverview?
    var startError: Error?
    var starts: [HOLStart] = []

    init(nextOverview: HOLOverview?) { self.nextOverview = nextOverview }

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }
    func hol() async throws -> HOLOverview {
        guard let nextOverview else { throw URLError(.cannotConnectToHost) }
        return nextOverview
    }
    func startHOL(_ name: String, request: HOLStart) async throws -> HOLRun {
        starts.append(request)
        if let startError { throw startError }
        throw AuraClientError.badResponse
    }
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
    @Test func refreshPublishesOneAtomicSessionAndModelSnapshot() async {
        let expected = overview(snapshot: String(repeating: "b", count: 64))
        let store = HOLStore { HOLMutableTransport(nextOverview: expected) }

        await store.refresh()

        #expect(store.overview == expected && store.catalog == expected.catalog)
        #expect(store.selectedModel == expected.defaults.model && store.effort == "medium")
        #expect(store.coherence == .complete && store.lastError == nil)
    }

    @Test func aFailedSnapshotWithholdsPriorModelChoicesAndBlocksNewStarts() async {
        let transport = HOLMutableTransport(nextOverview: overview())
        let store = HOLStore { transport }
        await store.refresh()
        transport.nextOverview = nil

        await store.refresh()

        #expect(store.catalog == nil && store.coherence == .unavailable && store.lastError != nil)
        await store.start(overview().sessions[0])
        #expect(store.lastError == "Refresh HOL before preparing a session.")
    }

    @Test func anOlderRefreshCannotOverwriteANewerAtomicSnapshot() async {
        let older = overview("old", snapshot: String(repeating: "c", count: 64))
        let newer = overview("new", snapshot: String(repeating: "d", count: 64))
        let transport = OutOfOrderHOLTransport(older: older, newer: newer)
        let store = HOLStore { transport }

        let first = Task { await store.refresh() }
        for _ in 0..<10_000 where transport.holCalls == 0 { await Task.yield() }
        await store.refresh()
        await first.value

        #expect(store.overview == newer && store.catalog == newer.catalog && store.coherence == .complete)
    }

    @Test func aStaleSnapshotRejectionRefreshesButNeverRetriesTheStart() async {
        let transport = HOLMutableTransport(nextOverview: overview(snapshot: String(repeating: "e", count: 64)))
        let store = HOLStore { transport }
        await store.refresh()
        transport.nextOverview = overview(snapshot: String(repeating: "f", count: 64))
        transport.startError = AuraClientError.http(status: 409, message: "HOL availability changed. Refresh the HOL Terminal before starting this session.")

        await store.start(overview().sessions[0])

        #expect(transport.starts.count == 1)
        #expect(store.overview?.snapshot == String(repeating: "f", count: 64))
        #expect(store.coherence == .complete)
        #expect(store.lastError?.contains("availability changed") == true)
    }

    @Test func anInFlightRefreshIsExplicitlyMarkedRefreshing() async {
        let transport = OutOfOrderHOLTransport(older: overview(), newer: overview())
        let store = HOLStore { transport }

        let refresh = Task { await store.refresh() }
        for _ in 0..<10_000 where transport.holCalls == 0 { await Task.yield() }
        #expect(store.coherence == .refreshing)
        await refresh.value

        #expect(store.coherence == .complete)
    }
}
