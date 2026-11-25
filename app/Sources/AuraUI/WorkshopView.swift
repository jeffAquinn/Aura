import AuraKit
import SwiftUI

/// The Workshop: where the user, as Chief Auditor, reviews and decides on new skills.
public struct WorkshopView: View {
    let store: WorkshopStore
    let openWorkstation: () -> Void
    @State private var sheet: BuildSheetRequest?
    @State private var agentSheet: AgentSheetRequest?

    public init(store: WorkshopStore, openWorkstation: @escaping () -> Void = {}) {
        self.store = store
        self.openWorkstation = openWorkstation
    }

    public var body: some View {
        NavigationSplitView {
            WorkshopSidebar(store: store, onBuild: { sheet = BuildSheetRequest(improve: $0) })
                .navigationSplitViewColumnWidth(min: 240, ideal: 280)
        } detail: {
            Group {
                if let build = store.selectedBuild {
                    BuildView(build: build,
                              onStop: { Task { await store.stopBuild(build.id) } },
                              onReview: { store.review(build) })
                } else if let task = store.selected {
                    AuditCardView(task: task,
                                  onDecide: { approve in Task { await store.decide(approve: approve) } },
                                  onCancel: { Task { await store.cancelAudit(task.id) } })
                } else {
                    ContentUnavailableView(
                        "Nothing selected",
                        systemImage: "checkmark.shield",
                        description: Text("Have a Developer build a skill, audit a Workstation agent candidate, or ask Aura."))
                }
            }
            .toolbar {
                ToolbarItem {
                    Button { sheet = BuildSheetRequest(improve: nil) } label: { Label("New Skill", systemImage: "plus") }
                        .help("Have Claude Code or Codex build a new skill")
                }
                ToolbarItem {
                    Menu {
                        Button("Create Agent…") { agentSheet = .create }
                        Button("Import Agent…") { agentSheet = .import }
                        Divider()
                        Button("Open Workstation in Finder", action: openWorkstation)
                    } label: {
                        Label("Workstation", systemImage: "desktopcomputer")
                    }
                    .help("Create or quarantine an agent candidate, or open its Vault folder")
                }
            }
            .safeAreaInset(edge: .top, spacing: 0) {
                let pending = SkillAlert.pending(store.alerts)
                if !pending.isEmpty {
                    VStack(spacing: 8) {
                        ForEach(pending) { alert in
                            AlertBanner(alert: alert,
                                        onKeep: { Task { await store.keep(alert.skill) } },
                                        onUninstall: { Task { await store.uninstall(alert.skill) } })
                        }
                    }
                    .padding(12)
                    .background(.bar)
                }
            }
        }
        .sheet(item: $sheet) { request in NewBuildSheet(store: store, improve: request.improve) }
        .sheet(item: $agentSheet) { request in NewAgentSheet(store: store, imported: request == .import) }
        .task { await store.refresh() }
    }
}

private enum AgentSheetRequest: String, Identifiable {
    case create, `import`
    var id: String { rawValue }
}

/// The Workstation's user-facing front door. Imports describe an arriving Other; they cannot
/// include executable content or connect an external account through this form.
private struct NewAgentSheet: View {
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
                 ? "This records an external agent's declared metadata in quarantine. It does not connect to or execute that agent."
                 : "This creates a local non-executable candidate in the Workstation Agent Inbox.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            LabeledContent("Name") { TextField("e.g. research_scout", text: $name).textFieldStyle(.roundedBorder) }
            VStack(alignment: .leading, spacing: 4) {
                Text("Purpose").font(.headline)
                TextEditor(text: $purpose).frame(height: 58).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.3)))
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Provenance").font(.headline)
                TextField(imported ? "Where did this agent description come from?" : "Created locally through Aura", text: $provenance)
                    .textFieldStyle(.roundedBorder)
            }
            if imported {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Intent Delta").font(.headline)
                    TextEditor(text: $intentDelta).frame(height: 52).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.3)))
                    Text("State the observed gap between claimed and tested behavior. “Unmeasured” is valid before evaluation.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("Memory Declaration").font(.headline)
                    TextEditor(text: $memory).frame(height: 44).overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(Color.secondary.opacity(0.3)))
                }
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

