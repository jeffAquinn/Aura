import AppKit
import AuraKit
import SwiftUI

/// Voice, appearance, persona and keys. Changes made here are the user acting directly: no approval.
struct SettingsView: View {
    let store: ChatStore
    @AppStorage(Theme.key) private var theme = Theme.glass.rawValue
    @State private var speed = 1.0
    @State private var pitch = 1.0
    @State private var volume = 0.9
    @State private var confirmReset = false

    var body: some View {
        Form {
            Section("Voice") {
                Toggle("Speak replies", isOn: Binding(
                    get: { store.voice.speak },
                    set: { on in Task { await store.updateVoice(VoicePatch(speak: on)) } }))
                Picker("Voice", selection: Binding(
                    get: { store.voice.voice },
                    set: { id in Task { await store.updateVoice(VoicePatch(voice: id)) } })) {
                    Text("System default").tag(String?.none)
                    ForEach(VoiceGroups(store.voices).sections, id: \.title) { section in
                        Section(section.title) {
                            ForEach(section.voices) { v in Text(VoiceGroups.label(v)).tag(Optional(v.id)) }
                        }
                    }
                }
                slider("Speed", $speed, 0.5...2.0) { VoicePatch(speed: $0) }
                slider("Pitch", $pitch, 0.5...2.0) { VoicePatch(pitch: $0) }
                slider("Volume", $volume, 0.1...1.0) { VoicePatch(volume: $0) }
                HStack {
                    Button("Preview") { store.speech?.preview("Hello, I'm \(store.persona.name). This is how I sound.") }
                    Spacer()
                    Link("Get higher-quality voices…", destination: Self.spokenContentSettings).font(.caption)
                }
            }
            Section("Appearance") {
                Picker("Theme", selection: $theme) {
                    ForEach(Theme.allCases, id: \.rawValue) { Text($0.label).tag($0.rawValue) }
                }
                .pickerStyle(.segmented)
                ChatAppearanceSettings()
            }
            UserProfileSection()
            Section("Persona") {
                LabeledContent("Name", value: store.persona.name)
                if !store.persona.tone.isEmpty {
                    LabeledContent("Tone") { Text(store.persona.tone).foregroundStyle(.secondary).lineLimit(3) }
                }
                ForEach(store.persona.constraints, id: \.self) { Label($0, systemImage: "checkmark.circle").font(.callout) }
                if let error = store.persona.error {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).font(.caption)
                }
                HStack {
                    Text(store.persona.history == 1 ? "1 change so far" : "\(store.persona.history) changes so far")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Reset to default", role: .destructive) { confirmReset = true }
                }
                Text("Ask \(store.persona.name) to change its tone, brevity or style; you approve each change.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ModelsSection(store: store)
            DeveloperAccountsSection(store: store)
            ConnectionsSection(store: store)
            KeysSection(store: store)
        }
        .formStyle(.grouped)
        .frame(width: 430, height: 760)
        .onAppear(perform: syncSliders)
        .task { await store.loadSecrets() }
        .onChange(of: store.voice) { syncSliders() }
        .confirmationDialog("Reset \(store.persona.name)'s persona to the default?", isPresented: $confirmReset) {
            Button("Reset", role: .destructive) { Task { await store.resetPersona() } }
        }
        .background { AuraThemeBackdrop() }
    }

    static let spokenContentSettings = URL(string: "x-apple.systempreferences:com.apple.preference.universalaccess?SpokenContent")!

    private func syncSliders() {
        speed = store.voice.speed
        pitch = store.voice.pitch
        volume = store.voice.volume
    }

    /// Sends one update when the user lets go, not one per pixel of drag.
    private func slider(_ title: String, _ value: Binding<Double>, _ range: ClosedRange<Double>,
                        patch: @escaping (Double) -> VoicePatch) -> some View {
        LabeledContent(title) {
            Slider(value: value, in: range) { editing in
                if !editing { Task { await store.updateVoice(patch(value.wrappedValue)) } }
            }
        }
    }
}

/// The coding harnesses deliberately use the user's own Claude and ChatGPT sign-ins, not the
/// Anthropic cloud-chat key or a selectable cloud model. A check sends only a fixed prompt to an
/// empty read-only container, so it is safe to run before asking either one to change files.
struct DeveloperAccountsSection: View {
    let store: ChatStore
    @State private var checking: String?
    @State private var error: String?

