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
    public let enabled: Bool  // the user's switch
    public let loaded: Bool?  // available to Aura: approved, switched on, not flagged
    public let flag: SkillFlag?  // set by the Continuous Audit
    public let error: String?
    public let version: String
    public let purpose: String
    public let permissions: Permissions?
    public let dependencies: [String]?
}

/// The Continuous Audit found a red problem in an installed skill; it is unloaded until cleared.
public struct SkillFlag: Codable, Sendable, Equatable {
    public let since: String
    public let kind: String  // quick | deep
    public let findings: [Finding]
    public let acknowledged: Bool
}

/// "Warning: The pdf_text skill has been flagged with a new vulnerability. Would you like to uninstall it?"
public struct SkillAlert: Codable, Sendable, Equatable, Identifiable {
    public var id: String { skill }
    public let skill: String
    public let message: String
    public let findings: [Finding]
    public let since: String
    public let acknowledged: Bool

    public init(skill: String, message: String, findings: [Finding], since: String = "", acknowledged: Bool = false) {
        self.skill = skill
        self.message = message
        self.findings = findings
        self.since = since
        self.acknowledged = acknowledged
    }

    /// The alerts that still need the user: those they haven't chosen to keep.
    public static func pending(_ alerts: [SkillAlert]) -> [SkillAlert] { alerts.filter { !$0.acknowledged } }

    public var menuTitle: String { "⚠︎ \(skill): new security problem. Review…" }
}

/// When installed skills were last re-checked (quick: at startup; deep: daily).
public struct AuditClock: Codable, Sendable, Equatable {
    public let lastQuick: String?
    public let lastDeep: String?
    public let lastError: String?
    public let running: Bool

    public init(lastQuick: String? = nil, lastDeep: String? = nil, lastError: String? = nil, running: Bool = false) {
        self.lastQuick = lastQuick
        self.lastDeep = lastDeep
        self.lastError = lastError
        self.running = running
    }

    /// The most recent successful check of either kind.
    public var lastChecked: Date? {
        let f = ISO8601DateFormatter()
        return [lastQuick, lastDeep].compactMap { $0.flatMap(f.date(from:)) }.max()
    }
}

public struct AlertsInfo: Codable, Sendable, Equatable {
    public let alerts: [SkillAlert]
    public let continuousAudit: AuditClock
    public let builds: [BuildAlert]?  // the Developer's builds: running, or ready for review

    public init(alerts: [SkillAlert], continuousAudit: AuditClock, builds: [BuildAlert]? = nil) {
        self.alerts = alerts
        self.continuousAudit = continuousAudit
        self.builds = builds
    }
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

/// A Workstation agent is a recognized, non-executable manifest.  It is not a connected bot.
public struct AgentInfo: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let version: String
    public let purpose: String
    public let origin: String
    public let error: String?
    public let state: String?
    public let reviewBy: String?

    public init(name: String, version: String, purpose: String, origin: String, error: String?,
                state: String? = nil, reviewBy: String? = nil) {
        self.name = name
        self.version = version
        self.purpose = purpose
        self.origin = origin
        self.error = error
        self.state = state
        self.reviewBy = reviewBy
    }
}

public struct AgentsOverview: Codable, Sendable, Equatable {
    public let installed: [AgentInfo]
    public let inbox: [AgentInfo]
    public let retired: [AgentInfo]?

    public init(installed: [AgentInfo], inbox: [AgentInfo], retired: [AgentInfo]? = nil) {
        self.installed = installed
        self.inbox = inbox
        self.retired = retired
    }
}

/// The user-visible metadata accepted for a local agent candidate or quarantined import.
/// It deliberately has no code, path, connector, credential, or executable-content field.
public struct AgentCandidateRequest: Codable, Sendable, Equatable {
    public let name: String
    public let purpose: String
    public let provenance: String
    public let intentDelta: String
    public let memory: String

    public init(name: String, purpose: String, provenance: String,
                intentDelta: String = "Unmeasured: candidate has not been evaluated.",
                memory: String = "No memory imported.") {
        self.name = name
        self.purpose = purpose
        self.provenance = provenance
        self.intentDelta = intentDelta
        self.memory = memory
    }
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

/// Who wrote a skill the Developer built: shown as "Built by" on its Audit Card.
public struct TaskOrigin: Codable, Sendable, Equatable {
    public let build: String
    public let engine: String  // the label, e.g. "Claude Code"
    public let round: Int
    public let costUsd: Double
    public let specification: String
    public let improve: Bool

    public init(build: String, engine: String, round: Int, costUsd: Double, specification: String, improve: Bool = false) {
        self.build = build
        self.engine = engine
        self.round = round
        self.costUsd = costUsd
        self.specification = specification
        self.improve = improve
    }
}

public struct TaskInfo: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let skill: String
    public let kind: String?
    public let stage: String  // staging | auditing | awaiting | deployed | rejected | failed | discarded
    public let message: String
    public let created: String
    public let report: AuditReportInfo?
    public let diff: [FileDiff]?
    public let origin: TaskOrigin?  // set when the Developer built it
    /// The Governor's current audit phase (not a model-generated estimate).
    public let progress: Int?
    public let phase: String?
    /// A concrete, bounded error when staging or the audit infrastructure failed.
    public let error: String?

