import AuraKit
import SwiftUI

/// make_plan's card (docs/orchestration.md T5): the goal, each step with its tool, what it waits for
/// and when it's done. Starting a plan approves its direction only, and the card says so: every step
/// that writes, runs code, searches the web, hands work to the Developer or sends still asks. Deny is
/// the default, as on every card.
struct PlanCard: View {
    let item: ChatStore.Item
    let plan: PlanProposal
    let store: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("\(store.persona.name) wants to start a plan:", systemImage: "list.bullet.rectangle")
                .font(.subheadline.weight(.semibold))
            Text(plan.goal)
                .font(.callout.weight(.semibold))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 8) {
                ForEach(plan.steps) { PlanStepRow(step: $0) }
            }
            .padding(10)
            .background(Color.black.opacity(0.08), in: .rect(cornerRadius: 8))
            if let replaced = plan.replaces {
                Label("Replaces the plan still running: \(replaced.goal)", systemImage: "arrow.triangle.2.circlepath")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("Starting the plan doesn't approve its steps: each one that writes, runs code, searches the web, hands work to the Developer or sends still asks you first.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let decisions = item.decisions {
                Label(decisions == [true] ? "Started" : ApprovalCard.outcome(decisions, elsewhere: item.answeredElsewhere),
                      systemImage: decisions.contains(true) ? "checkmark.circle" : "xmark.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                HStack(spacing: 8) {
                    Spacer()
                    Button("Start Plan") { store.decide([true]) }
                    Button("Deny") { store.decide([false]) }
                        .keyboardShortcut(.defaultAction)
                }
                .controlSize(.regular)
            }
        }
        .padding(12)
        .background(.orange.opacity(0.10), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.orange.opacity(0.4), lineWidth: 0.75))
        .padding(.leading, 32)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Plan card: \(plan.goal), \(plan.steps.count) steps")
    }
}

/// One step on the plan card: its number, what it does and its tool, then what it waits for and
/// when it counts as done.
struct PlanStepRow: View {
    let step: PlanProposal.Step

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(step.id)
                .font(.caption.monospaced().weight(.semibold))
                .foregroundStyle(.secondary)
                .frame(minWidth: 22, alignment: .leading)
            VStack(alignment: .leading, spacing: 2) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(step.does).font(.callout)
                    Text(PlanTool.label(step.tool))
                        .font(.caption2.monospaced())
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.quaternary, in: .capsule)
                }
                Text(details).font(.caption).foregroundStyle(.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var details: String {
        let waits = step.after.isEmpty ? "" : "After \(step.after.joined(separator: ", ")). "
        return waits + "Done when: \(step.doneWhen)"
    }
}

/// Above a card raised during a plan: which step of which plan the card is for (T6). Deny on the
/// card fails that step, and the steps that depend on it are blocked.
struct PlanStepBanner: View {
    let ref: PlanStepRef

    var body: some View {
        Label {
            Text("Step \(ref.index) of \(ref.total) of the plan “\(ref.goal)”: \(ref.does)")
                .fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: "list.bullet.rectangle")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.leading, 44)
        .padding(.bottom, 4)
        .help("Deny fails this step, and the steps that depend on it won't run.")
    }
}

enum PlanTool {
    /// `think` is Aura's own work, with no tool call.
    static func label(_ tool: String) -> String { tool == "think" ? "Aura thinks" : tool }
}

/// The running plan, as one line above the chat box: its goal, how many steps are done, and the
/// step being worked on. Each step's status is in the tooltip.
struct PlanProgressLine: View {
    let plan: ActivePlan

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "list.bullet.rectangle").foregroundStyle(.secondary)
            Text(plan.goal).lineLimit(1).truncationMode(.tail)
            Spacer(minLength: 8)
            if let current = plan.current {
                Text("\(current.id): \(current.does)")
                    .lineLimit(1).truncationMode(.tail)
                    .foregroundStyle(.secondary)
                    .layoutPriority(-1)
            }
            Text("\(plan.done) of \(plan.total) done").monospacedDigit().foregroundStyle(.secondary)
        }
        .font(.caption)
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
        .background(.ultraThinMaterial.opacity(0.6))
        .help(plan.steps.map { "\($0.id) [\($0.status)] \($0.does)" }.joined(separator: "\n"))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Plan running: \(plan.goal), \(plan.done) of \(plan.total) steps done")
    }
}
