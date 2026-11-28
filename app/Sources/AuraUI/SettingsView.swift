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
    @Environment(\.openModels) private var openModels

    var body: some View {
        Section("Models") {
            if let catalog = store.modelCatalog {
                // docs/models-window.md: every model setting is in the Models window now.
                HStack {
                    Label("Model roles, local and cloud models (the Anthropic key included) and Sleep Mode are in the Models window.",
                          systemImage: "arrow.triangle.branch")
                        .font(.callout)
                    Spacer()
                    Button("Open Models…") { openModels(.roles) }
                }
                if let problem = catalog.startupModelProblem {
                    Label(problem, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
            } else {
                Text(store.transport == nil ? "Aura is offline." : "Loading model settings…").foregroundStyle(.secondary)
            }
        }
    }
}

/// The chat's font, text size and bubble colors, with a live preview.
struct ChatAppearanceSettings: View {
    @AppStorage(ChatAppearance.sizeKey) private var fontSize = ChatAppearance.defaultSize
    @AppStorage(ChatAppearance.styleKey) private var fontStyle = ChatFontStyle.system.rawValue
    @AppStorage(ChatAppearance.userBubbleKey) private var userBubble = ""
    @AppStorage(ChatAppearance.auraBubbleKey) private var auraBubble = ChatAppearance.defaultAura
    @AppStorage(ChatAppearance.holBubbleKey) private var holBubble = ChatAppearance.defaultAura
    @AppStorage(ChatAppearance.userTextKey) private var userText = ""
    @AppStorage(ChatAppearance.auraTextKey) private var auraText = ""
    @AppStorage(ChatAppearance.holTextKey) private var holText = ""
    @AppStorage(ChatAppearance.bubbleTransparencyKey) private var bubbleTransparency = 0.0
    @AppStorage(ChatAppearance.textTransparencyKey) private var textTransparency = 0.0
    @AppStorage(ChatAppearance.holBubbleTransparencyKey) private var holBubbleTransparency = 0.0
    @AppStorage(ChatAppearance.holTextTransparencyKey) private var holTextTransparency = 0.0
    @AppStorage(ChatAppearance.textPaddingKey) private var textPadding = ChatAppearance.defaultTextPadding
    @AppStorage(ChatAppearance.avatarSizeKey) private var avatarSize = "small"
    @AppStorage(ChatAppearance.userBubbleBorderKey) private var userBubbleBorder = ""
    @AppStorage(ChatAppearance.auraBubbleBorderKey) private var auraBubbleBorder = ""
    @AppStorage(ChatAppearance.holBubbleBorderKey) private var holBubbleBorder = ""
    @AppStorage(ChatAppearance.userAvatarBorderKey) private var userAvatarBorder = ""
    @AppStorage(ChatAppearance.auraAvatarBorderKey) private var auraAvatarBorder = ""
    @AppStorage(ChatAppearance.userBubbleBorderTransparencyKey) private var userBubbleBorderTransparency = 1.0
    @AppStorage(ChatAppearance.auraBubbleBorderTransparencyKey) private var auraBubbleBorderTransparency = 1.0
    @AppStorage(ChatAppearance.holBubbleBorderTransparencyKey) private var holBubbleBorderTransparency = 1.0
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
        Divider()
        Text("HOL session bubble").font(.headline)
        BubbleColorRow(title: "HOL session bubble", hex: $holBubble)
        BubbleColorRow(title: "HOL session text color", hex: $holText)
        TransparencyRow(title: "HOL session bubble transparency", value: $holBubbleTransparency)
        TransparencyRow(title: "HOL session text transparency", value: $holTextTransparency)
        BubbleColorRow(title: "HOL session bubble border", hex: $holBubbleBorder)
        TransparencyRow(title: "HOL session bubble border transparency", value: $holBubbleBorderTransparency)
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
                holBubble = ChatAppearance.defaultAura
                userText = ""
                auraText = ""
                holText = ""
                bubbleTransparency = 0
                textTransparency = 0
                holBubbleTransparency = 0
                holTextTransparency = 0
                textPadding = ChatAppearance.defaultTextPadding
                avatarSize = "small"
                userBubbleBorder = ""
                auraBubbleBorder = ""
                holBubbleBorder = ""
                userAvatarBorder = ""
                auraAvatarBorder = ""
                userBubbleBorderTransparency = 1
                auraBubbleBorderTransparency = 1
                holBubbleBorderTransparency = 1
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
    @AppStorage(ChatAppearance.avatarSizeKey) private var avatarSize = "small"

    private var avatarDiameter: CGFloat { ChatAppearance.avatarSize(avatarSize) }

    var body: some View {
        VStack(spacing: 6) {
            HStack {
                Spacer(minLength: 40)
                Circle().fill(.secondary.opacity(0.25)).frame(width: avatarDiameter, height: avatarDiameter)
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
                Circle().fill(.secondary.opacity(0.25)).frame(width: avatarDiameter, height: avatarDiameter)
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
            // The Anthropic API key is on the Models window's Cloud page (Aura's ruling, 2026-11-27).
            ForEach(store.secrets.filter { !CloudModelsPage.keys.contains($0.name) }) { KeyRow(secret: $0, store: store) }
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
