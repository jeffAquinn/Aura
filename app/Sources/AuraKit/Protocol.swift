import Foundation

/// An action Aura wants to take that needs the user's approval.
public struct PendingAction: Codable, Sendable, Equatable {
    public let tool: String
    public let description: String
    /// The tool call's arguments (the Specification Approval card shows and edits some of them).
    public let args: [String: JSONValue]?

    public init(tool: String, description: String, args: [String: JSONValue]? = nil) {
        self.tool = tool
        self.description = description
        self.args = args
    }

    /// A Developer build Aura is asking to start (a develop_skill call), if this is one.
    public var build: BuildProposal? { BuildProposal(self) }
    /// A job in the vault Aura is asking to hand to Claude Code or Codex, if this is one.
    public var developerTask: DeveloperTask? { DeveloperTask(self) }
}

/// Any JSON value (tool arguments arrive untyped).
public enum JSONValue: Codable, Sendable, Equatable {
    case string(String), number(Double), bool(Bool), array([JSONValue]), object([String: JSONValue]), null

    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case let .string(s): try c.encode(s)
        case let .number(n): try c.encode(n)
        case let .bool(b): try c.encode(b)
        case let .array(a): try c.encode(a)
        case let .object(o): try c.encode(o)
        case .null: try c.encodeNil()
        }
    }

    public var string: String? { if case let .string(s) = self { s } else { nil } }
    public var bool: Bool? { if case let .bool(b) = self { b } else { nil } }
}

/// The Specification Approval card's subject: who would build what, from which specification.
/// One file a Developer changed in the vault (an apply_change action): what it would do, and the diff.
public struct FileChange: Sendable, Equatable {
    public enum Kind: String, Sendable { case create, modify, delete, createFolder = "create_folder", deleteFolder = "delete_folder" }
    public let path: String
    public let kind: Kind
    public let headline: String  // "change 'Map_of_the_Ascent.md' (+1 −0)"
    public let diff: [String]  // "+added", "-removed", " context", "@@ … @@"

    public init?(_ action: PendingAction) {
        guard action.tool == "apply_change" else { return nil }
        let args = action.args ?? [:]
        path = args["path"]?.string ?? ""
        kind = Kind(rawValue: args["change"]?.string ?? "") ?? .modify
        let lines = action.description.components(separatedBy: "\n")
        headline = (lines.first ?? "").trimmingCharacters(in: CharacterSet(charactersIn: ":"))
        diff = lines.dropFirst().compactMap { line in
            line.hasPrefix("    │ ") ? String(line.dropFirst(6)) : line.hasPrefix("    │") ? "" : nil
        }
    }

    /// The detail after the path: "(+1 −0)", "(1.2 KB, +40 lines)".
    public var detail: String {
        guard let open = headline.range(of: " (", options: .backwards), headline.hasSuffix(")") else { return "" }
        return String(headline[open.upperBound..<headline.index(before: headline.endIndex)])
    }

    public var verb: String {
        switch kind {
        case .create: "New"
        case .modify: "Changed"
        case .delete: "Deleted"
        case .createFolder: "New folder"
        case .deleteFolder: "Removed folder"
        }
    }
}

/// Aura asking to hand a job in the vault to Claude Code or Codex (an ask_developer action).
public struct DeveloperTask: Sendable, Equatable {
    public let engine: String  // "claude" | "codex"
    public let task: String
    /// The server's card text: who gets what, and the limits (first line), then what leaves this Mac.
    public let summary: String
    public let notes: [String]