    public init(id: String, skill: String, kind: String? = nil, stage: String, message: String, created: String = "",
                report: AuditReportInfo? = nil, diff: [FileDiff]? = nil, origin: TaskOrigin? = nil,
                progress: Int? = nil, phase: String? = nil, error: String? = nil) {
        self.id = id
        self.skill = skill
        self.kind = kind
        self.stage = stage
        self.message = message
        self.created = created
        self.report = report
        self.diff = diff
        self.origin = origin
        self.progress = progress
        self.phase = phase
        self.error = error
    }

    public var isRunning: Bool { stage == "staging" || stage == "auditing" }
    public var awaitsDecision: Bool { stage == "awaiting" }
    /// A build's failed round, wiped by the Governor (Gemma: nothing that failed is presented).
    public var isDiscarded: Bool { stage == "discarded" }
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
    public let alerts: [SkillAlert]?
    public let continuousAudit: AuditClock?
}

// MARK: - state

/// The Workshop: skills, audits awaiting the user, and the Approve & Deploy decision.
@MainActor
@Observable
public final class WorkshopStore {
    public private(set) var skills: [SkillInfo] = []
    public private(set) var inbox: [InboxItem] = []
    public private(set) var agents: [AgentInfo] = []
    public private(set) var agentInbox: [AgentInfo] = []
    public private(set) var retiredAgents: [AgentInfo] = []
    public private(set) var agentError: String?
    public private(set) var tasks: [TaskInfo] = []
    public private(set) var status: SystemStatus?
    public private(set) var developer: DeveloperInfo?
    public var selectedTaskID: String? {
        didSet { if selectedTaskID != oldValue { Task { await loadSelected() } } }
    }
    public private(set) var selected: TaskInfo?
    /// The build shown in the detail pane (instead of an Audit Card).
    public var selectedBuildID: String?
    public private(set) var lastError: String?
    /// Why the last build couldn't start (shown in the New Skill sheet).
    public private(set) var buildError: String?

    public var builds: [BuildInfo] { developer?.builds ?? [] }
    /// Work a restart interrupted: builds (Retry or Dismiss) and vault jobs (Dismiss).
    public var recovery: [RecoveryItem] { developer?.recovery ?? [] }
    public var engines: [EngineInfo] { developer?.engines ?? EngineInfo.known }
    public var selectedBuild: BuildInfo? { selectedBuildID.flatMap { id in builds.first { $0.id == id } } }
    /// The Audits list is an action queue, not history. Completed decisions are in the audit log;
    /// only work that is running, needs a decision, or needs a failure diagnosis stays visible.
    public var visibleTasks: [TaskInfo] {
        tasks.filter { !$0.isDiscarded && ($0.isRunning || $0.awaitsDecision || $0.stage == "failed") }
    }
    /// Called after anything that can change alerts (so the menu bar updates at once).
    @ObservationIgnored public var onAlertsChanged: () -> Void = {}

    public var alerts: [SkillAlert] { status?.alerts ?? [] }

    @ObservationIgnored private let transport: () -> (any AuraTransport)?
    @ObservationIgnored private var polling: Task<Void, Never>?

    public init(transport: @escaping () -> (any AuraTransport)?) {
        self.transport = transport
    }

    /// For previews and snapshot tests.
    public init(skills: [SkillInfo] = [], inbox: [InboxItem] = [], agents: [AgentInfo] = [], agentInbox: [AgentInfo] = [], retiredAgents: [AgentInfo] = [], tasks: [TaskInfo] = [], selected: TaskInfo? = nil,
                status: SystemStatus? = nil, developer: DeveloperInfo? = nil, selectedBuildID: String? = nil) {
        transport = { nil }
        self.skills = skills
        self.inbox = inbox
        self.agents = agents
        self.agentInbox = agentInbox
        self.retiredAgents = retiredAgents
        self.tasks = tasks
        self.selected = selected
        selectedTaskID = selected?.id
        self.status = status
        self.developer = developer
        self.selectedBuildID = selectedBuildID
    }

    /// Show a task's Audit Card or a build ("b-…"), e.g. from the chat or the menu bar.
    public func select(_ id: String) {
        if id.hasPrefix("b-") {
            selectedBuildID = id
            selectedTaskID = nil
            selected = nil
        } else {
            selectedBuildID = nil
            selectedTaskID = id
        }
    }