struct WorkshopSidebar: View {
    let store: WorkshopStore
    let onBuild: (String?) -> Void  // nil: a new skill; a name: improve that installed skill

    private var selection: Binding<String?> {
        Binding(get: { store.selectedBuildID ?? store.selectedTaskID },
                set: { id in
                    if let id { store.select(id) } else { store.selectedBuildID = nil; store.selectedTaskID = nil }
                })
    }

    var body: some View {
        List(selection: selection) {
            if !store.recovery.isEmpty {
                Section("Interrupted by a restart") {
                    ForEach(store.recovery) { item in RecoveryRow(item: item, store: store) }
                }
            }
            Section {
                if store.builds.isEmpty { Text("None yet").foregroundStyle(.secondary) }
                ForEach(store.builds) { build in BuildRow(build: build).tag(Optional(build.id)) }
            } header: {
                HStack {
                    Text("Builds")
                    Spacer()
                    Button("New Skill…") { onBuild(nil) }.buttonStyle(.link).font(.caption)
                }
            }
            Section("Audits") {
                if store.visibleTasks.isEmpty { Text("None yet").foregroundStyle(.secondary) }
                ForEach(store.visibleTasks) { task in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(task.skill).font(.body.weight(.medium))
                            Text(task.isRunning ? (task.phase ?? task.message) : stageLabel(task))
                                .font(.caption).foregroundStyle(.secondary)
                            if task.isRunning, let progress = task.progress {
                                ProgressView(value: Double(progress), total: 100).controlSize(.small)
                            }
                        }
                        Spacer()
                        if task.isRunning { ProgressView().controlSize(.small) }
                        else if let risk = task.report?.risk { RiskBadge(risk: risk, compact: true) }
                    }
                    .tag(Optional(task.id))
                }
            }
            Section("Skills Inbox") {
                if store.inbox.isEmpty { Text("Empty").foregroundStyle(.secondary) }
                ForEach(store.inbox) { item in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name)
                            Text(item.error ?? item.purpose).font(.caption).foregroundStyle(item.error == nil ? Color.secondary : Color.orange).lineLimit(2)
                        }
                        Spacer()
                        Button("Audit") { Task { await store.propose(item.name) } }
                            .controlSize(.small)
                            .disabled(item.error != nil)
                    }
                }
            }
            Section("Agent Inbox") {
                if store.agentInbox.isEmpty {
                    Text("Empty").foregroundStyle(.secondary)
                } else {
                    ForEach(store.agentInbox) { item in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.name)
                                Text(item.error ?? item.purpose)
                                    .font(.caption)
                                    .foregroundStyle(item.error == nil ? Color.secondary : Color.orange)
                                    .lineLimit(2)
                            }
                            Spacer()
                            Button("Audit") { Task { await store.proposeAgent(item.name) } }
                                .controlSize(.small)
                                .disabled(item.error != nil)
                        }
                    }
                }
            }
            Section("Recognized Workstation agents") {
                if store.agents.isEmpty {
                    Text("None").foregroundStyle(.secondary)
                } else {
                    ForEach(store.agents) { agent in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(agent.name) \(agent.version)")
                            Text("\(agent.origin) · \(agent.purpose)")
                                .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                }
            }
            Section("Installed skills") {
                if store.skills.isEmpty { Text("None").foregroundStyle(.secondary) }
                ForEach(store.skills) { skill in
                    if let flag = skill.flag {
                        HStack {
                            Image(systemName: "exclamationmark.shield.fill").foregroundStyle(.red)
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(skill.name) \(skill.version)")
                                Text("Switched off: \(flag.findings.first?.message ?? "flagged by the Continuous Audit")")
                                    .font(.caption).foregroundStyle(.red).lineLimit(2)
                            }
                            Spacer()
                            Button("Uninstall", role: .destructive) { Task { await store.uninstall(skill.name) } }
                                .controlSize(.small)
                        }
                    } else {
                        Toggle(isOn: Binding(get: { skill.enabled }, set: { on in Task { await store.setEnabled(skill.name, on) } })) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(skill.name) \(skill.version)")
                                Text(skill.status == "approved" ? skill.purpose : "Not loaded: \(skill.status)")
                                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }
                        }
                        .disabled(skill.status != "approved")
                        .contextMenu {
                            Button("Improve with a Developer…") { onBuild(skill.name) }
                                .disabled(skill.status != "approved")
                        }
                    }
                }
            }
            if let s = store.status {
                Section("System") {
                    LabeledContent("Model", value: "\(s.model) · \(s.modelLoaded == true ? "loaded" : "idle")")
                    LabeledContent("Server memory", value: "\(s.serverMemoryMb) MB")
                    LabeledContent("Sandbox (Docker)", value: s.docker ? "running" : "not running")
                    LabeledContent("Skills", value: "\(s.skills.enabled) of \(s.skills.installed) on")
                    if let clock = s.continuousAudit {
                        SecurityCheckRow(clock: clock) { Task { await store.checkNow() } }
                    }
                }
                .font(.callout)
            }
            if let error = store.lastError {
                Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption)
            }
        }
        .listStyle(.sidebar)
    }

    private func stageLabel(_ task: TaskInfo) -> String {
        switch task.stage {
        case "staging": "Creating a shadow project…"
        case "auditing": "Running the Governor's checks…"
        case "awaiting": task.report?.blocked == true ? "Blocked" : "Awaiting your decision"
        case "deployed": "Deployed"
        case "rejected": "Rejected"
        default: "Couldn't finish"
        }
    }
}