    public init?(_ action: PendingAction) {
        guard action.tool == "ask_developer" else { return nil }
        let args = action.args ?? [:]
        engine = EngineInfo.engineName(args["engine"]?.string)
        task = (args["task"]?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let lines = action.description.components(separatedBy: "\n").filter { !$0.hasPrefix("    │") }
        summary = (lines.first ?? "").trimmingCharacters(in: CharacterSet(charactersIn: ":"))
        notes = lines.dropFirst().map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
}

public struct BuildProposal: Sendable, Equatable {
    public let skill: String
    public let specification: String
    public let engine: String  // "claude" | "codex"
    public let improve: Bool
    /// The server's card text above the specification: the limits, and exactly what leaves this Mac.
    public let summary: String
    public let notes: [String]

    public init?(_ action: PendingAction) {
        guard action.tool == "develop_skill" else { return nil }
        let args = action.args ?? [:]
        skill = args["name"]?.string ?? ""
        specification = (args["specification"]?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        engine = args["engine"]?.string ?? "claude"
        improve = args["improve"]?.bool ?? false
        let lines = action.description.components(separatedBy: "\n").filter { !$0.hasPrefix("    │") }
        summary = lines.first ?? ""
        notes = lines.dropFirst().map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
}

/// The user's answer to one action on an approval card.
public enum ApprovalDecision: Sendable, Equatable, Encodable {
    case allow, deny
    /// Approve with some arguments changed (only the ones the tool allows, e.g. a specification).
    case allowEdited([String: String])

    public var approved: Bool { self != .deny }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .allow: var c = encoder.singleValueContainer(); try c.encode(true)
        case .deny: var c = encoder.singleValueContainer(); try c.encode(false)
        case let .allowEdited(args):
            struct Edited: Encodable { let approve = true; let args: [String: String] }
            try Edited(args: args).encode(to: encoder)
        }
    }
}

/// One Server-Sent Event from the Aura Server (see docs/sprint-3-design.md §3).
public enum AuraEvent: Sendable, Equatable {
    /// The local or cloud model Aura selected for an automatic turn.
    case model(ModelChoice)
    /// Measured local Ollama model performance for one completed inference call.
    case performance(LocalModelPerformance)
    case thinking
    case waking
    case status(String)
    case text(String)
    case approval([PendingAction])
    case error(String)
    /// Aura changed how it sounds (the set_voice tool).
    case voice(VoiceProfile)
    /// Aura's persona changed (an approved update_persona); carries the (possibly new) name.
    case persona(name: String)
    /// Aura proposed a skill: its audit has started (review it in the Workshop).
    case task(id: String, skill: String)
    /// A Developer build started (after the Specification Approval card); it continues in the Workshop.
    case build(id: String, skill: String)
    /// The turn was stopped (POST /v1/stop) after its cleanup; `partial`: something already ran.
    case stopped(partial: Bool)
    case done
}

/// Evidence from Aura's local-model stream. Durations are measured around the actual request;
/// Ollama's OpenAI-compatible API supplies the token counts but not its native verbose timings.
public struct LocalModelPerformance: Codable, Equatable, Sendable {
    public let totalDuration: Double
    public let promptDuration: Double
    public let promptTokens: Int
    public let promptTokensPerSecond: Double
    public let generationDuration: Double
    public let outputTokens: Int
    public let outputTokensPerSecond: Double

    enum CodingKeys: String, CodingKey {
        case totalDuration = "total_duration"
        case promptDuration = "prompt_duration"
        case promptTokens = "prompt_tokens"
        case promptTokensPerSecond = "prompt_tokens_per_second"
        case generationDuration = "generation_duration"
        case outputTokens = "output_tokens"
        case outputTokensPerSecond = "output_tokens_per_second"
    }
}

/// The conversation as the server keeps it: it survives a restart of the server.
public struct ConversationInfo: Codable, Sendable, Equatable {
    public struct Message: Codable, Sendable, Equatable {
        public let role: String  // "user" | "aura"
        public let text: String
        /// Where the user wrote it, when that wasn't this Mac ("slack").
        public var origin: String? = nil
    }

    public let messages: [Message]
    public let pending: PendingApproval?
    public let recoveryNote: String?
}

/// An approval that is still waiting (e.g. across a restart), bound to its id.
public struct PendingApproval: Codable, Sendable, Equatable {
    public let approvalId: String?
    public let actions: [PendingAction]
    /// Where the card's turn began ("mac" or "slack").
    public var origin: String? = nil
}

/// Work a restart interrupted: a build (retry or dismiss) or a vault job (dismiss).
public struct RecoveryItem: Codable, Sendable, Equatable, Identifiable {
    public let kind: String  // "build" | "vault_task"
    public let id: String
    public let title: String
    public let message: String
    public let when: String
    public let scratchKept: Bool
    public let actions: [String]

    public init(kind: String, id: String, title: String, message: String, when: String = "", scratchKept: Bool = false,
                actions: [String] = ["dismiss"]) {
        self.kind = kind
        self.id = id
        self.title = title
        self.message = message
        self.when = when
        self.scratchKept = scratchKept
        self.actions = actions
    }
}

/// How Aura sounds. Owned by the server (voice.json); spoken replies are off by default.
public struct VoiceProfile: Codable, Sendable, Equatable {
    public var voice: String?
    public var speed: Double
    public var pitch: Double
    public var volume: Double
    public var speak: Bool

    public init(voice: String? = nil, speed: Double = 1, pitch: Double = 1, volume: Double = 0.9, speak: Bool = false) {
        self.voice = voice
        self.speed = speed
        self.pitch = pitch
        self.volume = volume
        self.speak = speak
    }
}

/// A partial voice change from the settings UI: nil fields stay as they are.
public struct VoicePatch: Codable, Sendable, Equatable {
    public var voice: String?
    public var speed: Double?
    public var pitch: Double?
    public var volume: Double?
    public var speak: Bool?

    public init(voice: String? = nil, speed: Double? = nil, pitch: Double? = nil, volume: Double? = nil, speak: Bool? = nil) {
        self.voice = voice
        self.speed = speed
        self.pitch = pitch
        self.volume = volume
        self.speak = speak
    }
}

/// A voice the app can speak with, reported to the server so the agent only names real voices.
public struct VoiceInfo: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let language: String
    public let quality: String  // "premium" | "enhanced" | "default"
    public let gender: String
    public let novelty: Bool

    public init(id: String, name: String, language: String, quality: String, gender: String, novelty: Bool) {
        self.id = id
        self.name = name
        self.language = language
        self.quality = quality
        self.gender = gender
        self.novelty = novelty
    }
}

/// Aura's Expression layer (persona.json), as the settings panel shows it.
public struct PersonaInfo: Codable, Sendable, Equatable {
    public var name: String
    public var tone: String
    public var brevity: String
    public var formality: String
    public var backstory: String
    public var style: [String]
    public var constraints: [String]
    public var history: Int
    public var error: String?

