import Foundation
import Observation

/// A Vault-only Human-Out-of-Loop session. The task prompt is audited through Workshop; it is
/// not an arbitrary host shell command.
public struct HOLSession: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let purpose: String
    public let runner: String
    public let prompt: String
}

public struct HOLSavepoint: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let created: String
    public let label: String
}

public struct HOLDefaults: Codable, Sendable, Equatable {
    public let model: ModelChoice
    public let effort: String?
}

public struct HOLOverview: Codable, Sendable, Equatable {
    public let vaultProject: String
    public let sessions: [HOLSession]
    public let savepoints: [HOLSavepoint]
    public let defaults: HOLDefaults
    public let notice: String
}

public struct HOLStart: Encodable, Sendable {
    public let model: ModelChoice
    public let effort: String?
}

public struct HOLRun: Codable, Sendable, Equatable {
    public let session: String
    public let workingDirectory: String
    public let model: ModelChoice
    public let effort: String?
    public let prompt: String
    public let output: [String]
    public let terminal: HOLTerminalState
}

public struct HOLTerminalState: Codable, Sendable, Equatable {
    public let id: String
    public let running: Bool
    public let output: [String]
    public let next: Int
}

@MainActor
@Observable
public final class HOLStore {
    public private(set) var overview: HOLOverview?
    public private(set) var catalog: ModelCatalog?
    public var selectedModel = ModelChoice(provider: "ollama", model: "")
    public var effort = "none"
    public private(set) var output: [String] = []
    public private(set) var terminal: HOLTerminalState?
    public private(set) var lastError: String?
    public private(set) var busy = false

    @ObservationIgnored private let transport: () -> (any AuraTransport)?
    @ObservationIgnored private var terminalPoll: Task<Void, Never>?

    public init(transport: @escaping () -> (any AuraTransport)?) { self.transport = transport }

    public func refresh() async {
        guard let transport = transport() else { return }
        do {
            async let nextOverview = transport.hol()
            async let nextCatalog = transport.models()
            let (overview, catalog) = try await (nextOverview, nextCatalog)
            self.overview = overview
            self.catalog = catalog
            if selectedModel.model.isEmpty { selectedModel = overview.defaults.model }
            if let saved = overview.defaults.effort { effort = saved }
            lastError = nil
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func start(_ session: HOLSession) async {
        guard let transport = transport() else { return }
        busy = true
        defer { busy = false }
        do {
            let run = try await transport.startHOL(session.name, request: HOLStart(model: selectedModel, effort: effort))
            terminal = run.terminal
            output += run.output + run.terminal.output
            startPolling()
            lastError = nil
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func savepoint() async {
        guard let transport = transport() else { return }
        busy = true
        defer { busy = false }
        do {
            let point = try await transport.createHOLSavepoint("Before HOL changes")
            output += ["Savepoint created: \(point.label)"]
            await refresh()
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func restore(_ point: HOLSavepoint) async {
        guard let transport = transport() else { return }
        busy = true
        defer { busy = false }
        do {
            _ = try await transport.restoreHOLSavepoint(point.id)
            output += ["Restored savepoint: \(point.label)"]
            await refresh()
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func restoreOriginal() async {
        guard let transport = transport() else { return }
        busy = true
        defer { busy = false }
        do {
            try await transport.restoreHOLOriginal()
            output += ["Restored clean Git HEAD into the Vault Aura copy."]
            await refresh()
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func send(_ command: String) async {
        guard let transport = transport(), let terminal else { return }
        do {
            let state = try await transport.sendHOLCommand(terminal.id, command: command)
            accept(state)
            lastError = nil
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func stopTerminal() async {
        guard let transport = transport(), let terminal else { return }
        do {
            let state = try await transport.stopHOLTerminal(terminal.id)
            accept(state)
            terminalPoll?.cancel()
            lastError = nil
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    private func startPolling() {
        terminalPoll?.cancel()
        terminalPoll = Task { [weak self] in
            while let self, !Task.isCancelled, self.terminal?.running == true {
                try? await Task.sleep(for: .milliseconds(350))
                await self.refreshTerminal()
            }
        }
    }

    private func refreshTerminal() async {
        guard let transport = transport(), let terminal else { return }
        do { accept(try await transport.holTerminal(terminal.id, after: terminal.next)) }
        catch { lastError = FriendlyMessage.describe(error); terminalPoll?.cancel() }
    }

    private func accept(_ state: HOLTerminalState) {
        output += state.output
        terminal = state
    }
}
