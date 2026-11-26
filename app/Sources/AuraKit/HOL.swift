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

/// Public readiness of an account-backed coding harness.  It never includes credentials.
public struct HOLEngine: Codable, Sendable, Equatable, Identifiable {
    public let name: String
    public let label: String
    public let problem: String?
    public var id: String { name }
}

public struct HOLOverview: Codable, Sendable, Equatable {
    /// Opaque server validator binding the sessions and allowed model catalog in this response.
    public let snapshot: String
    public let vaultProject: String
    public let sessions: [HOLSession]
    public let savepoints: [HOLSavepoint]
    public let defaults: HOLDefaults
    public let catalog: ModelCatalog
    public let notice: String
    public let developers: [HOLEngine]

    public init(snapshot: String, vaultProject: String, sessions: [HOLSession], savepoints: [HOLSavepoint], defaults: HOLDefaults,
                catalog: ModelCatalog, notice: String, developers: [HOLEngine] = []) {
        self.snapshot = snapshot
        self.vaultProject = vaultProject; self.sessions = sessions; self.savepoints = savepoints
        self.defaults = defaults; self.catalog = catalog; self.notice = notice; self.developers = developers
    }
}

public struct HOLStart: Encodable, Sendable {
    public let snapshot: String
    public let model: ModelChoice
    public let effort: String?
    public let engine: String?

    public init(snapshot: String, model: ModelChoice, effort: String?, engine: String? = nil) {
        self.snapshot = snapshot; self.model = model; self.effort = effort; self.engine = engine
    }
}

public struct HOLRun: Codable, Sendable, Equatable {
    public let session: String
    public let workingDirectory: String
    public let model: ModelChoice
    public let effort: String?
    public let prompt: String
    public let output: [String]
    public let engine: String?
    public let terminal: HOLTerminalState
}

public struct HOLChange: Codable, Sendable, Equatable, Identifiable {
    public let path: String
    public let kind: String
    public let description: String
    public let added: Int
    public let removed: Int
    public var id: String { "\(kind):\(path)" }
}

public struct HOLMessageResult: Codable, Sendable, Equatable {
    public let output: [String]
    public let changes: [HOLChange]
}

public struct HOLTerminalState: Codable, Sendable, Equatable {
    public let id: String
    public let running: Bool
    public let output: [String]
    public let next: Int
}

/// The honesty level of the current HOL refresh. A complete state means both independent endpoints
/// responded for the same client refresh generation; it does not claim a server snapshot.
public enum HOLCoherence: String, Sendable, Equatable {
    case connecting
    case refreshing
    case complete
    case degraded
    case unavailable
}

@MainActor
@Observable
public final class HOLStore {
    public private(set) var overview: HOLOverview?
    public private(set) var catalog: ModelCatalog?
    public var selectedModel = ModelChoice(provider: "ollama", model: "")
    /// nil selects the ordinary offline terminal; a value routes through Aura's isolated
    /// account-backed Developer container rather than exposing credentials to that terminal.
    public var selectedEngine: String?
    public var effort = "none"
    public private(set) var output: [String] = []
    public private(set) var terminal: HOLTerminalState?
    public private(set) var lastError: String?
    public private(set) var coherence: HOLCoherence = .connecting
    public private(set) var busy = false
    public private(set) var activeEngine: String?
    public private(set) var pendingChanges: [HOLChange] = []

    @ObservationIgnored private let transport: () -> (any AuraTransport)?
    @ObservationIgnored private var terminalPoll: Task<Void, Never>?
    /// Only the newest refresh may publish state; refreshes can overlap after a deployment.
    @ObservationIgnored private var refreshGeneration = 0
    /// The desktop host may mirror this safe terminal output elsewhere.  It is deliberately
    /// one-way: ChatView input is never forwarded to a HOL container.
    @ObservationIgnored public var onOutput: ([String]) -> Void = { _ in }