    public static let initial = PersonaInfo(
        name: "Aura", tone: "", brevity: "", formality: "", backstory: "", style: [], constraints: [], history: 0)
}

public struct AppSettings: Codable, Sendable, Equatable {
    public let voice: VoiceProfile
    public let persona: PersonaInfo
    public let voicesRegistered: Int
    public let modelRouting: [String: ModelChoice]?
    public let startupModelProblem: String?
}

/// A model is selected by job role, not globally. Provider credentials stay on the server.
public struct ModelChoice: Codable, Sendable, Equatable {
    public var provider: String  // "ollama" | "anthropic"
    public var model: String

    public init(provider: String, model: String) {
        self.provider = provider
        self.model = model
    }
}

public struct ModelCatalog: Codable, Sendable, Equatable {
    public let roles: [String: ModelChoice]
    public let roleLabels: [String: String]
    public let ollamaModels: [String]
    public let anthropicModels: [String]
    public let anthropicKeySet: Bool
    /// Claude Code or Codex: the persisted account-backed coding harness, never a cloud chat model.
    public let codingEngine: String
    public let sleepMode: SleepMode
    public let startupModelProblem: String?
}

/// User-controlled local Ollama residency. It never applies to cloud models.
public struct SleepMode: Codable, Sendable, Equatable {
    public var enabled: Bool
    public var model: String

    public init(enabled: Bool = false, model: String) {
        self.enabled = enabled
        self.model = model
    }
}

public struct ModelRoutingUpdate: Encodable, Sendable {
    public let roles: [String: ModelChoice]

    public init(roles: [String: ModelChoice]) { self.roles = roles }
}

public struct Health: Codable, Sendable, Equatable {
    public let status: String
    public let model: String
    public let modelLoaded: Bool?
    public let searchMode: String
    public let vault: String
    public let busy: Bool
    public let awaitingApproval: Bool
    public let runtimeHealth: RuntimeHealth?
    public let governanceHealth: GovernanceHealth?
}

/// Observable service state only. This is deliberately not an epistemic or philosophical claim.
public struct RuntimeHealth: Codable, Sendable, Equatable {
    public let state: String  // loaded | idle | unknown | unavailable
    public let provider: String
    public let model: String
    public let detail: String
}

public struct GovernanceHealth: Codable, Sendable, Equatable {
    public let state: String  // healthy | attention
    public let agent: AgentGovernanceHealth
    public let optionalConnectionAttention: [String]
}

public struct AgentGovernanceHealth: Codable, Sendable, Equatable {
    public let state: String
    public let agents: [String: Int]
    public let detail: String
}

/// Line-oriented SSE parser. The server sends exactly one `event:` line and one `data:` line
/// per event, so an event is complete at its data line; blank lines are not needed (some line
/// readers drop them).
/// Parses a stream one line at a time (AuraClient's SSE responses).
public protocol LineParser: Sendable {
    associatedtype Output: Sendable
    mutating func feed(_ line: String) -> Output?
}

public struct SSEParser: LineParser {
    private var kind = "message"
    /// The correlation id of the turn being streamed (from its `thinking` event).
    public private(set) var turnID: String?
    /// The id binding the last approval card to this turn, its tools and their arguments.
    public private(set) var approvalID: String?

