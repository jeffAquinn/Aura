import Foundation
import Observation

/// The conversation as the UI shows it, driven by the server's events.
@MainActor
@Observable
public final class ChatStore {
    public enum Phase: Equatable, Sendable {
        case idle, thinking, waking, streaming, stopping, awaitingApproval
    }

    public enum Connection: Equatable, Sendable {
        case starting, connected, offline(String)
    }

    public struct Item: Identifiable, Equatable, Sendable {
        public enum Role: Sendable { case user, aura, status, approval, error, task, build, incoming }
        public let id = UUID()
        public let role: Role
        public var text: String
        public var actions: [PendingAction] = []
        public var decisions: [Bool]?  // approval cards: nil until the user answers
        public var edited = false  // approved with the user's edits (the Specification Approval card)
        public var incoming: IncomingMessage?  // a live Slack or Discord message
        public var developer: String?  // the user's message went to this Developer ("claude", "codex")

        public init(role: Role, text: String, actions: [PendingAction] = [], decisions: [Bool]? = nil, edited: Bool = false,
                    incoming: IncomingMessage? = nil, developer: String? = nil) {
            self.role = role
            self.text = text
            self.actions = actions
            self.decisions = decisions
            self.edited = edited
            self.incoming = incoming
            self.developer = developer
        }

        /// A card asking about the files a Developer changed (one action per file).
        public var isFileChanges: Bool { role == .approval && !actions.isEmpty && actions.allSatisfy { $0.tool == "apply_change" } }

        public static func message(_ m: IncomingMessage) -> Item {
            Item(role: .incoming, text: m.plainText, incoming: m)
        }
    }

    public private(set) var items: [Item] = []
    public private(set) var phase: Phase = .idle
    public var connection: Connection = .starting
    /// Server-observed runtime and governance evidence, refreshed every 30 seconds while connected.
    public private(set) var health: Health?
    /// The chat box's choice: hand messages to this Developer ("claude", "codex"), or nil (Aura decides).
    public var developer: String?
    /// Optional one-turn Aura-model override selected beside the chat box. nil follows the Chat role.
    public var chatModel: ModelChoice?
    /// The concrete model the server selected for the most recent automatic Aura turn.
    public private(set) var automaticModel: ModelChoice?
    /// Bumped when the panel opens so the input field can take focus.
    public var focusRequests = 0

    public var transport: (any AuraTransport)? {
        didSet {
            healthPolling?.cancel()
            if transport != nil { connection = .connected; startHealthPolling() }
            else { health = nil }
        }
    }

    // Voice and identity (Sprint 4). The server owns them; these mirror it.
    public private(set) var voice = VoiceProfile()
    public private(set) var persona = PersonaInfo.initial
    public private(set) var voices: [VoiceInfo] = []
    /// Server-owned model role choices. API keys themselves are never sent back to the app.
    public private(set) var modelCatalog: ModelCatalog?
    /// Speaks replies and approval requests when spoken replies are on.
    public var speech: SpeechController? {
        didSet { speech?.profile = voice }
    }
    @ObservationIgnored private var speakingItem: UUID?
    /// A Developer build started from the chat (the app then follows it, to say when it's ready).
    @ObservationIgnored public var onBuildStarted: (String) -> Void = { _ in }

    /// The running (or last) turn; tests and the smoke tool await it.
    @ObservationIgnored public private(set) var turnTask: Task<Void, Never>?
    @ObservationIgnored private var healthPolling: Task<Void, Never>?

    public init() {}

    deinit { healthPolling?.cancel() }

