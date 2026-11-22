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
    case done
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
        default: return nil  // unknown event types are ignored (forward compatibility)
        }
    }
}
