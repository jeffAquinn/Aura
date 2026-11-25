import AuraKit
import SwiftUI

/// A terminal-shaped control surface for approved, Vault-only HOL task sessions.
public struct HOLTerminalView: View {
    let store: HOLStore
    let openWorkshop: () -> Void
    @State private var selectedSession = ""
    @State private var command = ""
    @State private var restoreOriginal = false
    @State private var restoreSavepoint: HOLSavepoint?

    public init(store: HOLStore, openWorkshop: @escaping () -> Void) {
        self.store = store
        self.openWorkshop = openWorkshop
    }

    public var body: some View {
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

    private var header: some View {
        HStack {
            Label("HOL Terminal", systemImage: "terminal.fill").font(.title3.weight(.semibold))
            Spacer()
            Text("Vault-only").font(.caption.weight(.medium)).padding(.horizontal, 8).padding(.vertical, 4)
                .background(.green.opacity(0.15), in: Capsule()).foregroundStyle(.green)
            Button { Task { await store.refresh() } } label: { Image(systemName: "arrow.clockwise") }
                .disabled(store.busy)
        }
        .padding()
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
                            .disabled(store.busy)
                    }
                    Button("Create a session in Workshop…", action: openWorkshop)
                        .font(.caption)
                    Text("New sessions are declarative Aura skills. Workshop audit and deployment are required before they appear here.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            Section("Model") {
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
                if store.output.isEmpty {
                    Text("Choose an approved session to start its disposable container.").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(store.output.enumerated()), id: \.offset) { _, line in
                        Text(line).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
            .font(.system(.body, design: .monospaced))
            .foregroundStyle(.white.opacity(0.92))
            .padding()
        }
        .background(Color.black.opacity(0.88))
        .safeAreaInset(edge: .bottom) { commandBar }
    }

    private var commandBar: some View {
        HStack(spacing: 10) {
            Text("$ ").font(.system(.body, design: .monospaced)).foregroundStyle(.green)
            TextField("Enter a command for the Vault container", text: $command)
                .textFieldStyle(.plain)
                .font(.system(.body, design: .monospaced))
                .onSubmit { send() }
                .disabled(store.terminal?.running != true)
            Button("Send", action: send)
                .disabled(command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.terminal?.running != true)
            Button("Stop", role: .destructive) { Task { await store.stopTerminal() } }
                .disabled(store.terminal?.running != true)
        }
        .padding(12)
        .background(.bar)
    }

    private func send() {
        let sent = command
        command = ""
        Task { await store.send(sent) }
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
}