    public init(transport: @escaping () -> (any AuraTransport)?) { self.transport = transport }

    public func refresh() async {
        guard let transport = transport() else { return }
        refreshGeneration &+= 1
        let generation = refreshGeneration
        coherence = .refreshing
        do {
            let overview = try await transport.hol()
            guard generation == refreshGeneration else { return }
            self.overview = overview
            self.catalog = overview.catalog
            if selectedModel.model.isEmpty { selectedModel = overview.defaults.model }
            if let saved = overview.defaults.effort { effort = saved }
            coherence = .complete
            lastError = nil
        } catch {
            guard generation == refreshGeneration else { return }
            // Do not present model choices from an older snapshot as if they supported a new one.
            catalog = nil
            coherence = .unavailable
            lastError = FriendlyMessage.describe(error)
        }
    }

    public func start(_ session: HOLSession) async {
        guard let transport = transport(), let overview, coherence == .complete else {
            lastError = "Refresh HOL before preparing a session."
            return
        }
        busy = true
        defer { busy = false }
        do {
            let run = try await transport.startHOL(session.name, request: HOLStart(snapshot: overview.snapshot, model: selectedModel, effort: effort,
                                                                                    engine: selectedEngine))
            terminal = run.terminal
            activeEngine = run.engine
            pendingChanges = []
            appendOutput(run.output + run.terminal.output)
            startPolling()
            lastError = nil
        } catch {
            // The server rejected an availability validator. Refresh once so the user can make a
            // new explicit choice; never repeat the start request on their behalf.
            if case let AuraClientError.http(status: 409, message) = error,
               message.localizedCaseInsensitiveContains("HOL availability changed") {
                await refresh()
                if coherence == .complete {
                    lastError = "HOL availability changed. Review the refreshed session and model choices, then prepare it again."
                }
            } else {
                lastError = FriendlyMessage.describe(error)
            }
        }
    }

    public func savepoint() async {
        guard let transport = transport() else { return }
        busy = true
        defer { busy = false }
        do {
            let point = try await transport.createHOLSavepoint("Before HOL changes")
            appendOutput(["Savepoint created: \(point.label)"])
            await refresh()
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func restore(_ point: HOLSavepoint) async {
        guard let transport = transport() else { return }
        busy = true
        defer { busy = false }
        do {
            _ = try await transport.restoreHOLSavepoint(point.id)
            appendOutput(["Restored savepoint: \(point.label)"])
            await refresh()
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func restoreOriginal() async {
        guard let transport = transport() else { return }
        busy = true
        defer { busy = false }
        do {
            try await transport.restoreHOLOriginal()
            appendOutput(["Restored clean Git HEAD into the Vault Aura copy."])
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

    public func sendMessage(_ message: String) async {
        guard let transport = transport(), let terminal, activeEngine != nil else { return }
        busy = true
        defer { busy = false }
        do {
            let result = try await transport.sendHOLMessage(terminal.id, message: message)
            appendOutput(result.output)
            pendingChanges = result.changes
            lastError = nil
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func decideChanges(_ allowed: [Bool]) async {
        guard let transport = transport(), let terminal, !pendingChanges.isEmpty else { return }
        busy = true
        defer { busy = false }
        do {
            let result = try await transport.decideHOLChanges(terminal.id, allowed: allowed)
            appendOutput(result.output)
            pendingChanges = result.changes
            lastError = nil
        } catch { lastError = FriendlyMessage.describe(error) }
    }

    public func stopTerminal() async {
        guard let transport = transport(), let terminal else { return }
        do {
            let state = try await transport.stopHOLTerminal(terminal.id)
            accept(state)
            terminalPoll?.cancel()
            activeEngine = nil
            pendingChanges = []
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
        appendOutput(state.output)
        terminal = state
    }

    private func appendOutput(_ lines: [String]) {
        guard !lines.isEmpty else { return }
        output += lines
        onOutput(lines)
    }
}
