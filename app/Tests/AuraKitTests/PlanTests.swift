import Foundation
import Testing
@testable import AuraKit

@Suite struct PlanTests {
    static let planJSON = #"""
    {"goal": "Write a note on tides", "replaces": {"id": "p-1a2b3c4d", "goal": "Old plan"},
     "steps": [{"id": "s1", "does": "Find notes", "tool": "search_vault", "after": [], "done_when": "listed"},
               {"id": "s2", "does": "Draft", "tool": "think", "after": ["s1"], "done_when": "a draft"}]}
    """#

    @Test func theLiveStreamsPlanCardDecodes() {
        var parser = SSEParser()
        let lines = ["event: approval", "data: {\"actions\": [{\"tool\": \"make_plan\", \"description\": \"Start this plan?\", \"plan\": \(Self.planJSON.replacingOccurrences(of: "\n", with: " "))}], \"approval_id\": \"a-9\"}"]
        let events = lines.compactMap { parser.feed($0) }
        guard case let .approval(actions)? = events.first else { Issue.record("no approval event: \(events)"); return }
        let plan = try? #require(actions.first?.plan)
        #expect(plan?.steps.map(\.doneWhen) == ["listed", "a draft"])
        #expect(plan?.steps[1].after == ["s1"] && plan?.replaces?.goal == "Old plan")
    }

    @Test func theCardShownAgainAfterARestartDecodesToo() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = #"{"tool": "make_plan", "description": "Start this plan?", "plan": "# + Self.planJSON + "}"
        let action = try decoder.decode(PendingAction.self, from: Data(json.utf8))
        #expect(action.plan?.steps.first?.doneWhen == "listed")
    }

    @Test func otherCardsHaveNoPlanAndOlderServersStillDecode() throws {
        let action = try JSONDecoder().decode(PendingAction.self, from: Data(#"{"tool": "write_file", "description": "x"}"#.utf8))
        #expect(action.plan == nil)
        let null = try JSONDecoder().decode(PendingAction.self, from: Data(#"{"tool": "write_file", "description": "x", "plan": null}"#.utf8))
        #expect(null.plan == nil)
    }

    @Test func theRunningPlanDecodesAndKnowsItsCurrentStep() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = #"{"plan": {"id": "p-1", "goal": "Tides", "done": 1, "total": 3, "steps": [{"id": "s1", "does": "a", "status": "done"}, {"id": "s2", "does": "b", "status": "running"}, {"id": "s3", "does": "c", "status": "pending"}]}}"#
        let plan = try #require(try decoder.decode(ActivePlanReply.self, from: Data(json.utf8)).plan)
        #expect(plan.current?.id == "s2" && plan.done == 1)
        #expect(try decoder.decode(ActivePlanReply.self, from: Data(#"{"plan": null}"#.utf8)).plan == nil)
    }
}
