import AuraKit
import SwiftUI

/// The Models window's Roles page (step 2 of docs/models-window.md): the model each role uses and
/// the Developer for coding, moved here from Settings → Models with their behavior unchanged.
/// A pick saves itself shortly after (a provider switch changes both provider and model, so the
/// two updates settle into one), and a newer pick made while saving is saved next.
struct RoleChoices: View {
    let store: ChatStore
    let catalog: ModelCatalog
    @State private var roles: [String: ModelChoice] = [:]
    /// The last role map the server confirmed.
    @State private var savedRoles: [String: ModelChoice] = [:]
    @State private var saveGeneration = 0
    @State private var saving = false
    @State private var error: String?

    /// Coding is an account-backed Developer setting, not an Aura cloud-chat model role.
    static let order = ["startup", "chat", "web_search", "deep_research"]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Self.order, id: \.self) { role in
                ModelRoleRow(title: catalog.roleLabels[role] ?? role, choice: binding(role), catalog: catalog,
                             isStartup: role == "startup")
            }
            DeveloperCodingRow(store: store, engine: catalog.codingEngine)
            if let problem = catalog.startupModelProblem {
                WarningLabel(problem)
            }
            if let error {
                WarningLabel(error)
            }
            HStack {
                Text(saving ? "Saving model choices…" : "Model choices save automatically.")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Save now") { saveNow() }.disabled(saving || roles.count != savedRoles.count || roles.isEmpty)
            }
            .controlSize(.small)
        }
        .onChange(of: catalog) { sync() }
        .onChange(of: roles) { _, _ in scheduleSave() }
        .onAppear(perform: sync)
    }

    private func binding(_ role: String) -> Binding<ModelChoice> {
        Binding(
            get: { roles[role] ?? catalog.roles[role] ?? ModelChoice(provider: "ollama", model: "") },
            set: { roles[role] = $0 }
        )
    }

    private func sync() {
        roles = catalog.roles
        savedRoles = catalog.roles
    }

    private func scheduleSave() {
        guard roles.count == savedRoles.count, roles != savedRoles else { return }
        saveGeneration += 1
        let generation = saveGeneration
        Task {
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard generation == saveGeneration else { return }
            await persist(roles)
        }
    }

    private func saveNow() {
        saveGeneration += 1  // supersede any queued automatic save
        Task { await persist(roles) }
    }

    private func persist(_ requested: [String: ModelChoice]) async {
        guard requested.count == savedRoles.count else { return }
        saving = true
        error = await store.updateModels(requested)
        if error == nil { savedRoles = requested }
        saving = false
        // A newer selection made while the request was in flight is saved next; a rejected value
        // isn't retried by itself, so the user sees the validation message.
        if roles != requested { scheduleSave() }
    }
}

struct ModelRoleRow: View {
    let title: String
    @Binding var choice: ModelChoice
    let catalog: ModelCatalog
    let isStartup: Bool

    private var suggestions: [String] {
        catalog.models(for: choice.provider)
    }

    private var menuTitle: String {
        switch choice.provider {
        case "anthropic": "Available cloud models"
        case "llamacpp": "Served by llama-server"
        default: "Installed local models"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 5) {
                Text(title).font(.callout.weight(.medium))
                if isStartup {
                    Label("Launch default", systemImage: "power")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.tint)
                }
            }
            HStack {
                Picker("Provider", selection: $choice.provider) {
                    ForEach(ModelProviders.all, id: \.id) { Text($0.title).tag($0.id) }
                }
                .labelsHidden()
                .onChange(of: choice.provider) { provider in
                    choice.model = catalog.models(for: provider).first ?? ""
                }
                TextField("Model", text: $choice.model)
                    .textFieldStyle(.roundedBorder)
                    .frame(minWidth: 180)
            }
            if choice.provider == "llamacpp" && suggestions.isEmpty {
                Text("No llama.cpp models found. Install llama.cpp (brew install llama.cpp) and put .gguf model files in ~/models; Aura starts and stops llama-server itself.")
                    .font(.caption).foregroundStyle(.orange)
            }
            if !suggestions.isEmpty {
                Menu(menuTitle) {
                    ForEach(suggestions, id: \.self) { model in
                        Button(model) { choice.model = model }
                    }
                }
                .font(.caption).controlSize(.small)
            }
        }
        .padding(8)
        .background(isStartup ? Color.accentColor.opacity(0.10) : .clear, in: RoundedRectangle(cornerRadius: 8))
    }
}

struct DeveloperCodingRow: View {
    let store: ChatStore
    let engine: String
    @State private var saving = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Coding").font(.callout.weight(.medium))
            Picker("Coding developer", selection: Binding(get: { engine }, set: save)) {
                Text("Claude Code (your Claude plan)").tag("claude")
                Text("Codex (your ChatGPT plan)").tag("codex")
            }
            .disabled(saving)
            Text("Code-change requests use this connected Developer account after Aura presents its approval card. They do not use an Anthropic cloud-chat model.")
                .font(.caption).foregroundStyle(.secondary)
            if let error { WarningLabel(error) }
        }
        .padding(8)
        .background(Color.accentColor.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
    }

    private func save(_ selected: String) {
        saving = true
        error = nil
        Task {
            error = await store.updateCodingEngine(selected)
            saving = false
        }
    }
}