    private var engines: [EngineInfo] { store.developerInfo?.engines ?? EngineInfo.known }

    var body: some View {
        Section("Claude Code & Codex") {
            Text("Coding requests use your connected Claude Code or Codex account in Aura’s isolated Developer container. Cloud chat models, including unavailable ones such as Fable, are not used here.")
                .font(.caption).foregroundStyle(.secondary)
            ForEach(engines) { engine in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(engine.label).font(.body.weight(.medium))
                        Spacer()
                        Button(checking == engine.name ? "Checking…" : "Check account") { check(engine) }
                            .disabled(checking != nil || engine.problem != nil)
                    }
                    Text(engine.plan).font(.caption).foregroundStyle(.secondary)
                    if let problem = engine.problem {
                        Text(problem).font(.caption).foregroundStyle(.orange)
                    } else if let message = engine.checkMessage {
                        Label(message, systemImage: engine.checkState == "verified" ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                            .font(.caption).foregroundStyle(engine.checkState == "verified" ? .green : .orange)
                    } else {
                        Text("Configured; run Check account to verify current plan access.").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if let error { Label(error, systemImage: "xmark.octagon").font(.caption).foregroundStyle(.red) }
        }
    }

    private func check(_ engine: EngineInfo) {
        checking = engine.name
        error = nil
        Task {
            error = await store.checkDeveloper(engine.name)
            checking = nil
        }
    }
}

/// The user's identity is local application preference, independent of Aura's editable persona.
struct UserProfileSection: View {
    @AppStorage(UserProfile.nameKey) private var name = ""
    @AppStorage(UserProfile.avatarPathKey) private var avatarPath = ""
    @AppStorage(UserProfile.backgroundPathKey) private var backgroundPath = ""
    @AppStorage(Theme.key) private var theme = Theme.glass.rawValue
    @State private var error: String?

    var body: some View {
        Section("Your Profile") {
            HStack(spacing: 10) {
                UserAvatarView(size: 44)
                TextField("Display name", text: $name, prompt: Text("Your name"))
                    .onChange(of: name) { _, value in if value.count > 80 { name = String(value.prefix(80)) } }
            }
            Text("This name and avatar identify you locally in Aura. They are not sent to a model or stored in Aura_Vault.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Choose Avatar…") { choose("avatar") }
                if !avatarPath.isEmpty { Button("Remove", role: .destructive) { avatarPath = "" } }
            }
            HStack {
                Button("Choose Background…") { choose("background") }
                if !backgroundPath.isEmpty { Button("Remove", role: .destructive) { backgroundPath = ""; if theme == Theme.custom.rawValue { theme = Theme.glass.rawValue } } }
            }
            Text("Images are copied to Aura’s local Application Support folder (20 MB maximum). Custom backgrounds receive a dark contrast overlay.")
                .font(.caption).foregroundStyle(.secondary)
            if let error { Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
        }
    }

    private func choose(_ kind: String) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let path = try UserProfile.importImage(url, named: kind)
            if kind == "avatar" { avatarPath = path } else { backgroundPath = path; theme = Theme.custom.rawValue }
            error = nil
        } catch { self.error = error.localizedDescription }
    }
}

/// Settings → Models. Choices are stored by role; keys remain in the Keychain and never appear here.
struct ModelsSection: View {
    let store: ChatStore
    @State private var roles: [String: ModelChoice] = [:]
    /// The last role map confirmed by the server.  Model choices should persist as soon as a user
    /// picks them; requiring a second, easy-to-miss Save button made them appear to reset later.
    @State private var savedRoles: [String: ModelChoice] = [:]
    @State private var saveGeneration = 0
    @State private var error: String?
    @State private var saving = false
    @State private var localModel = ""
    @State private var confirmDownload = false
    @State private var downloading = false
    @State private var syncingCloudModels = false
    @State private var sleepMode = SleepMode(model: "")
    @State private var savingSleep = false
    @State private var confirmSleep = false

