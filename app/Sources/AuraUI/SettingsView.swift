import AuraKit
import SwiftUI

/// Visual theme (Gemma's mandate): plain system glass, or Aura's starfield beneath it.
public enum Theme: String, CaseIterable, Sendable {
    case glass, starfield
    public static let key = "auraTheme"

    var label: String { self == .glass ? "Clean Glass" : "Aura Starfield" }
}

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
            ConnectionsSection(store: store)
            KeysSection(store: store)
        }
        .formStyle(.grouped)
        .frame(width: 400, height: 600)
        .onAppear(perform: syncSliders)
        .task { await store.loadSecrets() }
        .onChange(of: store.voice) { syncSliders() }
        .confirmationDialog("Reset \(store.persona.name)'s persona to the default?", isPresented: $confirmReset) {
            Button("Reset", role: .destructive) { Task { await store.resetPersona() } }
        }
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

/// The chat's font, text size and bubble colors, with a live preview.
struct ChatAppearanceSettings: View {
    @AppStorage(ChatAppearance.sizeKey) private var fontSize = ChatAppearance.defaultSize
    @AppStorage(ChatAppearance.styleKey) private var fontStyle = ChatFontStyle.system.rawValue
    @AppStorage(ChatAppearance.userBubbleKey) private var userBubble = ""
    @AppStorage(ChatAppearance.auraBubbleKey) private var auraBubble = ChatAppearance.defaultAura

    private var text: ChatTextStyle { ChatAppearance.textStyle(size: fontSize, style: fontStyle) }
    private var colors: BubbleColors { ChatAppearance.bubbles(user: userBubble, aura: auraBubble) }

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
        BubbleColorRow(title: "Your bubbles", hex: $userBubble)
        BubbleColorRow(title: "Aura's bubbles", hex: $auraBubble)
        BubblePreview(text: text, colors: colors)
        HStack {
            Spacer()
            Button("Reset Chat Appearance") {
                fontSize = ChatAppearance.defaultSize
                fontStyle = ChatFontStyle.system.rawValue
                userBubble = ""
                auraBubble = ChatAppearance.defaultAura
            }
            .controlSize(.small)
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

    var body: some View {
        VStack(spacing: 6) {
            HStack {
                Spacer(minLength: 40)
                Text("Hi Aura, can you summarise my notes?")
                    .font(text.font())
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .foregroundStyle(colors.userText)
                    .background(colors.user, in: .rect(cornerRadius: 14))
            }
            HStack {
                Text("Of course. Your notes cover **three** things:")
                    .font(text.font())
                    .padding(.horizontal, 10).padding(.vertical, 6)
                    .foregroundStyle(colors.auraText)
                    .background(colors.aura, in: .rect(cornerRadius: 14))
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
                SecureField("Paste the key", text: $value)
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
                    Button(secret.set ? "Replace…" : "Add…") { entering = true; focused = true }
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
