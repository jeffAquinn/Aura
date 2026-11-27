import AuraKit
import SwiftUI

/// Gemma's Specification Approval card: Aura wants a Developer (Claude Code or Codex) to build a
/// skill. It shows who builds it, the hard limits, exactly what leaves this Mac and the
/// specification itself (all the Developer receives), and lets the user edit the specification
/// and the Developer before approving. Deny is the default (Return); building takes a deliberate click.
struct SpecificationApprovalCard: View {
    let item: ChatStore.Item
    let proposal: BuildProposal
    let store: ChatStore
    @State private var editing = false
    @State private var draft = ""
    @State private var engine = "claude"

    private var engineChanged: Bool { engine != proposal.engine }
    private var specChanged: Bool {
        draft.trimmingCharacters(in: .whitespacesAndNewlines) != proposal.specification
    }
    private var problems: [String] { editing ? Specification.problems(draft) : [] }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("\(store.persona.name) wants to:", systemImage: "hand.raised.fill")
                .font(.subheadline.weight(.semibold))
            Text(Self.summary(proposal, engine: editing ? engine : proposal.engine))
                .font(.callout).fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(notes.enumerated()), id: \.offset) { i, note in
                    Label(note, systemImage: i == 0 ? "arrow.up.forward.circle" : "checkmark.shield")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if editing { developerPicker }
            specification
            footer
        }
        .padding(12)
        .background(.orange.opacity(0.10), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.orange.opacity(0.4), lineWidth: 0.75))
        .padding(.leading, 32)
    }

    /// The server's notes, with "Sent to …" restated when the user picked the other Developer.
    private var notes: [String] {
        guard engineChanged, let chosen = EngineInfo.named(engine) else { return proposal.notes }
        let original = EngineInfo.named(proposal.engine)?.vendor ?? "the vendor"
        return proposal.notes.map { note in
            note.hasPrefix("Sent to ") ? note.replacingOccurrences(of: "Sent to \(original)", with: "Sent to \(chosen.vendor) instead")
                : note
        } + [chosen.costNote]
    }

    private var developerPicker: some View {
        Picker("Developer", selection: $engine) {
            ForEach(EngineInfo.known) { Text($0.label).tag($0.name) }
        }
        .pickerStyle(.segmented)
        .font(.callout)
    }

    private var specification: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Specification").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Spacer()
                if item.decisions == nil, !editing {
                    Button("Edit") { startEditing() }.controlSize(.small)
                }
            }
            Group {
                if editing {
                    TextEditor(text: $draft)
                        .font(.system(.caption, design: .monospaced))
                        .scrollContentBackground(.hidden)
                        .frame(height: 220)
                } else {
                    Scrolling(maxHeight: 200) {
                        Text(proposal.specification)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .padding(8)
            .background(Color.primary.opacity(0.05), in: .rect(cornerRadius: 8))
            ForEach(problems, id: \.self) { problem in
                Label("The specification isn't complete: \(problem)", systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }

    @ViewBuilder private var footer: some View {
        if let decisions = item.decisions {
            Label(Self.outcome(decisions, edited: item.edited, elsewhere: item.answeredElsewhere), systemImage: decisions.contains(true) ? "checkmark.circle" : "xmark.circle")
                .font(.caption)
                .foregroundStyle(.secondary)
        } else if editing {
            HStack(spacing: 8) {
                Button("Discard Edits") { editing = false }
                Spacer()
                Button("Approve & Build") { approve() }
                    .disabled(!problems.isEmpty)
                Button("Deny") { store.decide(decisions: [.deny]) }
                // No Return shortcut while editing: Return types a new line in the specification.
            }
            .controlSize(.regular)
        } else {
            HStack(spacing: 8) {
                Spacer()
                Button("Approve & Build") { store.decide(decisions: [.allow]) }
                Button("Deny") { store.decide(decisions: [.deny]) }
                    .keyboardShortcut(.defaultAction)
            }
            .controlSize(.regular)
        }
    }

    private func startEditing() {
        draft = proposal.specification
        engine = proposal.engine
        editing = true
    }

    /// Only what changed is sent: the server allows the specification and the Developer only.
    private func approve() {
        var args: [String: String] = [:]
        if specChanged { args["specification"] = draft.trimmingCharacters(in: .whitespacesAndNewlines) }
        if engineChanged { args["engine"] = engine }
        store.decide(decisions: [args.isEmpty ? .allow : .allowEdited(args)])
    }

    /// The server's summary; if the user picked the other Developer, restated for it (the new
    /// Developer's own limits apply, so the old ones are dropped rather than shown wrongly).
    static func summary(_ proposal: BuildProposal, engine: String) -> String {
        guard engine != proposal.engine, let from = EngineInfo.named(proposal.engine), let to = EngineInfo.named(engine)
        else { return proposal.summary }
        return proposal.summary
            .replacingOccurrences(of: "have \(from.label) ", with: "have \(to.label) ")
            .replacingOccurrences(of: #"\s*\([^()]*\):$"#, with: ":", options: .regularExpression)
    }

    static func outcome(_ decisions: [Bool], edited: Bool, elsewhere: String? = nil) -> String {
        if decisions.isEmpty, elsewhere == "slack" { return "Answered in Slack" }
        if decisions.isEmpty { return "Expired: Aura restarted before you answered" }
        guard decisions.allSatisfy({ $0 }) else { return "Denied: nothing was sent" }
        return edited ? "Approved with your edits" : "Approved"  // whether it started: the card below, or Aura says why not
    }
}
