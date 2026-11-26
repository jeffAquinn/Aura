import AuraKit
import SwiftUI

/// A terminal-shaped control surface for approved, Vault-only HOL task sessions.
public struct HOLTerminalView: View {
    let store: HOLStore
    let openWorkshop: () -> Void
    @State private var selectedSession = ""
    @State private var command = ""
    @State private var changeDecisions: [String: Bool] = [:]
    @State private var restoreOriginal = false
    @State private var restoreSavepoint: HOLSavepoint?
    @AppStorage(ChatAppearance.holBubbleKey) private var holBubble = ChatAppearance.defaultAura
    @AppStorage(ChatAppearance.holTextKey) private var holText = ""
    @AppStorage(ChatAppearance.holBubbleTransparencyKey) private var holBubbleTransparency = 0.0
    @AppStorage(ChatAppearance.holTextTransparencyKey) private var holTextTransparency = 0.0
    @AppStorage(ChatAppearance.holBubbleBorderKey) private var holBubbleBorder = ""
    @AppStorage(ChatAppearance.holBubbleBorderTransparencyKey) private var holBubbleBorderTransparency = 1.0
    @AppStorage(ChatAppearance.textPaddingKey) private var textPadding = ChatAppearance.defaultTextPadding

    public init(store: HOLStore, openWorkshop: @escaping () -> Void) {
        self.store = store
        self.openWorkshop = openWorkshop
    }

