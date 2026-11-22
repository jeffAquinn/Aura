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

        public init(role: Role, text: String, actions: [PendingAction] = [], decisions: [Bool]? = nil) {
            self.role = role
            self.text = text
            self.actions = actions
            self.decisions = decisions
        }
    }

    public private(set) var items: [Item] = []
    public private(set) var phase: Phase = .idle
    public var connection: Connection = .starting
    /// Bumped when the panel opens so the input field can take focus.
    public var focusRequests = 0

    public var transport: (any AuraTransport)? {
        didSet { if transport != nil { connection = .connected } }
    }

    // Voice and identity (Sprint 4). The server owns them; these mirror it.
    public private(set) var voice = VoiceProfile()
    public private(set) var persona = PersonaInfo.initial
    public private(set) var voices: [VoiceInfo] = []
    /// Speaks replies and approval requests when spoken replies are on.
    public var speech: SpeechController? {
        didSet { speech?.profile = voice }
    }
    @ObservationIgnored private var speakingItem: UUID?

    /// The running (or last) turn; tests and the smoke tool await it.
    @ObservationIgnored public private(set) var turnTask: Task<Void, Never>?

    public init() {}

    /// A store frozen in a given state: for previews and UI snapshot tests.
    public init(items: [Item], phase: Phase = .idle, connection: Connection = .connected) {
        self.items = items
        self.phase = phase
        self.connection = connection
    }

    public var canSend: Bool { phase == .idle && transport != nil }
    public var isBusy: Bool { phase == .thinking || phase == .waking || phase == .streaming }

    public func send(_ raw: String) {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, canSend, let transport else { return }
        items.append(Item(role: .user, text: text))
        speech?.stop()  // a new message interrupts whatever Aura was saying
        speech?.beginReply()
        speakingItem = nil
        run(transport.chat(text))
    }

    /// Answer the pending approval card: one decision per action.
    public func decide(_ approvals: [Bool]) {
        guard phase == .awaitingApproval, let transport,
              let i = items.lastIndex(where: { $0.role == .approval && $0.decisions == nil }),
              approvals.count == items[i].actions.count
        else { return }
        items[i].decisions = approvals
        speech?.stop()
        speech?.beginReply()
        run(transport.approve(approvals))
    }

    /// Stop the reply being generated (closes the stream; the server frees the turn) and stop
    /// speaking it.
    public func stop() {
        turnTask?.cancel()
        speech?.stop()
    }

    // MARK: - voice and persona

    public func loadSettings() async {
        guard let settings = try? await transport?.settings() else { return }
        setVoice(settings.voice)
        persona = settings.persona
    }

    /// Report the voices this Mac can speak with, so Aura only ever names real ones.
    public func registerVoices(_ catalog: [VoiceInfo]) async {
        voices = catalog
        try? await transport?.registerVoices(catalog)
    }

    public func updateVoice(_ patch: VoicePatch) async {
        guard let profile = try? await transport?.updateVoice(patch) else { return }
        setVoice(profile)
    }

    public func resetPersona() async {
        if let reset = try? await transport?.resetPersona() { persona = reset }
    }

    private func setVoice(_ profile: VoiceProfile) {
        voice = profile
        speech?.profile = profile
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
                self?.apply(.error(FriendlyMessage.turnFailed(error)))
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
                finishSpokenSegment()  // Aura speaks again after a tool: a new segment
                items.append(Item(role: .aura, text: text))
                speakingItem = items[items.count - 1].id
            }
            speech?.reply(items[items.count - 1].text, final: false)
            phase = .streaming
        case let .approval(actions):
            finishSpokenSegment()
            items.append(Item(role: .approval, text: "", actions: actions))
            speech?.approvalRequested(actions)  // the verbal audit trail
            phase = .awaitingApproval
        case let .error(message):
            items.append(Item(role: .error, text: message))
        case let .voice(profile):
            setVoice(profile)
        case let .persona(name):
            persona.name = name
        case .done:
            break
        }
    }

    /// Speak the last sentence of the current reply segment, then start a fresh one.
    private func finishSpokenSegment() {
        if let id = speakingItem, let item = items.first(where: { $0.id == id }) {
            speech?.reply(item.text, final: true)
        }
        speakingItem = nil
        speech?.beginReply()
    }

    private func endTurn() {
        if phase != .awaitingApproval {
            finishSpokenSegment()
            phase = .idle
        }
    }
}