/// The Continuous Audit's question, when an installed skill turns red.
/// Work a restart cut short: what it was, what happened, and the user's choices.
struct RecoveryRow: View {
    let item: RecoveryItem
    let store: WorkshopStore

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(item.title, systemImage: "arrow.clockwise.circle")
                .font(.body.weight(.medium))
                .foregroundStyle(.orange)
            Text(item.message).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if item.scratchKept {
                Text("Its working files are kept for a day, for diagnosis.").font(.caption2).foregroundStyle(.tertiary)
            }
            HStack {
                if item.actions.contains("retry") {
                    Button("Retry") { Task { await store.retry(item.id) } }
                }
                Button("Dismiss") { Task { await store.dismissRecovery(item.id) } }
            }
            .buttonStyle(.link)
            .font(.caption)
        }
        .padding(.vertical, 2)
    }
}

struct AlertBanner: View {
    let alert: SkillAlert
    let onKeep: () -> Void
    let onUninstall: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.shield.fill").font(.title2).foregroundStyle(.red)
            VStack(alignment: .leading, spacing: 4) {
                Text(alert.message).font(.callout.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                ForEach(alert.findings, id: \.self) { f in
                    Text(f.detail.isEmpty ? f.message : "\(f.message): \(f.detail)")
                        .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                Text("It's switched off until you decide.").font(.caption).foregroundStyle(.secondary)
            }
            Spacer(minLength: 12)
            Button("Keep Disabled", action: onKeep)
            Button("Uninstall", role: .destructive, action: onUninstall)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.10), in: .rect(cornerRadius: 10))
    }
}

/// When installed skills were last re-checked against PyPI and OSV, and a way to check now.
struct SecurityCheckRow: View {
    let clock: AuditClock
    let onCheck: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text("Security check")
                Spacer()
                if clock.running {
                    ProgressView().controlSize(.small)
                } else {
                    Button("Check now", action: onCheck).controlSize(.small)
                }
            }
            Group {
                if let date = clock.lastChecked {
                    Text("Last checked \(date, format: .relative(presentation: .named))")
                } else {
                    Text("Not checked yet")
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            if let error = clock.lastError {
                Text("Couldn't check: \(error)").font(.caption).foregroundStyle(.orange).lineLimit(2)
            }
        }
    }
}

