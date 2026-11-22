import Foundation
import Observation

/// The conversation as the UI shows it, driven by the server's events.
@MainActor
@Observable
public final class ChatStore {
    public enum Phase: Equatable, Sendable {
        case idle, thinking, waking, streaming, awaitingApproval
    }

    public enum Connection: Equatable, Sendable {
        case starting, connected, offline(String)
    }

    public struct Item: Identifiable, Equatable, Sendable {
        public enum Role: Sendable { case user, aura, status, approval, error }
        public let id = UUID()
        public let role: Role
        public var text: String
        public var actions: [PendingAction] = []
        public var decisions: [Bool]?  // approval cards: nil until the user answers
    }

    public private(set) var items: [Item] = []
    public private(set) var phase: Phase = .idle
    public var connection: Connection = .starting
    /// Bumped when the panel opens so the input field can take focus.
    public var focusRequests = 0

    public var transport: (any AuraTransport)? {
        didSet { if transport != nil { connection = .connected } }
    }

    /// The running (or last) turn; tests and the smoke tool await it.
    @ObservationIgnored public private(set) var turnTask: Task<Void, Never>?

    public init() {}

    public var canSend: Bool { phase == .idle && transport != nil }
    public var isBusy: Bool { phase == .thinking || phase == .waking || phase == .streaming }

    public func send(_ raw: String) {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, canSend, let transport else { return }
        items.append(Item(role: .user, text: text))
        run(transport.chat(text))
    }

    /// Answer the pending approval card: one decision per action.
    public func decide(_ approvals: [Bool]) {
        guard phase == .awaitingApproval, let transport,
              let i = items.lastIndex(where: { $0.role == .approval && $0.decisions == nil }),
              approvals.count == items[i].actions.count
        else { return }
        items[i].decisions = approvals
        run(transport.approve(approvals))
    }

    /// Stop the reply being generated (closes the stream; the server frees the turn).
    public func stop() {
        turnTask?.cancel()
    }

    /// The server went away (it restarts with fresh state): end the turn, expire any open card.
    public func connectionLost(_ reason: String) {
        stop()
        if let i = items.lastIndex(where: { $0.role == .approval && $0.decisions == nil }) {
            items[i].decisions = []  // expired
        }
        phase = .idle
        transport = nil
        connection = .offline(reason)
    }

    public func newConversation() async {
        stop()
        await turnTask?.value
        try? await transport?.newConversation()
        items.removeAll()
        phase = .idle
    }

    // MARK: - events

    private func run(_ events: AsyncThrowingStream<AuraEvent, Error>) {
        phase = .thinking
        turnTask = Task { [weak self] in
            do {
                for try await event in events {
                    self?.apply(event)
                }
            } catch is CancellationError {
            } catch let error as URLError where error.code == .cancelled {
            } catch {
                self?.apply(.error(error.localizedDescription))
            }
            self?.endTurn()
        }
    }

    func apply(_ event: AuraEvent) {
        switch event {
        case .thinking:
            if phase != .waking { phase = .thinking }
        case .waking:
            phase = .waking
        case let .status(text):
            items.append(Item(role: .status, text: text))
            phase = .streaming
        case let .text(text):
            if let last = items.indices.last, items[last].role == .aura {
                items[last].text += text
            } else {
                items.append(Item(role: .aura, text: text))
            }
            phase = .streaming
        case let .approval(actions):
            items.append(Item(role: .approval, text: "", actions: actions))
            phase = .awaitingApproval
        case let .error(message):
            items.append(Item(role: .error, text: message))
        case .done:
            break
        }
    }

    private func endTurn() {
        if phase != .awaitingApproval { phase = .idle }
    }
}
