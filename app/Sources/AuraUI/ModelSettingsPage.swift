import AuraKit
import SwiftUI

/// The Models window's Model settings page (step 6 of docs/models-window.md; docs/model-settings.md):
/// pick any model Aura knows and set its own values. A blank field isn't sent, so the model's own
/// default applies. A setting its provider doesn't accept is greyed out with a short note (Aura's
/// revision). Use recommended fills in the model's recommended values; nothing changes until Save.
struct ModelSettingsPage: View {
    let store: ChatStore
    let catalog: ModelCatalog
    let navigation: ModelsNavigation
    @State private var selected = ""  // "provider|model"
    @State private var info: ModelSettingsInfo?
    @State private var draft: [String: String] = [:]
    @State private var saving = false
    @State private var message: String?
    @State private var failed = false

    /// `initial`: a model's settings to start from (snapshot tests; the app loads them).
    init(store: ChatStore, catalog: ModelCatalog, navigation: ModelsNavigation, initial: ModelSettingsInfo? = nil) {
        self.store = store
        self.catalog = catalog
        self.navigation = navigation
        _info = State(initialValue: initial)
        _draft = State(initialValue: Self.texts(initial?.saved ?? [:]))
        _selected = State(initialValue: initial.map { "\($0.provider)|\($0.model)" } ?? "")
    }

    /// The settings, in the order shown: (name, label, what it does, whole number).
    static let fields: [(name: String, label: String, help: String, integer: Bool)] = [
        ("temperature", "Temperature", "How varied replies are. Lower is steadier.", false),
        ("top_p", "Top P", "Keeps the most likely words up to this share.", false),
        ("top_k", "Top K", "Considers only this many likely words.", true),
        ("min_p", "Min P", "Drops words far less likely than the best one.", false),
        ("presence_penalty", "Presence penalty", "Nudges toward new topics.", false),
        ("repeat_penalty", "Repeat penalty", "Discourages repeating itself.", false),
        ("effort", "Thinking effort", "How much the model reasons before answering.", false),
        ("context_budget", "Context budget", "Tokens of conversation Aura keeps before summarizing.", true),
        ("max_reply_tokens", "Reply length", "The longest reply, in tokens.", true),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ModelsCard("Model", symbol: "cube") {
                Picker("Model", selection: $selected) {
                    ForEach(Self.groups(catalog), id: \.provider) { group in
                        Section(group.title) {
                            ForEach(group.models, id: \.self) { Text($0).tag("\(group.provider)|\($0)") }
                        }
                    }
                }
                .labelsHidden()
                Text("Settings belong to a model, so every role that uses it shares them. A blank field isn't sent: the model's own default applies.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if let info {
                ModelsCard("Settings", symbol: "slider.horizontal.3") {
                    ForEach(Self.fields, id: \.name) { field in
                        SettingRow(field: field, info: info, text: binding(field.name))
                    }
                }
                HStack {
                    Button(saving ? "Saving…" : "Save") { save(parsed()) }
                        .keyboardShortcut(.defaultAction)
                        .disabled(saving)
                    if !info.recommended.isEmpty {
                        Button("Use recommended") { draft = Self.texts(info.recommended) }
                            .disabled(saving)
                            .help("Fills in this model's recommended values; nothing changes until you save.")
                    }
                    Button("Reset to defaults") { save([:]) }
                        .disabled(saving || info.saved.isEmpty)
                        .help(info.recommended.isEmpty ? "Forgets these settings: the model's own defaults apply."
                              : "Forgets these settings: the recommended values apply again.")
                    Spacer()
                    if let message {
                        Label(message, systemImage: failed ? "exclamationmark.triangle" : "checkmark.circle")
                            .font(.caption).foregroundStyle(failed ? Color.orange : .green)
                    }
                }
            } else {
                Text(store.transport == nil ? "Aura is offline." : "Loading this model's settings…").foregroundStyle(.secondary)
            }
        }
        .onAppear {
            if selected.isEmpty { selected = Self.tag(navigation.settingsModel ?? catalog.roles["chat"] ?? catalog.roles["startup"]) }
        }
        .onChange(of: navigation.settingsModel) { _, model in
            if let model { selected = Self.tag(model) }
        }
        .task(id: selected) {
            if info.map({ Self.tag(ModelChoice(provider: $0.provider, model: $0.model)) }) != selected { await load() }
        }
    }