/// Green / yellow / red, from the rules only.
struct RiskBadge: View {
    let risk: String
    var compact = false

    var body: some View {
        Label(compact ? label.components(separatedBy: " ").first ?? label : label, systemImage: icon)
            .font(compact ? .caption2.weight(.semibold) : .callout.weight(.semibold))
            .padding(.horizontal, compact ? 6 : 10)
            .padding(.vertical, compact ? 2 : 5)
            .foregroundStyle(.white)
            .background(color, in: .capsule)
    }

    private var color: Color { risk == "red" ? .red : risk == "yellow" ? .orange : .green }
    private var icon: String { risk == "red" ? "xmark.shield.fill" : risk == "yellow" ? "exclamationmark.shield.fill" : "checkmark.shield.fill" }
    private var label: String { risk == "red" ? "High risk" : risk == "yellow" ? "Medium risk" : "Low risk" }
}

/// The Audit Card: what the rules verified, what the candidate merely claims, the tests, the diff.
struct AuditCardView: View {
    let task: TaskInfo
    let onDecide: (Bool) -> Void
    let onCancel: () -> Void
    @State private var file = 0

    init(task: TaskInfo, onDecide: @escaping (Bool) -> Void, onCancel: @escaping () -> Void = {}) {
        self.task = task
        self.onDecide = onDecide
        self.onCancel = onCancel
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if let origin = task.origin { BuiltByCard(origin: origin) }
                    if let report = task.report {
                        AuditFindings(report: report)
                        if let diff = task.diff, !diff.isEmpty {
                            ChangesView(diff: diff, file: $file)
                        }
                    } else {
                        auditProgress
                    }
                }
                .padding(20)
            }
            if task.awaitsDecision { Divider(); decisionBar }
        }
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: task.kind == "agent" ? "person.crop.circle.badge.checkmark" : "puzzlepiece.extension.fill")
                .font(.title2).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text("\(task.skill) \(task.report?.version ?? "")").font(.title3.weight(.semibold))
                Text(task.message).font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            if let risk = task.report?.risk { RiskBadge(risk: risk) }
        }
        .padding(20)
    }

    @ViewBuilder
    private var auditProgress: some View {
        if task.isRunning {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    ProgressView().controlSize(.small)
                    Text(task.phase ?? task.message).foregroundStyle(.secondary)
                    Spacer()
                    if let progress = task.progress { Text("\(progress)%").monospacedDigit().foregroundStyle(.secondary) }
                }
                if let progress = task.progress {
                    ProgressView(value: Double(progress), total: 100)
                } else {
                    ProgressView()
                }
                Button("Cancel Audit", role: .destructive, action: onCancel)
                    .controlSize(.small)
                    .disabled(task.phase == "Cancelling")
            }
        } else if task.stage == "failed", let error = task.error {
            Label(error, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange).textSelection(.enabled)
        } else {
            Text(task.message).foregroundStyle(.secondary)
        }
    }

    private var decisionBar: some View {
        HStack {
            if task.report?.blocked == true {
                Label(task.kind == "agent" ? "This agent candidate is blocked and can't be recognized."
                      : "This skill is blocked and can't be deployed.", systemImage: "nosign").foregroundStyle(.red)
            } else {
                Text("Nothing is installed until you approve.").font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
            Button("Approve & Deploy") { onDecide(true) }
                .disabled(task.report?.blocked != false)
            Button("Reject", role: .destructive) { onDecide(false) }
                .keyboardShortcut(.defaultAction)  // Return rejects: deploying takes a deliberate click
        }
        .padding(16)
    }
}

