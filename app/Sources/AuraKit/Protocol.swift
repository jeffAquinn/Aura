import Foundation

/// An action Aura wants to take that needs the user's approval.
public struct PendingAction: Codable, Sendable, Equatable {
    public let tool: String
    public let description: String

    public init(tool: String, description: String) {
        self.tool = tool
        self.description = description
    }
}

/// One Server-Sent Event from the Aura Server (see docs/sprint-3-design.md §3).
public enum AuraEvent: Sendable, Equatable {
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
    case done
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
}

public struct Health: Codable, Sendable, Equatable {
    public let status: String
    public let model: String
    public let modelLoaded: Bool?
    public let searchMode: String
    public let vault: String
    public let busy: Bool
    public let awaitingApproval: Bool
}

/// Line-oriented SSE parser. The server sends exactly one `event:` line and one `data:` line
/// per event, so an event is complete at its data line; blank lines are not needed (some line
/// readers drop them).
public struct SSEParser: Sendable {
    private var kind = "message"

    public init() {}

    /// Feed one line, without its line terminator. Returns an event when one is complete.
    public mutating func feed(_ line: String) -> AuraEvent? {
        if line.hasPrefix("event:") {
            kind = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
            return nil
        }
        guard line.hasPrefix("data:") else { return nil }  // blank lines, comments
        defer { kind = "message" }
        return Self.decode(kind, Data(line.dropFirst(5).utf8))
    }

    static func decode(_ kind: String, _ data: Data) -> AuraEvent? {
        struct TextBody: Decodable { let text: String }
        struct ApprovalBody: Decodable { let actions: [PendingAction] }
        struct ErrorBody: Decodable { let message: String }
        let json = JSONDecoder()
        switch kind {
        case "thinking": return .thinking
        case "waking": return .waking
        case "done": return .done
        case "status": return (try? json.decode(TextBody.self, from: data)).map { .status($0.text) }
        case "text": return (try? json.decode(TextBody.self, from: data)).map { .text($0.text) }
        case "approval": return (try? json.decode(ApprovalBody.self, from: data)).map { .approval($0.actions) }
        case "error": return (try? json.decode(ErrorBody.self, from: data)).map { .error($0.message) }
        case "voice": return (try? json.decode(VoiceProfile.self, from: data)).map { .voice($0) }
        case "task":
            struct TaskBody: Decodable { let id: String; let skill: String }
            return (try? json.decode(TaskBody.self, from: data)).map { .task(id: $0.id, skill: $0.skill) }
        case "persona":
            struct PersonaBody: Decodable { let name: String }
            return (try? json.decode(PersonaBody.self, from: data)).map { .persona(name: $0.name) }
        default: return nil  // unknown event types are ignored (forward compatibility)
        }
    }
}
