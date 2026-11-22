import AuraKit
import SwiftUI

/// Visual theme (Gemma's mandate): plain system glass, or Aura's starfield beneath it.
public enum Theme: String, CaseIterable, Sendable {
    case glass, starfield
    public static let key = "auraTheme"

    var label: String { self == .glass ? "Clean Glass" : "Aura Starfield" }
}

/// Voice, appearance and persona. Changes made here are the user acting directly: no approval.
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
        }
        .formStyle(.grouped)
        .frame(width: 360, height: 560)
        .onAppear(perform: syncSliders)
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

/// Voices worth showing: best quality first, the user's languages, no novelty voices.
struct VoiceGroups {
    struct Group { let title: String; let voices: [VoiceInfo] }
    let sections: [Group]

    init(_ catalog: [VoiceInfo], languages: [String] = Locale.preferredLanguages) {
        let usable = catalog.filter { !$0.novelty }
        let mine = Set(languages.compactMap { $0.split(separator: "-").first.map(String.init) })
        let recommended = usable.filter { $0.quality != "default" }.sorted(by: Self.order)
        let local = usable.filter { $0.quality == "default" && mine.contains(String($0.language.prefix(2))) }
            .sorted(by: Self.order)
        sections = [Group(title: "Recommended", voices: recommended), Group(title: "More voices", voices: local)]
            .filter { !$0.voices.isEmpty }
    }

    static func label(_ v: VoiceInfo) -> String {
        let quality = v.quality == "default" ? "" : " (\(v.quality.capitalized))"
        return "\(v.name)\(quality) · \(v.language)"
    }

    private static func order(_ a: VoiceInfo, _ b: VoiceInfo) -> Bool {
        let rank = ["premium": 0, "enhanced": 1, "default": 2]
        return (rank[a.quality] ?? 3, a.language, a.name) < (rank[b.quality] ?? 3, b.language, b.name)
    }
}