struct AuditFindings: View {
    let report: AuditReportInfo

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Card {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(report.findings, id: \.self) { f in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Image(systemName: icon(f.severity)).foregroundStyle(color(f.severity)).frame(width: 16)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(f.message).font(.callout)
                                if !f.detail.isEmpty {
                                    Text(f.detail).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                                }
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } title: {
                Label("Verified by rules", systemImage: "checkmark.seal").font(.headline)
            }
            Card {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(report.tests, id: \.self) { t in
                        Label(t.name.replacingOccurrences(of: "_", with: " "), systemImage: t.status == "passed" ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(t.status == "passed" ? Color.green : Color.red)
                            .font(.callout)
                    }
                    if let summary = report.suite.summary {
                        Label("Aura's existing suite: \(summary)", systemImage: (report.suite.failed ?? 0) == 0 ? "checkmark.circle" : "xmark.circle")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    if report.tests.isEmpty { Text("Not run (blocked earlier).").font(.callout).foregroundStyle(.secondary) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            } title: {
                Label("Tests (in the sandbox and the shadow project)", systemImage: "testtube.2").font(.headline)
            }
            Card {
                Text(report.purpose.isEmpty ? "No purpose given." : report.purpose)
                    .font(.callout)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } title: {
                Label("Stated purpose (from the manifest; unverified)", systemImage: "quote.bubble").font(.headline)
            }
        }
    }

    private func icon(_ severity: String) -> String {
        switch severity {
        case "pass": "checkmark.circle.fill"
        case "yellow": "exclamationmark.triangle.fill"
        case "red", "reject": "xmark.octagon.fill"
        default: "info.circle"
        }
    }

    private func color(_ severity: String) -> Color {
        severity == "pass" ? .green : severity == "yellow" ? .orange : ["red", "reject"].contains(severity) ? .red : .secondary
    }
}

/// A titled section, drawn in SwiftUI (renders everywhere, matches the glass style).
struct Card<Content: View, Title: View>: View {
    @ViewBuilder let content: Content
    @ViewBuilder let title: Title

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            title
            content
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.05), in: .rect(cornerRadius: 10))
    }
}

/// Side-by-side diff: before on the left, after on the right.
struct ChangesView: View {
    let diff: [FileDiff]
    @Binding var file: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Proposed changes", systemImage: "doc.text.magnifyingglass").font(.headline)
                Spacer()
                Picker("File", selection: $file) {
                    ForEach(Array(diff.enumerated()), id: \.offset) { i, d in Text("\(d.file) (\(d.status))").tag(i) }
                }
                .labelsHidden()
                .frame(maxWidth: 260)
            }
            if diff.indices.contains(file) {
                DiffTable(diff: diff[file])
            }
        }
    }
}

struct DiffTable: View {
    let diff: FileDiff

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 0) {
                Text("Before").frame(maxWidth: .infinity, alignment: .leading)
                Text("After").frame(maxWidth: .infinity, alignment: .leading)
            }
            .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            .padding(.horizontal, 8).padding(.vertical, 4)
            ForEach(Array(diff.rows.enumerated()), id: \.offset) { _, row in
                HStack(spacing: 1) {
                    cell(row.left, highlight: row.kind == "removed" ? .red : row.kind == "changed" ? .orange : nil)
                    cell(row.right, highlight: row.kind == "added" ? .green : row.kind == "changed" ? .orange : nil)
                }
            }
            if diff.status == "binary" {
                Text("Binary file: not shown").font(.caption).foregroundStyle(.secondary).padding(8)
            }
        }
        .font(.system(.caption, design: .monospaced))
        .background(Color.primary.opacity(0.04), in: .rect(cornerRadius: 8))
        .textSelection(.enabled)
    }

    private func cell(_ text: String?, highlight: Color?) -> some View {
        Text(text ?? " ")
            .lineLimit(1)
            .truncationMode(.tail)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 8)
            .padding(.vertical, 1)
            .background(text == nil ? Color.primary.opacity(0.03) : (highlight?.opacity(0.18) ?? Color.clear))
    }
}