    public init() {}

    /// Feed one line, without its line terminator. Returns an event when one is complete.
    public mutating func feed(_ line: String) -> AuraEvent? {
        if line.hasPrefix("event:") {
            kind = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
            return nil
        }
        guard line.hasPrefix("data:") else { return nil }  // blank lines, comments
        defer { kind = "message" }
        let data = Data(line.dropFirst(5).utf8)
        struct IDs: Decodable { let turn_id: String?; let approval_id: String? }
        if kind == "thinking" { turnID = (try? JSONDecoder().decode(IDs.self, from: data))?.turn_id }
        if kind == "approval" { approvalID = (try? JSONDecoder().decode(IDs.self, from: data))?.approval_id }
        return Self.decode(kind, data)
    }

    static func decode(_ kind: String, _ data: Data) -> AuraEvent? {
        struct TextBody: Decodable { let text: String }
        struct ApprovalBody: Decodable { let actions: [PendingAction] }
        struct ErrorBody: Decodable { let message: String }
        let json = JSONDecoder()
        switch kind {
        case "model": return (try? json.decode(ModelChoice.self, from: data)).map { .model($0) }
        case "performance": return (try? json.decode(LocalModelPerformance.self, from: data)).map { .performance($0) }
        case "thinking": return .thinking
        case "waking": return .waking
        case "done": return .done
        case "status": return (try? json.decode(TextBody.self, from: data)).map { .status($0.text) }
        case "text": return (try? json.decode(TextBody.self, from: data)).map { .text($0.text) }
        case "approval": return (try? json.decode(ApprovalBody.self, from: data)).map { .approval($0.actions) }
        case "error": return (try? json.decode(ErrorBody.self, from: data)).map { .error($0.message) }
        case "stopped":
            struct StoppedBody: Decodable { let partial: Bool? }
            return .stopped(partial: (try? json.decode(StoppedBody.self, from: data))?.partial ?? false)
        case "voice": return (try? json.decode(VoiceProfile.self, from: data)).map { .voice($0) }
        case "task":
            struct TaskBody: Decodable { let id: String; let skill: String; let kind: String? }
            return (try? json.decode(TaskBody.self, from: data)).map {
                $0.kind == "build" ? .build(id: $0.id, skill: $0.skill) : .task(id: $0.id, skill: $0.skill)
            }
        case "persona":
            struct PersonaBody: Decodable { let name: String }
            return (try? json.decode(PersonaBody.self, from: data)).map { .persona(name: $0.name) }
        default: return nil  // unknown event types are ignored (forward compatibility)
        }
    }
}

/// One frame of the mirror feed (GET /v1/turns/live): every turn's events as the server showed
/// them, already masked, so the panel can draw a turn it didn't start (a Slack command).
public struct MirrorFrame: Sendable, Equatable {
    public let kind: String  // "ready", "turn", "reset", "done", or any turn event's kind
    public let seq: Int
    public let turnID: String?
    public let origin: String?  // "mac" | "slack"
    /// The `turn` frame: what the user said (nil when the turn answers a card), and where.
    public let said: String?
    public let place: String?
    /// The turn event this frame carries, if it is one.
    public let event: AuraEvent?
    /// The `approval` frame: the id binding the card, so an answer here can't approve another.
    public let approvalID: String?
}

/// Parses the mirror feed: the same event lines as a turn's stream, each with `seq`, `turn_id`
/// and `origin` in its data.
public struct MirrorParser: LineParser {
    private var kind = "message"

    public init() {}

    public mutating func feed(_ line: String) -> MirrorFrame? {
        if line.hasPrefix("event:") {
            kind = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
            return nil
        }
        guard line.hasPrefix("data:") else { return nil }  // blank lines, keepalive comments
        defer { kind = "message" }
        let data = Data(line.dropFirst(5).utf8)
        struct Envelope: Decodable {
            let seq: Int?
            let turn_id: String?
            let origin: String?
            let text: String?
            let place: String?
            let approval_id: String?
        }
        guard let envelope = try? JSONDecoder().decode(Envelope.self, from: data), let seq = envelope.seq else { return nil }
        let isTurn = kind == "turn"
        return MirrorFrame(kind: kind, seq: seq, turnID: envelope.turn_id, origin: envelope.origin,
                           said: isTurn ? envelope.text : nil, place: isTurn ? envelope.place : nil,
                           event: isTurn ? nil : SSEParser.decode(kind, data), approvalID: envelope.approval_id)
    }
}
