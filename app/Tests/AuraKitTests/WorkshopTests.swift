import Foundation
import Testing
@testable import AuraKit

/// A fake Workshop server: one inbox skill; proposing creates a task; deciding records the flag.
final class FakeWorkshop: AuraTransport, @unchecked Sendable {
    var taskList: [TaskInfo] = []
    var decisions: [(String, Bool)] = []
    var running = false
    var actions: [String] = []
    var agentRequests: [(AgentCandidateRequest, Bool)] = []

    func uninstall(_ skill: String) async throws { actions.append("uninstall \(skill)") }
    func keepFlagged(_ skill: String) async throws { actions.append("keep \(skill)") }
    func auditNow() async throws { actions.append("audit") }

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }

    func skills() async throws -> SkillsOverview {
        SkillsOverview(installed: [], inbox: [InboxItem(name: "pdf_text", version: "1.0.0", purpose: "Extract PDF text.", error: nil)])
    }

    func agents() async throws -> AgentsOverview { AgentsOverview(installed: [], inbox: []) }

    func createAgent(_ request: AgentCandidateRequest, imported: Bool) async throws -> AgentInfo {
        agentRequests.append((request, imported))
        return AgentInfo(name: request.name, version: "1.0.0", purpose: request.purpose,
                         origin: imported ? "imported" : "created", error: nil)
    }

    func propose(_ skill: String) async throws -> TaskInfo {
        let task = TaskInfo(id: "t1", skill: skill, stage: running ? "auditing" : "awaiting", message: "",
                            report: running ? nil : Self.report)
        taskList = [task]
        return task
    }

    func tasks() async throws -> [TaskInfo] { taskList }
    func task(_ id: String) async throws -> TaskInfo { taskList.first { $0.id == id }! }

    func decide(_ id: String, approve: Bool) async throws -> TaskInfo {
        decisions.append((id, approve))
        taskList = taskList.map { TaskInfo(id: $0.id, skill: $0.skill, stage: approve ? "deployed" : "rejected", message: "") }
        return taskList[0]
    }

    func cancelAudit(_ id: String) async throws -> TaskInfo {
        actions.append("cancel \(id)")
        taskList = taskList.map { TaskInfo(id: $0.id, skill: $0.skill, stage: "cancelled", message: "Cancelled.") }
        return taskList[0]
    }

    static let report = AuditReportInfo(
        skill: "pdf_text", version: "1.0.0", purpose: "Extract PDF text.",
        findings: [Finding(check: "write-scope", severity: "pass", message: "only skills/pdf_text/ changed")],
        tests: [TestResult(name: "test_extracts", status: "passed")],
        suite: SuiteResult(passed: 213, failed: 0, summary: "213 passed"), risk: "green", blocked: false)
}

@MainActor
struct WorkshopStoreTests {
    @Test func creatingAnImportUsesTheQuarantineEndpointShape() async {
        let fake = FakeWorkshop()
        let store = WorkshopStore { fake }
        let request = AgentCandidateRequest(name: "research_scout", purpose: "Summarize public sources.",
                                            provenance: "User-supplied template metadata.",
                                            intentDelta: "Unmeasured.", memory: "No memory imported.")
        let created = await store.createAgent(request, imported: true)
        #expect(created)
        #expect(fake.agentRequests.count == 1)
        #expect(fake.agentRequests[0].0 == request && fake.agentRequests[0].1)
    }

    @Test func proposingSelectsTheNewAuditAndDecidingSendsTheChoice() async {
        let fake = FakeWorkshop()
        let store = WorkshopStore { fake }
        await store.refresh()
        #expect(store.inbox.map(\.name) == ["pdf_text"])

        await store.propose("pdf_text")
        #expect(store.selectedTaskID == "t1")
        for _ in 0..<10_000 where store.selected == nil { await Task.yield() }
        #expect(store.selected?.awaitsDecision == true && store.selected?.report?.risk == "green")

        await store.decide(approve: false)  // the default answer
        #expect(fake.decisions.count == 1 && fake.decisions[0] == ("t1", false))
        #expect(store.tasks.first?.stage == "rejected")
    }

    @Test func onlyASuccessfulApprovalSignalsOtherSurfacesToRefresh() async {
        let fake = FakeWorkshop()
        let store = WorkshopStore { fake }
        var deployments = 0
        store.onDeployed = { deployments += 1 }

        await store.propose("pdf_text")
        for _ in 0..<10_000 where store.selected == nil { await Task.yield() }
        await store.decide(approve: false)
        #expect(deployments == 0)

        await store.propose("pdf_text")
        for _ in 0..<10_000 where store.selected == nil { await Task.yield() }
        await store.decide(approve: true)
        #expect(deployments == 1)
    }