    private var choice: ModelChoice? {
        let parts = selected.split(separator: "|", maxSplits: 1).map(String.init)
        return parts.count == 2 ? ModelChoice(provider: parts[0], model: parts[1]) : nil
    }

    private func binding(_ name: String) -> Binding<String> {
        Binding(get: { draft[name] ?? "" }, set: { draft[name] = $0 })
    }

    private func load() async {
        guard let choice else { return }
        message = nil
        info = await store.modelSettings(choice)
        draft = Self.texts(info?.saved ?? [:])
    }

    /// The draft as values to send: blank fields left out; numbers as numbers (the server checks ranges).
    private func parsed() -> [String: JSONValue] {
        var out: [String: JSONValue] = [:]
        for (name, text) in draft {
            let value = text.trimmingCharacters(in: .whitespaces)
            guard !value.isEmpty, info?.accepted.contains(name) == true else { continue }
            out[name] = name == "effort" ? .string(value) : Double(value).map(JSONValue.number) ?? .string(value)
        }
        return out
    }

    private func save(_ values: [String: JSONValue]) {
        guard let choice else { return }
        saving = true
        Task {
            switch await store.saveModelSettings(choice, values) {
            case let .success(saved):
                info = saved
                draft = Self.texts(saved.saved)
                (message, failed) = (values.isEmpty ? "Back to defaults." : "Saved.", false)
            case let .failure(error):
                (message, failed) = (error.message, true)
            }
            saving = false
        }
    }

    static func tag(_ choice: ModelChoice?) -> String {
        choice.map { "\($0.provider)|\($0.model)" } ?? ""
    }

    static func texts(_ values: [String: JSONValue]) -> [String: String] {
        values.compactMapValues { value in
            switch value {
            case let .string(text): text
            case let .number(n): n.rounded() == n && abs(n) < 1e9 ? String(Int(n)) : String(n)
            default: nil
            }
        }
    }

    /// Every model Aura knows, by provider: the roles' choices and what's installed or available.
    static func groups(_ catalog: ModelCatalog) -> [(provider: String, title: String, models: [String])] {
        [("ollama", "Ollama (local)"), ("llamacpp", "llama.cpp (local)"), ("anthropic", "Anthropic (cloud)")].compactMap { provider, title in
            let models = Set(catalog.models(for: provider) + catalog.roles.values.filter { $0.provider == provider }.map(\.model))
            return models.isEmpty ? nil : (provider, title, models.sorted())
        }
    }
}

/// One setting: its name and what it does, its range, and the field (or, for effort, a menu).
/// Greyed out, with a note, when the model's provider doesn't accept it.
struct SettingRow: View {
    let field: (name: String, label: String, help: String, integer: Bool)
    let info: ModelSettingsInfo
    @Binding var text: String

    private var accepted: Bool { info.accepted.contains(field.name) }

    private var placeholder: String {
        if let value = info.effective[field.name] { return ModelSettingsPage.texts([field.name: value]).values.first ?? "" }
        return "Default"
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text(field.label).font(.callout.weight(.medium))
                Text(accepted ? field.help : "Not accepted by \(provider) models.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if let range = info.ranges[field.name], accepted {
                Text("\(Self.number(range[0]))–\(Self.number(range[1]))").font(.caption2).monospacedDigit().foregroundStyle(.tertiary)
            }
            if field.name == "effort" {
                Picker(field.label, selection: $text) {
                    Text("Default").tag("")
                    ForEach(info.efforts, id: \.self) { Text($0.capitalized).tag($0) }
                }
                .labelsHidden()
                .frame(width: 120)
            } else {
                TextField(placeholder, text: $text)
                    .textFieldStyle(.roundedBorder)
                    .multilineTextAlignment(.trailing)
                    .frame(width: 110)
            }
        }
        .disabled(!accepted)
        .opacity(accepted ? 1 : 0.45)
    }

    private var provider: String {
        switch info.provider {
        case "anthropic": "Claude"
        case "llamacpp": "llama.cpp"
        default: "Ollama"
        }
    }

    static func number(_ n: Double) -> String {
        n.rounded() == n ? n.formatted(.number.grouping(.automatic)) : String(n)
    }
}
