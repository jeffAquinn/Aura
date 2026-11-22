import Foundation
import Testing
@testable import AuraKit

/// Sprint 6: the Specification Approval card, the Workshop's builds, and Settings → Keys.

private let spec = """
## Goal
Extract the text of a PDF in the vault, page by page.
## Inputs
path: the PDF's path in the vault.
## Output
The text, with a '--- page N ---' line before each page.
## Steps
Open the file under /vault, read each page with pypdf, join the pages.
## Acceptance tests
A two-page PDF with 'Hello' and 'World' gives both words, in order.
## Constraints
No network. Vault read-only. pypdf==7.1.1 only.
"""

private var decoder: JSONDecoder {
    let d = JSONDecoder()
    d.keyDecodingStrategy = .convertFromSnakeCase
    return d
}

/// Records what the stores sent; scripts the server's answers.
final class FakeDeveloper: AuraTransport, @unchecked Sendable {
    var state = DeveloperInfo()
    var startError: Error?
    var started: [BuildRequest] = []
    var stopped: [String] = []
    var plain: [[Bool]] = []
    var edited: [[ApprovalDecision]] = []
    var keys = [SecretInfo(name: "anthropic_api_key", label: "Anthropic API key", set: false)]
    var saved: [(String, String)] = []

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> {
        plain.append(approvals)
        return AsyncThrowingStream { $0.finish() }
    }
    func approve(decisions: [ApprovalDecision]) -> AsyncThrowingStream<AuraEvent, Error> {
        edited.append(decisions)
        return AsyncThrowingStream { $0.finish() }
    }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }

    func developer() async throws -> DeveloperInfo { state }
    func startBuild(_ request: BuildRequest) async throws -> BuildInfo {
        if let startError { throw startError }
        started.append(request)
        let build = BuildInfo(id: "b-1234abcd", skill: request.skill, engine: request.engine, stage: "queued")
        state = DeveloperInfo(builds: [build])
        return build
    }
    func stopBuild(_ id: String) async throws -> BuildInfo {
        stopped.append(id)
        return state.builds[0]
    }
    func secrets() async throws -> [SecretInfo] { keys }
    func setSecret(_ name: String, value: String) async throws -> SecretInfo {
        guard value.hasPrefix("sk-ant-") else {
            throw AuraClientError.http(status: 422, message: "That doesn't look like an Anthropic API key (it should start with 'sk-ant-').")
        }
        saved.append((name, value))
        let info = SecretInfo(name: name, label: "Anthropic API key", set: true, hint: "sk-ant-…" + value.suffix(4))
        keys = [info]
        return info
    }
}

@MainActor
struct SpecificationApprovalTests {
    /// The approval event exactly as the server sends it (asdict(Action)).
    static let event = """
    {"actions": [{"tool": "develop_skill",
      "args": {"name": "pdf_text", "specification": \(String(data: try! JSONEncoder().encode(spec), encoding: .utf8)!), "engine": "claude", "improve": false},
      "description": "have Claude Code build the new skill 'pdf_text' ($2.00 total, 10 minutes per round, up to 2 repair rounds):\\n    Sent to Anthropic: this specification, the skill format guide and an example skill. Nothing from your vault or this conversation.\\n    The result must pass the Security Governor; you review it in the Workshop before anything is installed.\\n    │ ## Goal\\n    │ Extract the text"}]}
    """

    @Test func theCardReadsTheBuildFromTheToolCall() throws {
        var parser = SSEParser()
        let events = ["event: approval", "data: " + Self.event.replacingOccurrences(of: "\n", with: " ")].compactMap { parser.feed($0) }
        guard case let .approval(actions) = try #require(events.first) else { Issue.record("not an approval"); return }
        let build = try #require(actions[0].build)
        #expect(build.skill == "pdf_text" && build.engine == "claude" && !build.improve)
        #expect(build.specification == spec.trimmingCharacters(in: .whitespacesAndNewlines))
        #expect(build.summary.hasPrefix("have Claude Code build the new skill 'pdf_text' ($2.00 total"))
        #expect(build.notes.count == 2 && build.notes[0].hasPrefix("Sent to Anthropic:"))
        #expect(!build.notes.contains { $0.contains("│") })  // the specification is shown on its own, verbatim
        #expect(PendingAction(tool: "delete_path", description: "delete file 'a.txt'").build == nil)
    }

    @Test func decisionsEncodeAsTheServerExpects() throws {
        let body = try JSONEncoder().encode(["approvals": [ApprovalDecision.allow, .deny, .allowEdited(["specification": "x"])]])
        let json = try #require(try JSONSerialization.jsonObject(with: body) as? [String: [Any]])["approvals"]
        #expect(json?[0] as? Bool == true && json?[1] as? Bool == false)
        let edited = try #require(json?[2] as? [String: Any])
        #expect(edited["approve"] as? Bool == true && (edited["args"] as? [String: String]) == ["specification": "x"])
    }