    /// Coding is an account-backed Developer setting below, not an Aura cloud-chat model role.
    private static let order = ["startup", "chat", "deep_research", "web_search"]

    var body: some View {
        Section("Models") {
            if let catalog = store.modelCatalog {
                Text("Ollama is local by default. Selecting a cloud model sends that role's prompts to Anthropic; it does not preload a model at launch.")
                    .font(.caption).foregroundStyle(.secondary)
                if !catalog.anthropicKeySet {
                    Label("Add an Anthropic API key below before choosing Claude.", systemImage: "key.fill")
                        .font(.caption).foregroundStyle(.orange)
                }
                HStack {
                    Button(syncingCloudModels ? "Syncing cloud models…" : "Sync cloud models") { syncCloudModels() }
                        .disabled(syncingCloudModels || !catalog.anthropicKeySet)
                    if syncingCloudModels { ProgressView().controlSize(.small) }
                    Spacer()
                    Text("\(catalog.anthropicModels.count) available")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .controlSize(.small)
                Text("Sync checks Anthropic with your saved API key and lists the cloud models available to that account. It never exposes the key to Aura’s interface.")
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(Self.order, id: \.self) { role in
                    ModelRoleRow(
                        title: catalog.roleLabels[role] ?? role,
                        choice: binding(role),
                        catalog: catalog,
                        isStartup: role == "startup"
                    )
                }
                DeveloperCodingRow(store: store, engine: catalog.codingEngine)
                if let problem = catalog.startupModelProblem {
                    Label(problem, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
                Divider()
                Toggle("Sleep Mode", isOn: sleepEnabled)
                    .disabled(savingSleep || catalog.ollamaModels.isEmpty)
                if !catalog.ollamaModels.isEmpty {
                    Picker("Keep this local model loaded", selection: $sleepMode.model) {
                        ForEach(catalog.ollamaModels, id: \.self) { Text($0).tag($0) }
                    }
                    .disabled(savingSleep)
                    .onChange(of: sleepMode.model) { _ in
                        if sleepMode.enabled { saveSleepMode() }
                    }
                }
                Text("When on, Aura asks Ollama to keep this local model in memory even after the normal five-minute idle period. It can reserve several GB of unified memory and continues after Aura closes. Turning it off returns the model to Aura's normal idle lifecycle.")
                    .font(.caption).foregroundStyle(.secondary)
                if let startup = roles["startup"]?.model, sleepMode.enabled && sleepMode.model != startup {
                    Label("The Startup and Sleep models differ. Aura may load both models at launch, which increases memory use.", systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(.orange)
                }
                Divider()
                Text("Install a local Ollama model")
                    .font(.callout.weight(.medium))
                Link("Browse Ollama’s most-downloaded models", destination: Self.ollamaPopularModels)
                    .font(.caption)
                TextField("Model name, e.g. qwen3:8b", text: $localModel)
                    .textFieldStyle(.roundedBorder)
                    .disabled(downloading)
                Text("Downloads use your local Ollama daemon and its configured model folder, not Aura_Vault. Models can use several GB and may download from the internet. Only download a model name and source you trust.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button(downloading ? "Downloading…" : "Download Local Model") { confirmDownload = true }
                        .disabled(downloading || localModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    if downloading { ProgressView().controlSize(.small) }
                    Spacer()
                }
                .controlSize(.small)
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
                HStack {
                    Text(saving ? "Saving model settings…" : "Model choices save automatically.")
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button("Save now") { saveNow() }.disabled(saving || roles.count != 5)
                }
                .controlSize(.small)
            } else {
                Text(store.transport == nil ? "Aura is offline." : "Loading model settings…").foregroundStyle(.secondary)
            }
        }
        .onChange(of: store.modelCatalog) { sync() }
        .onChange(of: roles) { _, _ in scheduleSave() }
        .onAppear(perform: sync)
        .confirmationDialog("Download local model?", isPresented: $confirmDownload, titleVisibility: .visible) {
            Button("Download \(localModel.trimmingCharacters(in: .whitespacesAndNewlines))") { download() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Aura will ask Ollama to download this named model. The model is stored by Ollama outside Aura_Vault and can consume several GB of disk space.")
        }
        .confirmationDialog("Enable Sleep Mode?", isPresented: $confirmSleep, titleVisibility: .visible) {
            Button("Keep \(sleepMode.model) Loaded") {
                sleepMode.enabled = true
                saveSleepMode()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This keeps the selected local model resident in Ollama memory until you turn Sleep Mode off. It may make a smaller Mac feel less responsive.")
        }
    }

    private static let ollamaPopularModels = URL(string: "https://ollama.com/library?sort=popular")!

    private func binding(_ role: String) -> Binding<ModelChoice> {
        Binding(
            get: { roles[role] ?? ModelChoice(provider: "ollama", model: "") },
            set: { roles[role] = $0 }
        )
    }

    private func sync() {
        if let catalog = store.modelCatalog {
            roles = catalog.roles
            savedRoles = catalog.roles
            sleepMode = catalog.sleepMode
        }
    }

    private func scheduleSave() {
        guard roles.count == savedRoles.count, roles != savedRoles else { return }
        saveGeneration += 1
        let generation = saveGeneration
        // A provider switch changes both provider and model.  Debouncing lets those two picker
        // updates settle into one validated, atomic server preference update.
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
        // If the user made another selection while the request was in flight, persist that newer
        // choice next instead of allowing an older response to silently win. Do not retry a
        // rejected partial/invalid value by itself: the user should see the validation message.
        if roles != requested { scheduleSave() }
    }

    private func download() {
        downloading = true
        error = nil
        let requested = localModel.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            error = await store.pullLocalModel(requested)
            if error == nil { localModel = "" }
            downloading = false
        }
    }

    private func syncCloudModels() {
        syncingCloudModels = true
        error = nil
        Task {
            error = await store.syncCloudModels()
            syncingCloudModels = false
        }
    }

    private var sleepEnabled: Binding<Bool> {
        Binding(
            get: { sleepMode.enabled },
            set: { enabled in
                if enabled { confirmSleep = true }
                else {
                    sleepMode.enabled = false
                    saveSleepMode()
                }
            }
        )
    }

    private func saveSleepMode() {
        savingSleep = true
        error = nil
        Task {
            error = await store.updateSleepMode(sleepMode)
            savingSleep = false
        }
    }
}

private struct DeveloperCodingRow: View {
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
            if let error { Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
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

private struct ModelRoleRow: View {
    let title: String
    @Binding var choice: ModelChoice
    let catalog: ModelCatalog
    let isStartup: Bool

    private var suggestions: [String] {
        choice.provider == "anthropic" ? catalog.anthropicModels : catalog.ollamaModels
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
                    Text("Ollama (local)").tag("ollama")
                    Text("Anthropic (cloud)").tag("anthropic")
                }
                .labelsHidden()
                .onChange(of: choice.provider) { provider in
                    let available = provider == "anthropic" ? catalog.anthropicModels : catalog.ollamaModels
                    choice.model = available.first ?? ""
                }
                TextField("Model", text: $choice.model)
                    .textFieldStyle(.roundedBorder)
                    .frame(minWidth: 180)
            }
            if !suggestions.isEmpty {
                Menu(choice.provider == "ollama" ? "Installed local models" : "Available cloud models") {
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

/// The chat's font, text size and bubble colors, with a live preview.
struct ChatAppearanceSettings: View {
    @AppStorage(ChatAppearance.sizeKey) private var fontSize = ChatAppearance.defaultSize
    @AppStorage(ChatAppearance.styleKey) private var fontStyle = ChatFontStyle.system.rawValue
    @AppStorage(ChatAppearance.userBubbleKey) private var userBubble = ""
    @AppStorage(ChatAppearance.auraBubbleKey) private var auraBubble = ChatAppearance.defaultAura
    @AppStorage(ChatAppearance.userTextKey) private var userText = ""
    @AppStorage(ChatAppearance.auraTextKey) private var auraText = ""
    @AppStorage(ChatAppearance.bubbleTransparencyKey) private var bubbleTransparency = 0.0
    @AppStorage(ChatAppearance.textTransparencyKey) private var textTransparency = 0.0
    @AppStorage(ChatAppearance.textPaddingKey) private var textPadding = ChatAppearance.defaultTextPadding
    @AppStorage(ChatAppearance.avatarSizeKey) private var avatarSize = "small"
    @AppStorage(ChatAppearance.userBubbleBorderKey) private var userBubbleBorder = ""
    @AppStorage(ChatAppearance.auraBubbleBorderKey) private var auraBubbleBorder = ""
    @AppStorage(ChatAppearance.userAvatarBorderKey) private var userAvatarBorder = ""
    @AppStorage(ChatAppearance.auraAvatarBorderKey) private var auraAvatarBorder = ""
    @AppStorage(ChatAppearance.userBubbleBorderTransparencyKey) private var userBubbleBorderTransparency = 1.0
    @AppStorage(ChatAppearance.auraBubbleBorderTransparencyKey) private var auraBubbleBorderTransparency = 1.0
    @AppStorage(ChatAppearance.userAvatarBorderTransparencyKey) private var userAvatarBorderTransparency = 0.55
    @AppStorage(ChatAppearance.auraAvatarBorderTransparencyKey) private var auraAvatarBorderTransparency = 0.5

    private var text: ChatTextStyle { ChatAppearance.textStyle(size: fontSize, style: fontStyle) }
    private var colors: BubbleColors {
        ChatAppearance.bubbles(user: userBubble, aura: auraBubble, userText: userText, auraText: auraText,
                               bubbleTransparency: bubbleTransparency, textTransparency: textTransparency,
                               userBorder: userBubbleBorder, auraBorder: auraBubbleBorder,
                               userAvatarBorder: userAvatarBorder, auraAvatarBorder: auraAvatarBorder,
                               userBubbleBorderTransparency: userBubbleBorderTransparency,
                               auraBubbleBorderTransparency: auraBubbleBorderTransparency,
                               userAvatarBorderTransparency: userAvatarBorderTransparency,
                               auraAvatarBorderTransparency: auraAvatarBorderTransparency)
    }

    var body: some View {
        Picker("Font", selection: $fontStyle) {
            ForEach(ChatFontStyle.allCases, id: \.rawValue) { style in
                Text(style.label).font(style.font(size: 13, weight: .regular)).tag(style.rawValue)
            }
        }
        LabeledContent("Text size") {
            HStack {
                Slider(value: $fontSize, in: ChatAppearance.sizes, step: 1)
                Text("\(Int(fontSize)) pt").monospacedDigit().foregroundStyle(.secondary).frame(width: 40, alignment: .trailing)
            }
        }
        LabeledContent("Message text padding") {
            HStack {
                Slider(value: $textPadding, in: ChatAppearance.textPaddings, step: 1)
                Text("\(Int(textPadding)) pt").monospacedDigit().foregroundStyle(.secondary).frame(width: 40, alignment: .trailing)
            }
        }
        BubbleColorRow(title: "Your bubbles", hex: $userBubble)
        BubbleColorRow(title: "Aura's bubbles", hex: $auraBubble)
        BubbleColorRow(title: "Your text color", hex: $userText)
        BubbleColorRow(title: "Aura's text color", hex: $auraText)
        LabeledContent("Bubble transparency") {
            HStack { Slider(value: $bubbleTransparency, in: 0...1, step: 0.05); Text("\(Int(bubbleTransparency * 100))%").frame(width: 34, alignment: .trailing) }
        }
        LabeledContent("Text transparency") {
            HStack { Slider(value: $textTransparency, in: 0...1, step: 0.05); Text("\(Int(textTransparency * 100))%").frame(width: 34, alignment: .trailing) }
        }
        Divider()
        BubbleColorRow(title: "Your bubble border", hex: $userBubbleBorder)
        TransparencyRow(title: "Your bubble border transparency", value: $userBubbleBorderTransparency)
        BubbleColorRow(title: "Aura's bubble border", hex: $auraBubbleBorder)
        TransparencyRow(title: "Aura's bubble border transparency", value: $auraBubbleBorderTransparency)
        BubbleColorRow(title: "Your avatar border", hex: $userAvatarBorder)
        TransparencyRow(title: "Your avatar border transparency", value: $userAvatarBorderTransparency)
        BubbleColorRow(title: "Aura's avatar border", hex: $auraAvatarBorder)
        TransparencyRow(title: "Aura's avatar border transparency", value: $auraAvatarBorderTransparency)
        Picker("Chat avatar size", selection: $avatarSize) {
            Text("Small").tag("small")
            Text("Medium").tag("medium")
            Text("Large").tag("large")
        }
        BubblePreview(text: text, colors: colors)
        HStack {
            Spacer()
            Button("Reset Chat Appearance") {
                fontSize = ChatAppearance.defaultSize
                fontStyle = ChatFontStyle.system.rawValue
                userBubble = ""
                auraBubble = ChatAppearance.defaultAura
                userText = ""
                auraText = ""
                bubbleTransparency = 0
                textTransparency = 0
                textPadding = ChatAppearance.defaultTextPadding
                avatarSize = "small"
                userBubbleBorder = ""
                auraBubbleBorder = ""
                userAvatarBorder = ""
                auraAvatarBorder = ""
                userBubbleBorderTransparency = 1
                auraBubbleBorderTransparency = 1
                userAvatarBorderTransparency = 0.55
                auraAvatarBorderTransparency = 0.5
            }
            .controlSize(.small)
        }
    }
}

struct TransparencyRow: View {
    let title: String
    @Binding var value: Double

    var body: some View {
        LabeledContent(title) {
            HStack {
                Slider(value: $value, in: 0...1, step: 0.05)
                Text("\(Int(value * 100))%").monospacedDigit().frame(width: 34, alignment: .trailing)
            }
        }
    }
}

/// Preset swatches (they work without leaving the popover), plus the system color picker.
struct BubbleColorRow: View {
    let title: String
    @Binding var hex: String

    var body: some View {
        LabeledContent(title) {
            HStack(spacing: 5) {
                ForEach(ChatAppearance.swatches, id: \.name) { swatch in
                    Button { hex = swatch.hex } label: {
                        Circle()
                            .fill(ChatAppearance.color(hex: swatch.hex) ?? .accentColor)
                            .frame(width: 16, height: 16)
                            .overlay(Circle().strokeBorder(.primary.opacity(hex == swatch.hex ? 0.9 : 0), lineWidth: 2).padding(-3))
                    }
                    .buttonStyle(.plain)
                    .help(swatch.name)
                }
                ColorPicker(title, selection: Binding(
                    get: { ChatAppearance.color(hex: hex) ?? .accentColor },
                    set: { hex = ChatAppearance.hex($0) }), supportsOpacity: false)
                    .labelsHidden()
                    .help("Any color")
            }
        }
    }
}

/// How the chat will look.
struct BubblePreview: View {
    let text: ChatTextStyle
    let colors: BubbleColors
    @AppStorage(ChatAppearance.textPaddingKey) private var textPadding = ChatAppearance.defaultTextPadding

    var body: some View {
        VStack(spacing: 6) {
            HStack {
                Spacer(minLength: 40)
                Circle().fill(.secondary.opacity(0.25)).frame(width: 24, height: 24)
                    .overlay(Circle().strokeBorder(colors.userAvatarBorder, lineWidth: 1))
                Text("Hi Aura, can you summarise my notes?")
                    .font(text.font())
                    .padding(.horizontal, ChatAppearance.textPadding(textPadding))
                    .padding(.vertical, ChatAppearance.verticalTextPadding(textPadding))
                    .foregroundStyle(colors.userText)
                    .background(colors.userBackground, in: .rect(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(colors.userBorder, lineWidth: 1))
            }
            HStack {
                Circle().fill(.secondary.opacity(0.25)).frame(width: 24, height: 24)
                    .overlay(Circle().strokeBorder(colors.auraAvatarBorder, lineWidth: 1))
                Text("Of course. Your notes cover **three** things:")
                    .font(text.font())
                    .padding(.horizontal, ChatAppearance.textPadding(textPadding))
                    .padding(.vertical, ChatAppearance.verticalTextPadding(textPadding))
                    .foregroundStyle(colors.auraText)
                    .background(colors.auraBackground, in: .rect(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(colors.auraBorder, lineWidth: 1))
                Spacer(minLength: 40)
            }
        }
        .padding(.vertical, 4)
    }
}

/// Settings → Keys. A key goes from this field to the local Aura Server, which stores it in the
/// macOS Keychain; it is never shown again (only a masked hint), and never written to a file, a
/// log or the chat.
struct KeysSection: View {
    let store: ChatStore

    var body: some View {
        Section {
            ForEach(store.secrets) { KeyRow(secret: $0, store: store) }
            if store.secrets.isEmpty {
                Text(store.transport == nil ? "Aura is offline." : "Loading…").foregroundStyle(.secondary)
            }
        } header: {
            Text("Keys")
        } footer: {
            Text("Stored in this Mac's Keychain, never in a file, a log or the chat.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

struct KeyRow: View {
    let secret: SecretInfo
    let store: ChatStore
    @State private var entering = false
    @State private var value = ""
    @State private var error: String?
    @State private var saving = false
    @State private var confirmRemove = false
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text(secret.label)
                    Text(secret.purpose).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                if !entering {
                    Text(secret.hint ?? "Not set").font(.callout.monospaced())
                        .foregroundStyle(secret.set ? Color.primary : Color.secondary)
                }
            }
            if entering {
                SecureField(secret.placeholder, text: $value)
                    .textFieldStyle(.roundedBorder)
                    .focused($focused)
                    .onSubmit(save)
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
                HStack {
                    if let source = secret.source { Link("Get a key…", destination: source).font(.caption) }
                    Spacer()
                    Button("Cancel") { reset() }
                    Button("Save to Keychain", action: save).disabled(value.isEmpty || saving)
                }
                .controlSize(.small)
            } else {
                HStack {
                    if let source = secret.source, !secret.set { Link("Get a key…", destination: source).font(.caption) }
                    Spacer()
                    if secret.set {
                        Button("Remove", role: .destructive) { confirmRemove = true }
                    }
                    if !secret.isRetired {
                        Button(secret.set ? "Replace…" : "Add…") { entering = true; focused = true }
                    }
                }
                .controlSize(.small)
            }
        }
        .confirmationDialog("Remove the \(secret.label) from the Keychain?", isPresented: $confirmRemove) {
            Button("Remove", role: .destructive) { Task { await store.removeSecret(secret.name) } }
        }
    }

    private func save() {
        guard !value.isEmpty, !saving else { return }
        saving = true
        Task {
            error = await store.saveSecret(secret.name, value: value)
            saving = false
            if error == nil { reset() }
        }
    }

    private func reset() {
        value = ""  // the key doesn't linger in the view
        error = nil
        entering = false
    }
}

/// Voices worth showing: best quality first, the user's languages, no novelty voices.
struct VoiceGroups {
    struct Group { let title: String; let voices: [VoiceInfo] }
    let sections: [Group]

    init(_ catalog: [VoiceInfo], languages: [String] = Locale.preferredLanguages) {
        let usable = catalog.filter { !$0.novelty }
        let mine = Set(languages.compactMap { $0.split(separator: "-").first.map(String.init) })
        let aura = usable.filter { $0.quality == "neural" }  // the server's curated order
        let recommended = usable.filter { ["premium", "enhanced"].contains($0.quality) }.sorted(by: Self.order)
        let local = usable.filter { $0.quality == "default" && mine.contains(String($0.language.prefix(2))) }
            .sorted(by: Self.order)
        sections = [
            Group(title: "Aura voices", voices: aura),
            Group(title: "macOS: recommended", voices: recommended),
            Group(title: "macOS: more voices", voices: local),
        ].filter { !$0.voices.isEmpty }
    }

    static func label(_ v: VoiceInfo) -> String {
        let quality = ["default", "neural"].contains(v.quality) ? "" : " (\(v.quality.capitalized))"
        return "\(v.name)\(quality) · \(v.language)"
    }

    private static func order(_ a: VoiceInfo, _ b: VoiceInfo) -> Bool {
        let rank = ["premium": 0, "enhanced": 1, "default": 2]
        return (rank[a.quality] ?? 3, a.language, a.name) < (rank[b.quality] ?? 3, b.language, b.name)
    }
}
