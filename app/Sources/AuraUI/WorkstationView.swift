import AuraKit
import SwiftUI

/// The in-app registry for non-executable Workstation agent records.  It is deliberately not a
/// file browser: every write follows the existing candidate, audit, and explicit deployment path.
public struct WorkstationView: View {
    let store: WorkshopStore
    @State private var sheet: AgentSheetRequest?

    public init(store: WorkshopStore) {
        self.store = store
    }

    private var agentAudits: [TaskInfo] {
        store.visibleTasks.filter { $0.kind == "agent" }
    }

    private var selectedAgentAudit: TaskInfo? {
        guard let selected = store.selected, selected.kind == "agent" else { return nil }
        return selected
    }

    private var selection: Binding<String?> {
        Binding(
            get: { selectedAgentAudit?.id },
            set: { id in
                store.selectedBuildID = nil
                store.selectedTaskID = id
            }
        )
    }

    public var body: some View {
        AuraThemedPage {
        NavigationSplitView {
            List(selection: selection) {
                Section("Registry health") {
                    WorkstationHealthRow(health: store.agentHealth)
                }
                Section("Agent Inbox") {
                    if store.agentInbox.isEmpty {
                        Text("No candidates waiting for audit.").foregroundStyle(.secondary)
                    } else {
                        ForEach(store.agentInbox) { candidate in
                            CandidateRow(candidate: candidate) {
                                Task { await store.proposeAgent(candidate.name) }
                            }
                        }
                    }
                }
                Section("Recognized agents") {
                    if store.agents.isEmpty {
                        Text("No recognized agents.").foregroundStyle(.secondary)
                    } else {
                        ForEach(store.agents) { agent in
                            RecognizedAgentRow(agent: agent, store: store)
                        }
                    }
                }
                Section("Retired records") {
                    if store.retiredAgents.isEmpty {
                        Text("No retired agent records.").foregroundStyle(.secondary)
                    } else {
                        ForEach(store.retiredAgents) { agent in
                            AgentDescription(agent: agent, prefix: "retired · archived without operational recognition")
                        }
                    }
                }
                Section("Agent audits") {
                    if agentAudits.isEmpty {
                        Text("No agent audits need attention.").foregroundStyle(.secondary)
                    } else {
                        ForEach(agentAudits) { task in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(task.skill).font(.body.weight(.medium))
                                Text(task.isRunning ? (task.phase ?? task.message) : auditStage(task))
                                    .font(.caption).foregroundStyle(.secondary)
                                if task.isRunning, let progress = task.progress {
                                    ProgressView(value: Double(progress), total: 100).controlSize(.small)
                                }
                            }
                            .tag(Optional(task.id))
                        }
                    }
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 300, ideal: 350)
        } detail: {
            Group {
                if let task = selectedAgentAudit {
                    AuditCardView(task: task,
                                  onDecide: { approve in Task { await store.decide(approve: approve) } },
                                  onCancel: { Task { await store.cancelAudit(task.id) } })
                } else {
                    WorkstationWelcome(health: store.agentHealth)
                }
            }
            .toolbar {
                ToolbarItem {
                    Button { sheet = .create } label: { Label("Create Agent", systemImage: "plus") }
                        .help("Create a non-executable local candidate in the Agent Inbox")
                }
                ToolbarItem {
                    Button { sheet = .import } label: { Label("Import Agent", systemImage: "square.and.arrow.down") }
                        .help("Quarantine external agent metadata for audit")
                }
                ToolbarItem {
                    Button { Task { await store.refresh() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }
                        .help("Refresh the read-only registry snapshot")
                }
            }
        }
        .sheet(item: $sheet) { request in NewAgentSheet(store: store, imported: request == .import) }
        .task { await store.refresh() }
        .frame(minHeight: 520)
        }
    }

    private func auditStage(_ task: TaskInfo) -> String {
        switch task.stage {
        case "staging": "Creating a shadow project…"
        case "auditing": "Running the Governor's checks…"
        case "awaiting": task.report?.blocked == true ? "Blocked" : "Awaiting your decision"
        default: task.message
        }
    }
}

private struct WorkstationHealthRow: View {
    let health: AgentGovernanceHealth?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label(label, systemImage: icon).font(.body.weight(.semibold)).foregroundStyle(color)
                Spacer()
                if health == nil { ProgressView().controlSize(.small) }
            }
            Text(health?.detail ?? "Refreshing the read-only registry snapshot…")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let counts = health?.agents {
                let active = counts["active", default: 0]
                let suspended = counts["suspended", default: 0]
                let expired = counts["expired", default: 0]
                let due = counts["due", default: 0]
                Text("Active \(active) · Suspended \(suspended) · Expired \(expired) · Review due \(due)")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private var label: String { health?.state == "healthy" ? "Healthy" : health?.state == "attention" ? "Needs attention" : "Unknown" }
    private var icon: String { health?.state == "healthy" ? "checkmark.shield.fill" : "exclamationmark.shield.fill" }
    private var color: Color { health?.state == "healthy" ? .green : health == nil ? .secondary : .orange }
}

private struct WorkstationWelcome: View {
    let health: AgentGovernanceHealth?