    @Test func approvingWithEditsSendsOnlyTheEditsAndMarksTheCard() async {
        let fake = FakeDeveloper()
        let chat = ChatStore()
        chat.transport = fake
        chat.apply(.approval([PendingAction(tool: "develop_skill", description: "have Claude Code build…",
                                            args: ["name": .string("pdf_text"), "specification": .string(spec)])]))
        chat.decide(decisions: [.allowEdited(["specification": spec + "Skip empty pages."])])
        await chat.turnTask?.value
        #expect(fake.edited == [[.allowEdited(["specification": spec + "Skip empty pages."])]] && fake.plain.isEmpty)
        #expect(chat.items.last?.decisions == [true] && chat.items.last?.edited == true)
    }

    @Test func plainAnswersStillUseTheBooleanForm() async {
        let fake = FakeDeveloper()
        let chat = ChatStore()
        chat.transport = fake
        chat.apply(.approval([PendingAction(tool: "develop_skill", description: "…", args: ["name": .string("pdf_text")])]))
        chat.decide(decisions: [.deny])
        await chat.turnTask?.value
        #expect(fake.plain == [[false]] && fake.edited.isEmpty && chat.items.last?.edited == false)
    }

    @Test func aStartedBuildBecomesAChatCardAndTheAppFollowsIt() {
        var parser = SSEParser()
        let events = ["event: task", #"data: {"id": "b-1234abcd", "skill": "pdf_text", "stage": "queued", "kind": "build"}"#]
            .compactMap { parser.feed($0) }
        #expect(events == [.build(id: "b-1234abcd", skill: "pdf_text")])
        let chat = ChatStore()
        var followed: [String] = []
        chat.onBuildStarted = { followed.append($0) }
        chat.apply(events[0])
        #expect(chat.items.last?.role == .build && chat.items.last?.actions.first?.description == "b-1234abcd")
        #expect(followed == ["b-1234abcd"])
    }

    @Test func theSpecificationNeedsAllSixSections() {
        #expect(Specification.problems(spec).isEmpty)
        #expect(Specification.problems(Specification.template).first?.hasPrefix("it must be 150–12000 characters") == true)
        let noTests = spec.replacingOccurrences(of: "## Acceptance tests", with: "## Checks")
        #expect(Specification.problems(noTests) == ["missing sections: Acceptance tests"])
        let goalkeeper = spec.replacingOccurrences(of: "## Goal\n", with: "## Goalkeeper\n")
        #expect(Specification.problems(goalkeeper) == ["missing sections: Goal"])  // a whole word, like the server's \b
        #expect(Specification.problems(spec.replacingOccurrences(of: "## ", with: "")).isEmpty)  // headings optional
        #expect(Specification.validName("pdf_text") && !Specification.validName("PDF text") && !Specification.validName("x"))
    }
}

@MainActor
struct BuildStoreTests {
    static let developerJSON = """
    {"engines": [{"name": "claude", "label": "Claude Code", "vendor": "Anthropic", "caps_cost": true,
                  "problem": "Add an Anthropic API key in Settings → Keys."},
                 {"name": "codex", "label": "Codex", "vendor": "OpenAI", "caps_cost": false, "problem": null}],
     "builds": [{"id": "b-1234abcd", "skill": "word_count", "engine": "codex", "improve": false,
                 "specification": "## Goal ...", "stage": "ready", "round": 2, "max_rounds": 3, "cost_usd": 0.0,
                 "budget_usd": 0.0, "message": "Ready for your review: passed every check in round 2.",
                 "task_id": "9f8e7d6c",
                 "history": [{"round": 1, "task": "1a2b3c4d", "passed": false, "findings": ["test test_counts failed: ..."]},
                             {"round": 2, "task": "9f8e7d6c", "passed": true, "findings": [], "risk": "green"}],
                 "feed": [{"t": "2026-11-22T10:00:00+00:00", "text": "Writing skill/skill.py"}],
                 "created": "2026-11-22T10:00:00+00:00", "finished": "2026-11-22T10:01:00+00:00"}],
     "budget_usd": 2.0, "repair_rounds": 2, "timeout_minutes": 10}
    """

