import Foundation

// MARK: - the Developer (Sprint 6): models mirroring /v1/developer and /v1/builds

/// Claude Code or Codex, and whether it can run right now.
public struct EngineInfo: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String  // "claude" | "codex"
    public let label: String
    public let vendor: String
    public let capsCost: Bool  // a hard dollar cap, enforced by the engine itself
    public let problem: String?  // why it can't run now (nil: ready)

    public init(name: String, label: String, vendor: String, capsCost: Bool, problem: String? = nil) {
        self.name = name
        self.label = label
        self.vendor = vendor
        self.capsCost = capsCost
        self.problem = problem
    }

    /// The two engines, for cards drawn before the Workshop has loaded the real state.
    public static let known = [
        EngineInfo(name: "claude", label: "Claude Code", vendor: "Anthropic", capsCost: false),
        EngineInfo(name: "codex", label: "Codex", vendor: "OpenAI", capsCost: false),
    ]

    /// "Claude Code" / "claude-code" / "OpenAI Codex" -> "claude" / "codex" (as the server reads them).
    public static func engineName(_ raw: String?) -> String {
        let v = (raw ?? "claude").lowercased()
        return v.contains("codex") ? "codex" : v.contains("claude") ? "claude" : v
    }

    public static func named(_ name: String, in engines: [EngineInfo] = known) -> EngineInfo? {
        engines.first { $0.name == name } ?? known.first { $0.name == name }
    }

    /// The user's own plan it runs on: Claude Code on the Claude plan, Codex on the ChatGPT plan.
    public var plan: String { Self.plan(name) }

    public static func plan(_ name: String) -> String {
        switch name {
        case "claude": "your Claude plan"
        case "codex": "your ChatGPT plan"
        default: "its own plan"
        }
    }

    /// How the build is paid for, in the user's terms.
    public var costNote: String {
        capsCost ? "Billed per use; \(label) itself stops at the cap." : "\(label) counts against \(plan)'s usage."
    }
}

/// One audited round of a build.
public struct BuildRound: Codable, Sendable, Equatable, Hashable {
    public let round: Int
    public let task: String?
    public let passed: Bool
    public let findings: [String]
    public let risk: String?

    public init(round: Int, task: String? = nil, passed: Bool, findings: [String] = [], risk: String? = nil) {
        self.round = round
        self.task = task
        self.passed = passed
        self.findings = findings
        self.risk = risk
    }
}

/// A line of the build's live feed ("Writing skill/skill.py").
public struct FeedLine: Codable, Sendable, Equatable, Hashable {
    public let t: String
    public let text: String

    public init(t: String, text: String) {
        self.t = t
        self.text = text
    }

    public var time: Date? { ISO8601DateFormatter().date(from: t) }
}

public struct BuildInfo: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let skill: String
    public let engine: String
    public let improve: Bool
    public let specification: String
    /// queued → developing → auditing (→ repairing → developing …) → ready → deployed | rejected;
    /// or failed | stopped.
    public let stage: String
    public let round: Int
    public let maxRounds: Int
    public let costUsd: Double
    public let budgetUsd: Double  // 0: the engine runs on the user's plan
    public let message: String
    public let taskId: String?  // the passing round's Audit Card
    public let history: [BuildRound]
    public let feed: [FeedLine]
    public let created: String
    public let finished: String?

    public init(id: String, skill: String, engine: String = "claude", improve: Bool = false, specification: String = "",
                stage: String, round: Int = 0, maxRounds: Int = 3, costUsd: Double = 0, budgetUsd: Double = 2,
                message: String = "", taskId: String? = nil, history: [BuildRound] = [], feed: [FeedLine] = [],
                created: String = "", finished: String? = nil) {
        self.id = id
        self.skill = skill
        self.engine = engine
        self.improve = improve
        self.specification = specification
        self.stage = stage
        self.round = round
        self.maxRounds = maxRounds
        self.costUsd = costUsd
        self.budgetUsd = budgetUsd
        self.message = message
        self.taskId = taskId
        self.history = history
        self.feed = feed
        self.created = created
        self.finished = finished
    }

    public static let runningStages: Set<String> = ["queued", "developing", "auditing", "repairing"]
    public var isRunning: Bool { Self.runningStages.contains(stage) }
    public var awaitsReview: Bool { stage == "ready" }

    public var stageLabel: String {
        switch stage {
        case "queued": "Starting…"
        case "developing": "Writing the skill (round \(round) of \(maxRounds))…"
        case "auditing": "The Security Governor is auditing round \(round)…"
        case "repairing": "Round \(round) didn't pass; repairing…"
        case "ready": "Ready for your review"
        case "deployed": "Deployed"
        case "rejected": "Rejected"
        case "stopped": "Stopped"
        case "interrupted": "Interrupted by a restart"
        default: "Couldn't produce a passing skill"
        }
    }

    /// "$0.42 of $2.00" for a capped engine; otherwise the plan it ran on ("your Claude plan").
    public var costLabel: String {
        budgetUsd > 0 ? String(format: "$%.2f of $%.2f", costUsd, budgetUsd) : EngineInfo.plan(engine)
    }
}