    var body: some View {
        ContentUnavailableView {
            Label("Aura Workstation", systemImage: "desktopcomputer")
        } description: {
            Text("The Workstation is Aura's registry for non-executable agent records. Create or import metadata into quarantine; each candidate still requires a Security Governor audit and your explicit approval before recognition.")
        } actions: {
            if health?.state == "unknown" {
                Label("Registry evidence is incomplete. Review the health detail before acting.", systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }
}

private struct CandidateRow: View {
    let candidate: AgentInfo
    let audit: () -> Void

    var body: some View {
        HStack(alignment: .top) {
            AgentDescription(agent: candidate, prefix: "quarantined candidate")
            Spacer()
            Button("Audit", action: audit).controlSize(.small).disabled(candidate.error != nil)
        }
    }
}

private struct RecognizedAgentRow: View {
    let agent: AgentInfo
    let store: WorkshopStore

    var body: some View {
        HStack(alignment: .top) {
            AgentDescription(agent: agent, prefix: "\(agent.state ?? "active") · \(agent.origin)")
            Spacer()
            if ["suspended", "expired"].contains(agent.state) {
                Button("Revalidate") { Task { await store.revalidateAgent(agent.name) } }
                    .controlSize(.small)
            } else {
                Menu {
                    Button("Suspend", role: .destructive) { Task { await store.suspendAgent(agent.name) } }
                    Button("Retire", role: .destructive) { Task { await store.retireAgent(agent.name) } }
                } label: { Image(systemName: "ellipsis.circle") }
                    .menuStyle(.borderlessButton)
            }
        }
    }
}

private struct AgentDescription: View {
    let agent: AgentInfo
    let prefix: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(agent.version.isEmpty ? agent.name : "\(agent.name) \(agent.version)")
            Text(agent.error ?? "\(prefix) · \(agent.purpose)")
                .font(.caption)
                .foregroundStyle(agent.error == nil ? Color.secondary : Color.orange)
                .lineLimit(3)
        }
    }
}

enum AgentSheetRequest: String, Identifiable {
    case create, `import`
    var id: String { rawValue }
}

/// The Workstation's controlled front door.  Imported content is descriptive metadata only.
struct NewAgentSheet: View {
    let store: WorkshopStore
    let imported: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var purpose = ""
    @State private var provenance = ""
    @State private var intentDelta = "Unmeasured: candidate has not been evaluated."
    @State private var memory = "No memory imported."
    @State private var saving = false

    private var nameValid: Bool {
        name.range(of: "^[a-z][a-z0-9_]{1,38}$", options: .regularExpression) != nil
    }

    private var problems: [String] {
        var values: [String] = []
        if !nameValid { values.append("Name: lowercase letters, digits, and underscores; 2–39 characters.") }
        if purpose.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { values.append("Purpose is required.") }
        if provenance.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { values.append("Provenance is required.") }
        if imported && intentDelta.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { values.append("Intent delta is required for an import.") }
        if imported && memory.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { values.append("State whether memory was imported.") }
        return values
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(imported ? "Import Agent Candidate" : "Create Agent Candidate").font(.title2.weight(.semibold))
            Text(imported
                 ? "This quarantines an external agent's declared metadata. It does not connect to or execute that agent."
                 : "This creates a local non-executable candidate in the Workstation Agent Inbox.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            LabeledContent("Name") { TextField("e.g. research_scout", text: $name).textFieldStyle(.roundedBorder) }
            FieldEditor(title: "Purpose", text: $purpose, height: 58)
            VStack(alignment: .leading, spacing: 4) {
                Text("Provenance").font(.headline)
                TextField(imported ? "Where did this agent description come from?" : "Created locally through Aura", text: $provenance)
                    .textFieldStyle(.roundedBorder)
            }
            if imported {
                FieldEditor(title: "Intent Delta", text: $intentDelta, height: 52,
                            note: "State the observed gap between claimed and tested behavior. “Unmeasured” is valid before evaluation.")
                FieldEditor(title: "Memory Declaration", text: $memory, height: 44)
            }
            Label("No code, paths, tools, connectors, credentials, routines, model weights, or execution can be imported here.",
                  systemImage: "lock.shield").font(.caption).foregroundStyle(.secondary)
            ForEach(problems, id: \.self) { Label($0, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
            if let error = store.agentError { Label(error, systemImage: "xmark.octagon").font(.caption).foregroundStyle(.red) }
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(imported ? "Quarantine Import" : "Create Candidate") { submit() }
                    .disabled(!problems.isEmpty || saving)
            }
        }
        .padding(20)
        .frame(width: 580)
        .onAppear {
            store.clearAgentError()
            if !imported { provenance = "Created locally through Aura." }
        }
    }

    private func submit() {
        saving = true
        let request = AgentCandidateRequest(name: name, purpose: purpose, provenance: provenance,
                                            intentDelta: intentDelta, memory: memory)
        Task {
            if await store.createAgent(request, imported: imported) { dismiss() }
            saving = false
        }
    }
}

private struct FieldEditor: View {
    let title: String
    @Binding var text: String
    let height: CGFloat
    var note: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.headline)
            TextEditor(text: $text).frame(height: height)
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.3)))
            if let note { Text(note).font(.caption).foregroundStyle(.secondary) }
        }
    }
}