    public var body: some View {
        AuraThemedPage {
        VStack(spacing: 0) {
            header
            Divider()
            HSplitView {
                controls.frame(minWidth: 290, idealWidth: 330, maxWidth: 380)
                terminal
            }
        }
        .frame(minWidth: 840, minHeight: 560)
        .task { await store.refresh() }
        .confirmationDialog("Restore the original Aura code?", isPresented: $restoreOriginal, titleVisibility: .visible) {
            Button("Restore clean Git HEAD", role: .destructive) { Task { await store.restoreOriginal() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This replaces Aura_Vault/Aura with the downloaded checkout’s clean Git HEAD. All modifications in that Vault copy will be lost. The Git checkout itself is never changed.")
        }
        .confirmationDialog("Restore this savepoint?", isPresented: Binding(get: { restoreSavepoint != nil }, set: { if !$0 { restoreSavepoint = nil } }), titleVisibility: .visible) {
            Button("Restore", role: .destructive) {
                if let point = restoreSavepoint { Task { await store.restore(point) } }
                restoreSavepoint = nil
            }
            Button("Cancel", role: .cancel) { restoreSavepoint = nil }
        } message: {
            Text("The current Vault Aura copy will be replaced by this savepoint. The Git checkout is unaffected.")
        }
        }
    }

    private var header: some View {
        HStack {
            Label("HOL Terminal", systemImage: "terminal.fill").font(.title3.weight(.semibold))
            coherenceBadge
            Spacer()
            Text("Vault-only container").font(.caption.weight(.medium)).padding(.horizontal, 8).padding(.vertical, 4)
                .background(.green.opacity(0.15), in: Capsule()).foregroundStyle(.green)
            if store.terminal?.running == true {
                Button("Stop session", role: .destructive) { Task { await store.stopTerminal() } }
                    .disabled(store.busy)
            }
            Button { Task { await store.refresh() } } label: { Image(systemName: "arrow.clockwise") }
                .disabled(store.busy)
        }
        .padding()
    }

    @ViewBuilder
    private var coherenceBadge: some View {
        switch store.coherence {
        case .complete:
            Text("Synchronized").font(.caption.weight(.medium)).foregroundStyle(.green)
        case .connecting:
            Text("Connecting").font(.caption.weight(.medium)).foregroundStyle(.secondary)
        case .refreshing:
            Label("Refreshing", systemImage: "arrow.triangle.2.circlepath")
                .font(.caption.weight(.medium)).foregroundStyle(.secondary)
        case .degraded:
            Label("Partial refresh", systemImage: "exclamationmark.triangle.fill")
                .font(.caption.weight(.medium)).foregroundStyle(.orange)
                .help("Session data or the model catalog could not be refreshed. Model choices from an earlier refresh are withheld.")
        case .unavailable:
            Label("Refresh unavailable", systemImage: "exclamationmark.octagon.fill")
                .font(.caption.weight(.medium)).foregroundStyle(.red)
                .help("Aura could not refresh HOL session data or the model catalog.")
        }
    }

    private var controls: some View {
        Form {
            Section("Working directory") {
                Text(store.overview?.vaultProject ?? "Loading Vault…")
                    .font(.caption.monospaced()).textSelection(.enabled).lineLimit(3)
                Text("The shell runs in a disposable, no-network Docker container. The downloaded Git checkout is not mounted.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section("Session") {
                if store.overview?.sessions.isEmpty ?? true {
                    Text("Aura Session Review is waiting in the Workshop Inbox. Review and deploy it before it can run.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Open Workshop…", action: openWorkshop)
                } else {
                    Picker("Approved session", selection: $selectedSession) {
                        Text("Choose a session").tag("")
                        ForEach(store.overview?.sessions ?? []) { Text($0.name).tag($0.name) }
                    }
                    if let session = session {
                        Text(session.purpose).font(.caption).foregroundStyle(.secondary)
                        Button("Prepare Session") { Task { await store.start(session) } }
                            .disabled(store.busy || selectedEngineProblem != nil)
                    }
                    Button("Create a session in Workshop…", action: openWorkshop)
                        .font(.caption)
                    Text("New sessions are declarative Aura skills. Workshop audit and deployment are required before they appear here.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Section("Model") {
                Picker("Connected coding model", selection: selectedEngine) {
                    Text("Offline HOL shell only").tag(Optional<String>.none)
                    ForEach(store.overview?.developers ?? []) { engine in
                        Text(engine.problem == nil ? engine.label : "\(engine.label) — unavailable")
                            .tag(Optional(engine.name))
                            .disabled(engine.problem != nil)
                    }
                }
                if let problem = selectedEngineProblem {
                    Text(problem).font(.caption).foregroundStyle(.orange)
                } else if store.selectedEngine != nil {
                    Text("Uses Aura's isolated connected Developer container. Credentials never enter the HOL shell.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Picker("Provider", selection: provider) {
                    Text("Local (Ollama)").tag("ollama")
                    Text("Cloud (Anthropic)").tag("anthropic")
                }
                Picker("Model", selection: model) {
                    ForEach(models, id: \.self) { Text($0).tag($0) }
                }
                Picker("Effort", selection: effort) {
                    ForEach(["none", "low", "medium", "high"], id: \.self) { Text($0.capitalized).tag($0) }
                }
            }
            Section("Savepoints") {
                Button("Save point before changes") { Task { await store.savepoint() } }.disabled(store.busy)
                if let points = store.overview?.savepoints, !points.isEmpty {
                    ForEach(points.reversed()) { point in
                        Button("Restore: \(point.label)", role: .destructive) { restoreSavepoint = point }
                            .font(.caption)
                    }
                }
                Text("Aura keeps the last three Vault Aura snapshots.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Restore Original…", role: .destructive) { restoreOriginal = true }
                    .disabled(store.busy)
            }
            if let error = store.lastError {
                Section { Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption) }
            }
        }
        .formStyle(.grouped)
    }

    private var terminal: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text("Human Out of Loop").font(.headline).foregroundStyle(.green)
                Text(store.overview?.notice ?? "Connecting to Aura…").foregroundStyle(.secondary)
                Divider().overlay(.white.opacity(0.2))
                preflight
                if store.output.isEmpty {
                    Text(readiness.emptyOutputMessage).foregroundStyle(.secondary)
                } else {
                    ForEach(Array(store.output.enumerated()), id: \.offset) { _, line in
                        Text(line)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                            .lineSpacing(3)
                            .padding(.horizontal, ChatAppearance.textPadding(textPadding))
                            .padding(.vertical, ChatAppearance.verticalTextPadding(textPadding))
                            .foregroundStyle(holColors.auraText)
                            .background(holColors.auraBackground, in: .rect(cornerRadius: 12))
                            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(holColors.auraBorder, lineWidth: 1))
                    }
                }
                if !store.pendingChanges.isEmpty { changes }
            }
            .font(.system(.body, design: .monospaced))
            .padding()
        }
        .background(Color.black.opacity(0.88))
        .safeAreaInset(edge: .bottom) { commandBar }
    }

    private var commandBar: some View {
        HStack(spacing: 10) {
            Text(store.activeEngine == nil ? "$ " : "› ").font(.system(.body, design: .monospaced)).foregroundStyle(.green)
            TextField(store.activeEngine == nil ? "Enter a command for the Vault container" : "Message the selected assistant", text: $command)
                .textFieldStyle(.plain)
                .font(.system(.body, design: .monospaced))
                .onSubmit { send() }
                .disabled(store.terminal?.running != true || !store.pendingChanges.isEmpty || store.busy)
            Button("Send", action: send)
                .disabled(command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.terminal?.running != true || !store.pendingChanges.isEmpty || store.busy)
        }
        .padding(12)
        .background(.bar)
    }

    /// This remains visible before terminal output arrives: it makes the execution boundary and
    /// input state explicit instead of making a newly prepared session look stalled.
    private var preflight: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(readiness.title, systemImage: readiness.symbol)
                    .font(.headline)
                    .foregroundStyle(readiness.tint)
                Spacer()
                Text(readiness.badge).font(.caption.weight(.bold)).foregroundStyle(readiness.tint)
            }
            Text(readiness.instruction).font(.caption).foregroundStyle(.white.opacity(0.78))
            Divider().overlay(.white.opacity(0.18))
            boundaryRow("Mount point", "/vault (full Aura_Vault)", systemImage: "folder")
            boundaryRow("Working directory", "/vault/Aura", systemImage: "arrow.turn.down.right")
            boundaryRow("Network", "Disabled in this container", systemImage: "wifi.slash")
            boundaryRow("Host checkout", "Not mounted or writable", systemImage: "lock.fill")
            if let id = readiness.terminalID {
                boundaryRow("Container", "aura-hol-\(id)", systemImage: "cube.transparent")
            }
        }
        .padding(12)
        .background(readiness.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(readiness.tint.opacity(0.35)))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("HOL pre-flight. \(readiness.title). \(readiness.instruction). The full Aura Vault is mounted at slash vault. Network is disabled in the container.")
    }

    private func boundaryRow(_ label: String, _ detail: String, systemImage: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 7) {
            Image(systemName: systemImage).frame(width: 15).foregroundStyle(.white.opacity(0.7))
            Text("\(label):").foregroundStyle(.white.opacity(0.7))
            Text(detail).foregroundStyle(.white.opacity(0.95))
        }
        .font(.caption.monospaced())
    }

    private func send() {
        let sent = command
        command = ""
        Task {
            if store.activeEngine == nil { await store.send(sent) }
            else { await store.sendMessage(sent) }
        }
    }

    private var changes: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider().overlay(.white.opacity(0.2))
            Text("Review pending file changes").font(.headline).foregroundStyle(.orange)
            Text("Nothing is written to Aura_Vault until you choose each file below.")
                .font(.caption).foregroundStyle(.white.opacity(0.75))
            ForEach(store.pendingChanges) { change in
                Toggle(isOn: Binding(get: { changeDecisions[change.id] ?? false }, set: { changeDecisions[change.id] = $0 })) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(change.description).fixedSize(horizontal: false, vertical: true)
                        Text("Default: deny").font(.caption2).foregroundStyle(.white.opacity(0.6))
                    }
                }
                .toggleStyle(.switch)
            }
            Button("Apply selected changes") {
                let allowed = store.pendingChanges.map { changeDecisions[$0.id] ?? false }
                changeDecisions = [:]
                Task { await store.decideChanges(allowed) }
            }
            .disabled(store.busy)
        }
        .padding(12)
        .background(.orange.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
    }

    private var session: HOLSession? {
        (store.overview?.sessions ?? []).first { $0.name == selectedSession }
    }

    private var models: [String] {
        guard let catalog = store.catalog else { return store.selectedModel.model.isEmpty ? [""] : [store.selectedModel.model] }
        let known = store.selectedModel.provider == "anthropic" ? catalog.anthropicModels : catalog.ollamaModels
        return known.contains(store.selectedModel.model) ? known : [store.selectedModel.model] + known
    }

    private var provider: Binding<String> {
        Binding(get: { store.selectedModel.provider }, set: { value in
            store.selectedModel.provider = value
            store.selectedModel.model = (value == "anthropic" ? store.catalog?.anthropicModels : store.catalog?.ollamaModels)?.first ?? ""
        })
    }

    private var model: Binding<String> {
        Binding(get: { store.selectedModel.model }, set: { store.selectedModel.model = $0 })
    }

    private var effort: Binding<String> {
        Binding(get: { store.effort }, set: { store.effort = $0 })
    }

    private var selectedEngine: Binding<String?> {
        Binding(get: { store.selectedEngine }, set: { store.selectedEngine = $0 })
    }

    private var selectedEngineProblem: String? {
        guard let selected = store.selectedEngine else { return nil }
        guard let engine = store.overview?.developers.first(where: { $0.name == selected }) else {
            return "This connected coding model is unavailable."
        }
        return engine.problem
    }

    private var readiness: HOLTerminalReadiness {
        HOLTerminalReadiness(overview: store.overview, terminal: store.terminal, busy: store.busy)
    }

    private var holColors: BubbleColors {
        ChatAppearance.bubbles(user: "", aura: holBubble, userText: "", auraText: holText,
                               bubbleTransparency: holBubbleTransparency, textTransparency: holTextTransparency,
                               userBorder: "", auraBorder: holBubbleBorder,
                               auraBubbleBorderTransparency: holBubbleBorderTransparency)
    }
}