public struct DeveloperInfo: Codable, Sendable, Equatable {
    public let engines: [EngineInfo]
    public let builds: [BuildInfo]
    public let budgetUsd: Double
    public let repairRounds: Int
    public let timeoutMinutes: Int
    /// Builds and vault jobs a restart interrupted (absent from servers before the recovery layer).
    public let recovery: [RecoveryItem]?

    public init(engines: [EngineInfo] = EngineInfo.known, builds: [BuildInfo] = [], budgetUsd: Double = 2,
                repairRounds: Int = 2, timeoutMinutes: Int = 10, recovery: [RecoveryItem]? = nil) {
        self.engines = engines
        self.builds = builds
        self.budgetUsd = budgetUsd
        self.repairRounds = repairRounds
        self.timeoutMinutes = timeoutMinutes
        self.recovery = recovery
    }

    /// "$2.00 total, 10 minutes per round, up to 2 repair rounds" (the chat card's limits).
    public func limits(for engine: EngineInfo) -> String {
        let cost = engine.capsCost ? String(format: "$%.2f total", budgetUsd) : "no dollar cap"
        return "\(cost), \(timeoutMinutes) minutes per round, up to \(repairRounds) repair rounds"
    }
}

/// Sent when the user starts a build from the Workshop (their click is the approval).
public struct BuildRequest: Codable, Sendable, Equatable {
    public let skill: String
    public let specification: String
    public let engine: String
    public let improve: Bool

    public init(skill: String, specification: String, engine: String, improve: Bool) {
        self.skill = skill
        self.specification = specification
        self.engine = engine
        self.improve = improve
    }
}

/// A build the menu bar mentions: running, or ready for the user's review.
public struct BuildAlert: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let skill: String
    public let stage: String
    public let message: String

    public init(id: String, skill: String, stage: String, message: String = "") {
        self.id = id
        self.skill = skill
        self.stage = stage
        self.message = message
    }

    public var menuTitle: String { "\(skill): ready for your review…" }
}

/// The Technical Specification: the six sections the server requires, and a starting template.
public enum Specification {
    public static let sections = ["Goal", "Inputs", "Output", "Steps", "Acceptance tests", "Constraints"]
    public static let minimumLength = 150
    public static let maximumLength = 12_000

    public static let template = sections.map { "## \($0)\n" }.joined(separator: "\n")

    /// What's missing before the server would accept it (empty: complete), in the same terms.
    public static func problems(_ spec: String) -> [String] {
        var out: [String] = []
        let length = spec.trimmingCharacters(in: .whitespacesAndNewlines).count
        if length < minimumLength || length > maximumLength {
            out.append("it must be \(minimumLength)–\(maximumLength) characters (now \(length))")
        }
        let lines = spec.components(separatedBy: .newlines).map {
            $0.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: #"^#+\s*"#, with: "", options: .regularExpression)
                .lowercased()
        }
        let missing = sections.filter { section in
            !lines.contains { line in
                line.hasPrefix(section.lowercased())
                    && !(line.dropFirst(section.count).first.map { $0.isLetter || $0.isNumber || $0 == "_" } ?? false)
            }
        }
        if !missing.isEmpty { out.append("missing sections: " + missing.joined(separator: ", ")) }
        return out
    }

    /// The same rule as the server's: lowercase letters, digits and underscores, 2–40 characters.
    public static func validName(_ name: String) -> Bool {
        name.range(of: #"^[a-z][a-z0-9_]{1,39}$"#, options: .regularExpression) != nil
    }
}

// MARK: - keys (the macOS Keychain, via the server)

/// A key Aura can hold. The value never comes back: only whether it's set, and a masked hint.
public struct SecretInfo: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let label: String
    public let set: Bool
    public let hint: String?  // "sk-ant-oat…WXYZ"
    /// No longer used: shown only while still stored, so it can be removed (never replaced).
    public let retired: Bool?

    public init(name: String, label: String, set: Bool, hint: String? = nil, retired: Bool? = nil) {
        self.name = name
        self.label = label
        self.set = set
        self.hint = hint
        self.retired = retired
    }

    public var isRetired: Bool { retired ?? false }

    /// Where to get one.
    public var source: URL? {
        switch name {
        case "tavily_api_key": URL(string: "https://app.tavily.com/home")
        default: nil
        }
    }

    public var purpose: String {
        switch name {
        case "claude_oauth_token":
            "Claude Code works on your Claude plan, in its isolated container only. To get the token, run "
                + "\"claude setup-token\" in Terminal, sign in, and paste the token it prints."
        case "anthropic_api_key": "Not used any more: Claude Code uses your Claude plan. You can remove it."
        case "tavily_api_key": "Web search (otherwise DuckDuckGo)."
        default: ""
        }
    }

    /// What to paste, for the field's placeholder.
    public var placeholder: String { name == "claude_oauth_token" ? "Paste the token (sk-ant-oat01-…)" : "Paste the key" }
}
