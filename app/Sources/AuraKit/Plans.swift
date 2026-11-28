import Foundation

/// A plan Aura asks to start: make_plan's card (docs/orchestration.md T5). The server sends the plan
/// as it checked it, with the real step IDs, so the app never re-derives them.
public struct PlanProposal: Codable, Sendable, Equatable {
    public struct Step: Codable, Sendable, Equatable, Identifiable {
        public let id: String
        public let does: String
        public let tool: String
        public let after: [String]
        public let doneWhen: String

        public init(id: String, does: String, tool: String, after: [String] = [], doneWhen: String) {
            self.id = id
            self.does = does
            self.tool = tool
            self.after = after
            self.doneWhen = doneWhen
        }

        private enum Keys: String, CodingKey { case id, does, tool, after, doneWhen, done_when }

        /// Both spellings: the live event stream decodes plainly ("done_when"), while the card shown
        /// again after a restart goes through the snake_case-converting decoder ("doneWhen").
        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: Keys.self)
            id = try c.decode(String.self, forKey: .id)
            does = try c.decode(String.self, forKey: .does)
            tool = try c.decode(String.self, forKey: .tool)
            after = try c.decodeIfPresent([String].self, forKey: .after) ?? []
            doneWhen = try c.decodeIfPresent(String.self, forKey: .doneWhen) ?? c.decode(String.self, forKey: .done_when)
        }

        public func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: Keys.self)
            try c.encode(id, forKey: .id)
            try c.encode(does, forKey: .does)
            try c.encode(tool, forKey: .tool)
            try c.encode(after, forKey: .after)
            try c.encode(doneWhen, forKey: .done_when)
        }
    }

    /// The running plan this one replaces, if any.
    public struct Replaced: Codable, Sendable, Equatable {
        public let id: String
        public let goal: String

        public init(id: String, goal: String) {
            self.id = id
            self.goal = goal
        }
    }

    public let goal: String
    public let steps: [Step]
    public let replaces: Replaced?

    public init(goal: String, steps: [Step], replaces: Replaced? = nil) {
        self.goal = goal
        self.steps = steps
        self.replaces = replaces
    }
}

/// The running plan, for the progress line under the conversation.
public struct ActivePlan: Codable, Sendable, Equatable {
    public struct Step: Codable, Sendable, Equatable, Identifiable {
        public let id: String
        public let does: String
        public let status: String  // pending, running, waiting, done, failed, blocked, skipped

        public init(id: String, does: String, status: String) {
            self.id = id
            self.does = does
            self.status = status
        }
    }

    public let id: String
    public let goal: String
    public let done: Int
    public let total: Int
    public let steps: [Step]

    public init(id: String, goal: String, done: Int, total: Int, steps: [Step]) {
        self.id = id
        self.goal = goal
        self.done = done
        self.total = total
        self.steps = steps
    }

    /// The step being worked on or waiting: the first that isn't finished.
    public var current: Step? { steps.first { ["running", "waiting", "pending"].contains($0.status) } }
}

/// GET /v1/plans/active.
public struct ActivePlanReply: Codable, Sendable, Equatable {
    public let plan: ActivePlan?
}