/// Presentation-only state for the terminal's pre-flight card.  It intentionally derives from
/// the existing server state; it neither starts a shell nor changes any authority boundary.
enum HOLTerminalReadiness: Equatable {
    case connecting
    case approvalRequired
    case preparing
    case standby
    case active(String)
    case stopped

    init(overview: HOLOverview?, terminal: HOLTerminalState?, busy: Bool) {
        if let terminal {
            self = terminal.running ? .active(terminal.id) : .stopped
        } else if overview == nil {
            self = .connecting
        } else if overview?.sessions.isEmpty == true {
            self = .approvalRequired
        } else if busy {
            self = .preparing
        } else {
            self = .standby
        }
    }

    var title: String {
        switch self {
        case .connecting: "Connecting to Aura"
        case .approvalRequired: "Session approval required"
        case .preparing: "Preparing container"
        case .standby: "System ready — standby"
        case .active: "Container active"
        case .stopped: "Container stopped"
        }
    }

    var badge: String {
        switch self {
        case .connecting: "CONNECTING"
        case .approvalRequired: "BLOCKED"
        case .preparing: "STARTING"
        case .standby: "STANDBY"
        case .active: "INPUT READY"
        case .stopped: "STOPPED"
        }
    }

    var symbol: String {
        switch self {
        case .connecting: "arrow.triangle.2.circlepath"
        case .approvalRequired: "checkmark.shield"
        case .preparing: "hourglass"
        case .standby: "checkmark.circle"
        case .active: "play.circle.fill"
        case .stopped: "stop.circle"
        }
    }

    var tint: Color {
        switch self {
        case .active, .standby: .green
        case .approvalRequired, .preparing: .orange
        case .connecting: .secondary
        case .stopped: .red
        }
    }

    var instruction: String {
        switch self {
        case .connecting: "Loading approved sessions and the model catalog."
        case .approvalRequired: "Review and deploy a declarative session in Workshop before it can run."
        case .preparing: "Starting a disposable container; command input will enable when its stream connects."
        case .standby: "Choose an approved session and prepare it to start a disposable container."
        case .active: "System ready. Command input is connected only to this Vault container."
        case .stopped: "This container is stopped. Prepare an approved session to start a new one."
        }
    }

    var emptyOutputMessage: String {
        switch self {
        case .active: "The container is ready; waiting for its first output."
        default: instruction
        }
    }

    var terminalID: String? {
        if case let .active(id) = self { id } else { nil }
    }
}
