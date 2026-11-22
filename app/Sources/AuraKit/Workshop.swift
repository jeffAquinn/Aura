import Foundation
import Observation

// MARK: - models (mirroring the server's Workshop API)

public struct SkillInfo: Codable, Sendable, Equatable, Identifiable {
    public struct Permissions: Codable, Sendable, Equatable {
        public let network: Bool?
        public let vault: String?
    }

    public var id: String { name }
    public let name: String
    public let status: String  // approved | changed | unapproved | invalid
    public let enabled: Bool
    public let error: String?
    public let version: String
    public let purpose: String
    public let permissions: Permissions?
    public let dependencies: [String]?
}

public struct InboxItem: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let version: String
    public let purpose: String
    public let error: String?
}

public struct SkillsOverview: Codable, Sendable, Equatable {
    public let installed: [SkillInfo]
    public let inbox: [InboxItem]
}

public struct Finding: Codable, Sendable, Equatable, Hashable {
    public let check: String
    public let severity: String  // pass | info | yellow | red | reject
    public let message: String
    public let detail: String

    public init(check: String, severity: String, message: String, detail: String = "") {
        self.check = check
        self.severity = severity
        self.message = message
        self.detail = detail
    }
}

public struct TestResult: Codable, Sendable, Equatable, Hashable {
    public let name: String
    public let status: String
    public let detail: String

    public init(name: String, status: String, detail: String = "") {
        self.name = name
        self.status = status
        self.detail = detail
    }
}

public struct SuiteResult: Codable, Sendable, Equatable {
    public let passed: Int?
    public let failed: Int?
    public let summary: String?

    public init(passed: Int?, failed: Int?, summary: String?) {
        self.passed = passed
        self.failed = failed
        self.summary = summary
    }
}

public struct AuditReportInfo: Codable, Sendable, Equatable {
    public let skill: String
    public let version: String
    public let purpose: String
    public let findings: [Finding]
    public let tests: [TestResult]
    public let suite: SuiteResult
    public let image: String
    public let risk: String  // green | yellow | red
    public let blocked: Bool

    public init(skill: String, version: String, purpose: String, findings: [Finding], tests: [TestResult],
                suite: SuiteResult, image: String = "", risk: String, blocked: Bool) {
        self.skill = skill
        self.version = version
        self.purpose = purpose
        self.findings = findings
        self.tests = tests
        self.suite = suite
        self.image = image
        self.risk = risk
        self.blocked = blocked
    }
}

public struct DiffRow: Codable, Sendable, Equatable, Hashable {
    public let left: String?
    public let right: String?
    public let kind: String  // same | added | removed | changed

    public init(left: String?, right: String?, kind: String) {
        self.left = left
        self.right = right
        self.kind = kind
    }
}

public struct FileDiff: Codable, Sendable, Equatable, Identifiable {
    public var id: String { file }
    public let file: String
    public let status: String  // added | modified | removed | binary
    public let rows: [DiffRow]

    public init(file: String, status: String, rows: [DiffRow]) {
        self.file = file
        self.status = status
        self.rows = rows
    }
}

public struct TaskInfo: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let skill: String
    public let stage: String  // staging | auditing | awaiting | deployed | rejected | failed
    public let message: String
    public let created: String
    public let report: AuditReportInfo?
    public let diff: [FileDiff]?

    public init(id: String, skill: String, stage: String, message: String, created: String = "",
                report: AuditReportInfo? = nil, diff: [FileDiff]? = nil) {
        self.id = id
        self.skill = skill
        self.stage = stage
        self.message = message
        self.created = created
        self.report = report
        self.diff = diff
    }

    public var isRunning: Bool { stage == "staging" || stage == "auditing" }
    public var awaitsDecision: Bool { stage == "awaiting" }
}

public struct SystemStatus: Codable, Sendable, Equatable {
    public struct Skills: Codable, Sendable, Equatable {
        public let enabled: Int
        public let installed: Int
    }

    public let model: String
    public let modelLoaded: Bool?
    public let serverMemoryMb: Int
    public let docker: Bool
    public let skills: Skills
    public let tasksRunning: Int
}

// MARK: - state

/// The Workshop: skills, audits awaiting the user, and the Approve & Deploy decision.
@MainActor
@Observable
public final class WorkshopStore {
    public private(set) var skills: [SkillInfo] = []
    public private(set) var inbox: [InboxItem] = []
    public private(set) var tasks: [TaskInfo] = []
    public private(set) var status: SystemStatus?
    public var selectedTaskID: String? {
        didSet { if selectedTaskID != oldValue { Task { await loadSelected() } } }
    }
    public private(set) var selected: TaskInfo?
    public private(set) var lastError: String?

    @ObservationIgnored private let transport: () -> (any AuraTransport)?
    @ObservationIgnored private var polling: Task<Void, Never>?

    public init(transport: @escaping () -> (any AuraTransport)?) {
        self.transport = transport
    }

    /// For previews and snapshot tests.
    public init(skills: [SkillInfo] = [], inbox: [InboxItem] = [], tasks: [TaskInfo] = [], selected: TaskInfo? = nil,
                status: SystemStatus? = nil) {
        transport = { nil }
        self.skills = skills
        self.inbox = inbox
        self.tasks = tasks
        self.selected = selected
        selectedTaskID = selected?.id
        self.status = status
    }

    public func refresh() async {
        guard let t = transport() else { return }
        do {
            let overview = try await t.skills()
            skills = overview.installed
            inbox = overview.inbox
            tasks = try await t.tasks()
            status = try? await t.status()
            lastError = nil
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
        await loadSelected()
        if tasks.contains(where: \.isRunning) { startPolling() }
    }

    public func propose(_ skill: String) async {
        guard let t = transport() else { return }
        do {
            let task = try await t.propose(skill)
            await refresh()
            selectedTaskID = task.id
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
    }

    /// The Audit Card decision. Deploy only ever follows an explicit approve.
    public func decide(approve: Bool) async {
        guard let t = transport(), let id = selected?.id else { return }
        do {
            _ = try await t.decide(id, approve: approve)
            await refresh()
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
    }

    public func setEnabled(_ skill: String, _ enabled: Bool) async {
        guard let t = transport() else { return }
        do {
            try await t.toggleSkill(skill, enabled: enabled)
            await refresh()
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
    }

    private func loadSelected() async {
        guard let id = selectedTaskID, let t = transport() else { return }
        selected = try? await t.task(id)
    }

    private func startPolling() {
        guard polling == nil else { return }
        polling = Task { [weak self] in
            while let self, !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                await self.refresh()
                if !self.tasks.contains(where: \.isRunning) { break }
            }
            self?.polling = nil
        }
    }
}
