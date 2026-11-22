import Foundation
import Testing
@testable import AuraKit

/// A fake Workshop server: one inbox skill; proposing creates a task; deciding records the flag.
final class FakeWorkshop: AuraTransport, @unchecked Sendable {
    var taskList: [TaskInfo] = []
    var decisions: [(String, Bool)] = []
    var running = false

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }

    func skills() async throws -> SkillsOverview {
        SkillsOverview(installed: [], inbox: [InboxItem(name: "pdf_text", version: "1.0.0", purpose: "Extract PDF text.", error: nil)])
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

    static let report = AuditReportInfo(
        skill: "pdf_text", version: "1.0.0", purpose: "Extract PDF text.",
        findings: [Finding(check: "write-scope", severity: "pass", message: "only skills/pdf_text/ changed")],
        tests: [TestResult(name: "test_extracts", status: "passed")],
        suite: SuiteResult(passed: 213, failed: 0, summary: "213 passed"), risk: "green", blocked: false)
}

@MainActor
struct WorkshopStoreTests {
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
}