    private func startHealthPolling() {
        healthPolling = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshHealth()
                try? await Task.sleep(for: .seconds(30))
            }
        }
    }

    public func refreshHealth() async {
        guard let transport else { return }
        health = try? await transport.health()
    }

    /// A store frozen in a given state: for previews and UI snapshot tests.
    public init(items: [Item], phase: Phase = .idle, connection: Connection = .connected) {
        self.items = items
        self.phase = phase
        self.connection = connection
    }

    public var canSend: Bool { phase == .idle && transport != nil }

    /// One message as plain text for the clipboard (Aura's Markdown rendered as text).
    public static func copyText(_ item: Item) -> String {
        switch item.role {
        case .aura: Markdown.plainText(item.text)
        case .approval: item.actions.map(\.description).joined(separator: "\n")
        default: item.text
        }
    }

    /// The whole conversation as plain text: who said what, in order (tool lines and cards
    /// included, so the record is complete).
    public func conversationText(you: String = "You") -> String {
        items.compactMap { item -> String? in
            switch item.role {
            case .user: item.developer.map { "\(you) (to \(EngineInfo.named($0)?.label ?? $0)): \(item.text)" } ?? "\(you): \(item.text)"
            case .aura: "\(persona.name): \(Markdown.plainText(item.text))"
            case .status: "  [\(item.text.replacingOccurrences(of: #"^\[Tool: [^\]]*\]\s*"#, with: "", options: .regularExpression))]"
            case .approval: "  [Approval: \(item.actions.map { $0.description.components(separatedBy: "\n").first ?? "" }.joined(separator: "; "))]"
            case .error: "  [\(item.text)]"
            case .task, .build: "  [\(item.role == .build ? "Building" : "Auditing") the \(item.text) skill]"
            case .incoming: "  [\(item.text)]"
            }
        }
        .joined(separator: "\n\n")
    }
    public var isBusy: Bool { phase == .thinking || phase == .waking || phase == .streaming || phase == .stopping }
    public var isStopping: Bool { phase == .stopping }

    /// The prefix of a Developer's live progress lines ("[Tool: Developer] · Claude Code: Writing …").
    public static let progress = "[Tool: Developer] · "

    public func send(_ raw: String) {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, canSend, let transport else { return }
        if developer == nil && chatModel == nil { automaticModel = nil }
        items.append(Item(role: .user, text: text, developer: developer))
        speech?.stop()  // a new message interrupts whatever Aura was saying
        speech?.beginReply()
        speakingItem = nil
        run(transport.chat(text, developer: developer, model: developer == nil ? chatModel : nil))
    }

    /// Answer the pending approval card: one decision per action.
    public func decide(_ approvals: [Bool]) {
        decide(decisions: approvals.map { $0 ? .allow : .deny })
    }

    /// The same, where an answer can approve with edits (only fields the tool allows; the server
    /// checks).
    public func decide(decisions: [ApprovalDecision]) {
        guard phase == .awaitingApproval, let transport,
              let i = items.lastIndex(where: { $0.role == .approval && $0.decisions == nil }),
              decisions.count == items[i].actions.count
        else { return }
        let edited = decisions.contains { if case .allowEdited = $0 { true } else { false } }
        items[i].decisions = decisions.map(\.approved)
        items[i].edited = edited
        speech?.stop()
        speech?.beginReply()
        run(edited ? transport.approve(decisions: decisions) : transport.approve(decisions.map(\.approved)))
    }

    /// Stop the reply being generated and stop speaking it. The server stops the turn (and a
    /// Developer's container) and, once it is cleaned up, ends the stream with `stopped`. If the
    /// server can't be asked, the stream is closed instead, which the server treats the same way.
    public func stop() {
        speech?.stop()
        // A second click used to start a second stop request before the first round trip ended.
        // Keep the original stream alive for its authoritative `stopped` event, but visibly and
        // logically lock the control after one request.
        guard isBusy else { return }
        guard !isStopping else { return }
        guard let transport, let task = turnTask else {
            turnTask?.cancel()
            return
        }
        phase = .stopping
        Task {
            let stopped = (try? await transport.stopTurn()) ?? false
            if !stopped { task.cancel() }
        }
    }

    /// Close the stream at once (the server is gone, or the conversation is being replaced).
    private func cancelTurn() {
        turnTask?.cancel()
        speech?.stop()
    }

    /// After the app (re)connects: show the conversation the server kept, any approval still
    /// waiting for an answer, and what a restart cut short. Only into an empty chat.
    public func restoreConversation() async {
        guard items.isEmpty, phase == .idle, let info = try? await transport?.conversation() else { return }
        guard items.isEmpty else { return }  // the user started typing meanwhile
        items = info.messages.map { Item(role: $0.role == "user" ? .user : .aura, text: $0.text) }
        if let note = info.recoveryNote {
            items.append(Item(role: .status, text: note))
        }
        if let pending = info.pending, !pending.actions.isEmpty {
            items.append(Item(role: .approval, text: "", actions: pending.actions))
            phase = .awaitingApproval
        }
    }

    // MARK: - live messages (Slack, Discord)

    /// Messages that arrived while Aura was replying: shown once the reply ends, so a reply is
    /// never split in two.
    @ObservationIgnored private var held: [IncomingMessage] = []

    /// New Slack or Discord messages, as cards in the conversation. (The server also gives them to
    /// Aura at the start of the user's next message.)
    public func receive(_ messages: [IncomingMessage]) {
        if isBusy {
            held += messages
            return
        }
        items += messages.map(Item.message)
    }

    // MARK: - voice and persona

    public func loadSettings() async {
        guard let settings = try? await transport?.settings() else { return }
        setVoice(settings.voice)
        persona = settings.persona
        if let catalog = try? await transport?.models() { modelCatalog = catalog }
    }

    public func updateModels(_ roles: [String: ModelChoice]) async -> String? {
        guard let catalog = try? await transport?.updateModels(ModelRoutingUpdate(roles: roles)) else {
            return "Aura could not save the model settings."
        }
        modelCatalog = catalog
        return nil
    }

    /// Refresh only after the user asks; opening Settings does not contact a cloud provider.
    public func syncCloudModels() async -> String? {
        guard let transport else { return FriendlyMessage.offline }
        do {
            modelCatalog = try await transport.syncCloudModels()
            return nil
        } catch let AuraClientError.http(_, message) {
            return message
        } catch {
            return FriendlyMessage.describe(error)
        }
    }

    public func updateSleepMode(_ mode: SleepMode) async -> String? {
        guard let transport else { return FriendlyMessage.offline }
        do {
            modelCatalog = try await transport.updateSleepMode(mode)
            return nil
        } catch let AuraClientError.http(_, message) {
            return message
        } catch {
            return FriendlyMessage.describe(error)
        }
    }

    /// Downloading is deliberately a direct Settings action. The server permits only a model name
    /// through the local Ollama API and refreshes the picker after Ollama reports success.
    public func pullLocalModel(_ model: String) async -> String? {
        let model = model.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !model.isEmpty, let transport else { return FriendlyMessage.offline }
        do {
            modelCatalog = try await transport.pullLocalModel(model)
            return nil
        } catch let AuraClientError.http(_, message) {
            return message
        } catch {
            return FriendlyMessage.describe(error)
        }
    }

    /// Report the voices this Mac can speak with, so Aura only ever names real ones.
    public func registerVoices(_ catalog: [VoiceInfo]) async {
        voices = catalog
        try? await transport?.registerVoices(catalog)
        // The server's list adds Aura's own neural voices, best first.
        if let all = try? await transport?.voices(), !all.isEmpty { voices = all }
    }

    public func updateVoice(_ patch: VoicePatch) async {
        guard let profile = try? await transport?.updateVoice(patch) else { return }
        setVoice(profile)
    }

    public func resetPersona() async {
        if let reset = try? await transport?.resetPersona() { persona = reset }
    }

    // MARK: - keys (Settings → Keys; values go to the macOS Keychain and never come back)

    public private(set) var secrets: [SecretInfo] = []

    public func loadSecrets() async {
        if let listing = try? await transport?.secrets() { secrets = listing }
    }

    /// Store a key. Returns why it was refused (nil: saved).
    public func saveSecret(_ name: String, value: String) async -> String? {
        let value = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let transport else { return FriendlyMessage.offline }
        do {
            let saved = try await transport.setSecret(name, value: value)
            if let i = secrets.firstIndex(where: { $0.name == name }) { secrets[i] = saved } else { secrets.append(saved) }
            return nil
        } catch let AuraClientError.http(_, message) {
            return message
        } catch {
            return FriendlyMessage.describe(error)
        }
    }

    public func removeSecret(_ name: String) async {
        try? await transport?.deleteSecret(name)
        await loadSecrets()
    }

    private func setVoice(_ profile: VoiceProfile) {
        voice = profile
        speech?.profile = profile
    }

    /// The server went away (it restarts with fresh state): end the turn, expire any open card.
    public func connectionLost(_ reason: String) {
        cancelTurn()
        if let i = items.lastIndex(where: { $0.role == .approval && $0.decisions == nil }) {
            items[i].decisions = []  // expired
        }
        phase = .idle
        transport = nil
        connection = .offline(reason)
    }

    public func newConversation() async {
        cancelTurn()
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
        case let .model(choice):
            if developer == nil && chatModel == nil { automaticModel = choice }
        case .thinking:
            if phase != .waking && !isStopping { phase = .thinking }
        case .waking:
            if !isStopping { phase = .waking }
        case let .status(text):
            guard !isStopping else { return }
            // A Developer's live progress: one line that keeps updating, not a line per step.
            if text.hasPrefix(Self.progress), let last = items.indices.last, items[last].role == .status,
               items[last].text.hasPrefix(Self.progress) {
                items[last].text = text
            } else {
                items.append(Item(role: .status, text: text))
            }
            if text.contains("Skill_Tool") {  // Gemma: installed skills get a spoken status line
                speech?.announce(text.replacingOccurrences(of: #"^\[Tool: [^\]]*\]\s*"#, with: "", options: .regularExpression))
            }
            phase = .streaming
        case let .text(text):
            guard !isStopping else { return }
            if let last = items.indices.last, items[last].role == .aura {
                items[last].text += text
            } else {
                finishSpokenSegment()  // Aura speaks again after a tool: a new segment
                items.append(Item(role: .aura, text: text))
                speakingItem = items[items.count - 1].id
            }
            // A sentence can only end at one of these: re-planning on every chunk would re-parse
            // the whole reply each time (quadratic on a long reply).
            if text.contains(where: { ".!?:;\n".contains($0) }) {
                speech?.reply(items[items.count - 1].text, final: false)
            }
            phase = .streaming
        case let .approval(actions):
            guard !isStopping else { return }
            finishSpokenSegment()
            items.append(Item(role: .approval, text: "", actions: actions))
            speech?.approvalRequested(actions)  // the verbal audit trail
            phase = .awaitingApproval
        case let .error(message):
            items.append(Item(role: .error, text: message))
        case let .voice(profile):
            setVoice(profile)
            speech?.warmUp()  // e.g. "talk to me" just switched spoken replies on
        case let .persona(name):
            persona.name = name
        case let .task(id, skill):
            items.append(Item(role: .task, text: skill, actions: [PendingAction(tool: "task", description: id)]))
        case let .build(id, skill):
            items.append(Item(role: .build, text: skill, actions: [PendingAction(tool: "build", description: id)]))
            onBuildStarted(id)
        case let .stopped(partial):
            items.append(Item(role: .status, text: partial
                ? "Stopped. Whatever Aura had already done stays done."
                : "Stopped."))
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
        if !held.isEmpty, !isBusy {
            items += held.map(Item.message)
            held.removeAll()
        }
    }
}