    @Test func decodesTheDeveloperStateAndBuiltTasks() throws {
        let info = try decoder.decode(DeveloperInfo.self, from: Data(Self.developerJSON.utf8))
        #expect(info.engines.map(\.name) == ["claude", "codex"] && info.engines[0].problem != nil && !info.engines[1].capsCost)
        let build = info.builds[0]
        #expect(build.awaitsReview && !build.isRunning && build.taskId == "9f8e7d6c" && build.costLabel == "your ChatGPT plan")
        #expect(build.history.map(\.passed) == [false, true] && build.history[1].risk == "green")
        #expect(build.feed[0].time == ISO8601DateFormatter().date(from: "2026-11-22T10:00:00Z"))
        #expect(info.limits(for: info.engines[0]) == "$2.00 total, 10 minutes per round, up to 2 repair rounds")
        #expect(BuildInfo(id: "b", skill: "s", stage: "developing", costUsd: 0.4231, budgetUsd: 2).costLabel == "$0.42 of $2.00")

        let task = try decoder.decode(TaskInfo.self, from: Data("""
        {"id": "9f8e7d6c", "skill": "word_count", "stage": "awaiting", "message": "", "created": "",
         "origin": {"build": "b-1234abcd", "engine": "Codex", "round": 2, "cost_usd": 0.0,
                    "specification": "## Goal ...", "improve": false}}
        """.utf8))
        #expect(task.origin?.engine == "Codex" && task.origin?.round == 2)

        let alerts = try decoder.decode(AlertsInfo.self, from: Data("""
        {"alerts": [], "continuous_audit": {"running": false},
         "builds": [{"id": "b-1234abcd", "skill": "word_count", "stage": "ready", "message": "Ready."}]}
        """.utf8))
        #expect(alerts.builds?.first?.menuTitle == "word_count: ready for your review…")
    }

    @Test func startingABuildSelectsItAndFailuresSayWhy() async {
        let fake = FakeDeveloper()
        let store = WorkshopStore { fake }
        var menuBarUpdates = 0
        store.onAlertsChanged = { menuBarUpdates += 1 }
        let request = BuildRequest(skill: "pdf_text", specification: spec, engine: "codex", improve: false)
        #expect(await store.startBuild(request))
        #expect(fake.started == [request] && store.selectedBuildID == "b-1234abcd" && store.selectedBuild?.skill == "pdf_text")
        #expect(store.selectedTaskID == nil && menuBarUpdates == 1)
        await store.stopBuild("b-1234abcd")
        #expect(fake.stopped == ["b-1234abcd"])

        fake.startError = AuraClientError.http(status: 409, message: "another build is running; wait for it or stop it in the Workshop")
        #expect(await store.startBuild(request) == false)
        #expect(store.buildError == "another build is running; wait for it or stop it in the Workshop")
    }

    @Test func selectionTellsBuildsFromAuditsAndFailedRoundsStayHidden() {
        let ready = BuildInfo(id: "b-1234abcd", skill: "word_count", stage: "ready", taskId: "9f8e7d6c")
        let store = WorkshopStore(tasks: [
            TaskInfo(id: "1a2b3c4d", skill: "word_count", stage: "discarded", message: "Round 1 didn't pass"),
            TaskInfo(id: "9f8e7d6c", skill: "word_count", stage: "awaiting", message: ""),
        ], developer: DeveloperInfo(builds: [ready]))
        #expect(store.visibleTasks.map(\.id) == ["9f8e7d6c"])  // nothing that failed is presented
        store.select("b-1234abcd")
        #expect(store.selectedBuild == ready && store.selectedTaskID == nil)
        store.review(ready)
        #expect(store.selectedBuildID == nil && store.selectedTaskID == "9f8e7d6c")
    }
}

@MainActor
struct KeysTests {
    @Test func keysAreSavedThroughTheServerAndRefusalsExplained() async {
        let fake = FakeDeveloper()
        let chat = ChatStore()
        chat.transport = fake
        await chat.loadSecrets()
        #expect(chat.secrets.map(\.set) == [false])
        let refused = await chat.saveSecret("anthropic_api_key", value: "tvly-wrong-key-0123456789")
        #expect(refused == "That doesn't look like an Anthropic API key (it should start with 'sk-ant-').")
        let saved = await chat.saveSecret("anthropic_api_key", value: "  sk-ant-api03-abcdefghijklmnopqrstWXYZ\n")
        #expect(saved == nil && fake.saved.map(\.1) == ["sk-ant-api03-abcdefghijklmnopqrstWXYZ"])  // trimmed
        #expect(chat.secrets == [SecretInfo(name: "anthropic_api_key", label: "Anthropic API key", set: true, hint: "sk-ant-…WXYZ")])
    }

    @Test func listingsNeverCarryTheValue() throws {
        let listing = try decoder.decode([SecretInfo].self, from: Data("""
        [{"name": "anthropic_api_key", "label": "Anthropic API key (Claude Code, the Developer)", "set": true, "hint": "sk-ant-…WXYZ"},
         {"name": "tavily_api_key", "label": "Tavily API key (web search)", "set": false, "hint": null}]
        """.utf8))
        #expect(listing[0].hint == "sk-ant-…WXYZ" && listing[1].hint == nil)
        #expect(listing[0].source?.host() == "console.anthropic.com")
    }
}