    public func refresh() async {
        guard let t = transport() else { return }
        do {
            let overview = try await t.skills()
            skills = overview.installed
            inbox = overview.inbox
            let agentOverview = try await t.agents()
            agents = agentOverview.installed
            agentInbox = agentOverview.inbox
            retiredAgents = agentOverview.retired ?? []
            tasks = try await t.tasks()
            status = try? await t.status()
            developer = try? await t.developer()
            lastError = nil
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
        await loadSelected()
        if isBusy { startPolling() }
    }

    /// Something is in progress that the Workshop should follow live.
    public var isBusy: Bool { tasks.contains(where: \.isRunning) || builds.contains(where: \.isRunning) }

    /// The New Skill / Improve sheet's Build button: the user's click, with the specification,
    /// the Developer, the limits and what leaves this Mac in view, is the approval. Returns
    /// whether the build started.
    public func startBuild(_ request: BuildRequest) async -> Bool {
        guard let t = transport() else { return false }
        do {
            let build = try await t.startBuild(request)
            buildError = nil
            await refresh()
            select(build.id)
            onAlertsChanged()  // the menu bar follows the build, to say when it's ready
            return true
        } catch let AuraClientError.http(_, message) {
            buildError = message
        } catch {
            buildError = FriendlyMessage.describe(error)
        }
        return false
    }

    public func clearBuildError() { buildError = nil }

    public func stopBuild(_ id: String) async {
        await act { _ = try await $0.stopBuild(id) }
    }

    /// Retry an interrupted build: the same specification, Developer and limits (the user's click).
    public func retry(_ id: String) async {
        guard let t = transport() else { return }
        do {
            let build = try await t.retryBuild(id)
            await refresh()
            select(build.id)
            onAlertsChanged()
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
    }

    public func dismissRecovery(_ id: String) async {
        await act { try await $0.dismissRecovery(id) }
    }

    /// From a ready build to its Audit Card (Code Review).
    public func review(_ build: BuildInfo) {
        guard let task = build.taskId else { return }
        select(task)
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

    /// An agent import follows the same shadow-audit and explicit deployment path as a skill.
    public func proposeAgent(_ name: String) async {
        guard let t = transport() else { return }
        do {
            let task = try await t.proposeAgent(name)
            await refresh()
            selectedTaskID = task.id
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
    }

    /// Create or quarantine an import only after the user submits this form. It still needs an
    /// independent audit and an explicit Approve & Deploy decision before recognition.
    public func createAgent(_ request: AgentCandidateRequest, imported: Bool) async -> Bool {
        guard let t = transport() else { return false }
        do {
            _ = try await t.createAgent(request, imported: imported)
            agentError = nil
            await refresh()
            return true
        } catch {
            agentError = FriendlyMessage.describe(error)
            return false
        }
    }

    public func clearAgentError() { agentError = nil }

    public func suspendAgent(_ name: String) async {
        await agentAction { try await $0.suspendAgent(name, reason: "suspected_breach") }
    }

    public func retireAgent(_ name: String) async {
        await agentAction { try await $0.retireAgent(name, reason: "user_retirement") }
    }

    public func revalidateAgent(_ name: String) async {
        guard let t = transport() else { return }
        do {
            let task = try await t.revalidateAgent(name)
            await refresh()
            selectedTaskID = task.id
        } catch {
            agentError = FriendlyMessage.describe(error)
        }
    }

    private func agentAction(_ action: (any AuraTransport) async throws -> AgentInfo) async {
        guard let t = transport() else { return }
        do {
            _ = try await action(t)
            agentError = nil
            await refresh()
        } catch {
            agentError = FriendlyMessage.describe(error)
        }
    }

    /// The Audit Card decision. Deploy only ever follows an explicit approve.
    public func decide(approve: Bool) async {
        guard let t = transport(), let id = selected?.id else { return }
        do {
            _ = try await t.decide(id, approve: approve)
            await refresh()
            // A completed deployment/rejection is deliberately removed from the action queue.
            selectedTaskID = nil
            selected = nil
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
    }

    /// Requests cancellation. The server stops at the next safe checkpoint and never installs a skill.
    public func cancelAudit(_ id: String) async {
        await act { _ = try await $0.cancelAudit(id) }
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

    /// Gemma's alert, answered: remove the skill (a local commit) ...
    public func uninstall(_ skill: String) async {
        await act { try await $0.uninstall(skill) }
    }

    /// ... or keep it, switched off, and stop the alert.
    public func keep(_ skill: String) async {
        await act { try await $0.keepFlagged(skill) }
    }

    /// Re-check every installed skill against PyPI and OSV now, then show the result.
    public func checkNow() async {
        await act { try await $0.auditNow() }
        for _ in 0..<60 where status?.continuousAudit?.running == true {
            try? await Task.sleep(for: .seconds(1))
            await refresh()
        }
        onAlertsChanged()
    }

    private func act(_ action: (any AuraTransport) async throws -> Void) async {
        guard let t = transport() else { return }
        do {
            try await action(t)
            await refresh()
            onAlertsChanged()
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
                if !self.isBusy { break }
            }
            self?.polling = nil
        }
    }
}