    @Test func cancellingAnAuditCallsTheServer() async {
        let fake = FakeWorkshop()
        fake.running = true
        let store = WorkshopStore { fake }
        await store.propose("pdf_text")
        await store.cancelAudit("t1")
        #expect(fake.actions == ["cancel t1"])
        #expect(store.tasks.first?.stage == "cancelled")
    }

    @Test func decodesTheServersJSON() throws {
        let json = """
        {"id": "ab12", "skill": "pdf_text", "stage": "awaiting", "message": "Ready for your review.",
         "created": "2026-11-22T06:24:54+00:00",
         "report": {"skill": "pdf_text", "version": "1.0.0", "purpose": "Extract text.",
                    "findings": [{"check": "provenance", "severity": "red", "message": "4 known vulnerabilities", "detail": "GHSA-x"}],
                    "tests": [], "suite": {}, "image": "", "risk": "red", "blocked": true},
         "diff": [{"file": "skill.py", "status": "added", "rows": [{"left": null, "right": "def run(args):", "kind": "added"}]}]}
        """
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        let task = try d.decode(TaskInfo.self, from: Data(json.utf8))
        #expect(task.report?.blocked == true && task.report?.findings.first?.detail == "GHSA-x")
        #expect(task.diff?.first?.rows.first == DiffRow(left: nil, right: "def run(args):", kind: "added"))
        let status = try d.decode(SystemStatus.self, from: Data("""
        {"model": "gemma4:26b", "model_loaded": true, "server_memory_mb": 420, "docker": true,
         "skills": {"enabled": 1, "installed": 1}, "tasks_running": 0}
        """.utf8))
        #expect(status.serverMemoryMb == 420 && status.skills.enabled == 1)
    }

    @Test func taskEventsParseIntoAChatCard() {
        var parser = SSEParser()
        let events = ["event: task", #"data: {"id": "ab12", "skill": "pdf_text", "stage": "staging"}"#].compactMap { parser.feed($0) }
        #expect(events == [.task(id: "ab12", skill: "pdf_text")])
        let chat = ChatStore()
        chat.apply(events[0])
        #expect(chat.items.last?.role == .task && chat.items.last?.text == "pdf_text")
    }

    @Test func continuousAuditAlertsDecodeAndOnlyUnansweredOnesArePending() throws {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        let info = try d.decode(AlertsInfo.self, from: Data("""
        {"alerts": [
          {"skill": "pdf_text", "message": "Warning: The pdf_text skill has been flagged with a new vulnerability. Would you like to uninstall it?",
           "findings": [{"check": "provenance", "severity": "red", "message": "1 known vulnerabilities in pypdf 7.1.1", "detail": "GHSA-1"}],
           "since": "2026-11-22T07:00:00+00:00", "acknowledged": false},
          {"skill": "old_tool", "message": "Warning: ...", "findings": [], "since": "2026-11-20T07:00:00+00:00", "acknowledged": true}],
         "continuous_audit": {"last_quick": "2026-11-22T07:00:00+00:00", "last_deep": "2026-11-21T03:00:00+00:00",
                              "last_error": null, "running": false}}
        """.utf8))
        #expect(SkillAlert.pending(info.alerts).map(\.skill) == ["pdf_text"])
        #expect(info.alerts[0].menuTitle == "⚠︎ pdf_text: new security problem. Review…")
        #expect(info.continuousAudit.lastChecked == ISO8601DateFormatter().date(from: "2026-11-22T07:00:00Z"))

        let skill = try d.decode(SkillInfo.self, from: Data("""
        {"name": "pdf_text", "status": "approved", "enabled": true, "loaded": false, "error": null,
         "flag": {"since": "2026-11-22T07:00:00+00:00", "kind": "quick", "acknowledged": false,
                  "findings": [{"check": "provenance", "severity": "red", "message": "1 known vulnerabilities", "detail": "GHSA-1"}]},
         "version": "1.0.0", "purpose": "Extract PDF text.", "permissions": {"network": false, "vault": "read"}, "dependencies": []}
        """.utf8))
        #expect(skill.enabled && skill.loaded == false && skill.flag?.findings.first?.detail == "GHSA-1")
    }

    @Test func answeringAnAlertCallsTheServerAndUpdatesTheMenuBar() async {
        let fake = FakeWorkshop()
        let store = WorkshopStore { fake }
        var menuBarUpdates = 0
        store.onAlertsChanged = { menuBarUpdates += 1 }
        await store.keep("pdf_text")
        await store.uninstall("pdf_text")
        await store.checkNow()
        #expect(fake.actions == ["keep pdf_text", "uninstall pdf_text", "audit"])
        #expect(menuBarUpdates >= 3)
    }
}

