import AVFoundation
import Foundation
import NaturalLanguage
import Observation

// MARK: - planning: streamed Markdown -> speakable sentences

/// Turns a reply that streams in token by token into finished, speakable sentences.
/// A sentence is released only once the next one has begun (or the reply is final), so a
/// half-streamed "It's 5." is never spoken before "3°C" arrives.
public struct SpeechPlanner: Sendable {
    private var released = 0

    public init() {}

    public mutating func reset() { released = 0 }

    /// Newly completed sentences, given the whole reply so far.
    public mutating func next(_ reply: String, final: Bool) -> [String] {
        let all = Self.sentences(Self.speakable(reply))
        let complete = final ? all : Array(all.dropLast())
        guard complete.count > released else { return [] }
        defer { released = complete.count }
        return Array(complete[released...])
    }

    /// Markdown -> what a person would say: no symbols, code and tables announced, not recited.
    public static func speakable(_ markdown: String) -> String {
        var parts: [String] = []
        var saidCode = false, saidTable = false
        for block in Markdown.blocks(markdown) {
            switch block.kind {
            case .code:
                if !saidCode { parts.append("I've put the code in the chat."); saidCode = true }
            case .table:
                if !saidTable { parts.append("There's a table in the chat."); saidTable = true }
            case .rule:
                continue
            case .paragraph, .heading, .listItem:
                let text = clean(block.plainText)
                guard !text.isEmpty else { continue }
                // Headings and list items often lack a full stop; give each its own sentence.
                parts.append(text.last.map { ".!?:;".contains($0) } == true ? text : text + ".")
            }
        }
        return parts.joined(separator: " ")
    }

    /// "I need your approval to delete file old-draft.txt."
    public static func approvalSentence(_ actions: [PendingAction]) -> String {
        let what = actions.map { action in
            var line = action.description.components(separatedBy: "\n").first ?? ""
            line = line.replacingOccurrences(of: #"\s*\([^)]*\)"#, with: "", options: .regularExpression)  // "(6 B)"
            line = line.replacingOccurrences(of: #"[:'"]"#, with: "", options: .regularExpression)
            return line.trimmingCharacters(in: .whitespaces)
        }
        return "I need your approval to " + what.joined(separator: ", and to ") + "."
    }

    static func sentences(_ text: String) -> [String] {
        let tokenizer = NLTokenizer(unit: .sentence)
        tokenizer.string = text
        return tokenizer.tokens(for: text.startIndex..<text.endIndex)
            .map { text[$0].trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    private static func clean(_ text: String) -> String {
        var s = text.replacingOccurrences(of: #"https?://\S+"#, with: "a link", options: .regularExpression)
        s.unicodeScalars.removeAll { $0.properties.isEmojiPresentation || ($0.properties.isEmoji && $0.value > 0x2000) }
        return s.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }
}

// MARK: - engines

/// Something that can speak. The system engine is built in; a cloud engine would be an optional
/// module the user explicitly configures (Gemma's "Local-First" mandate).
@MainActor
public protocol SpeechEngine: AnyObject {
    var isSpeaking: Bool { get }
    var onSpeakingChanged: ((Bool) -> Void)? { get set }
    func speak(_ sentence: String, profile: VoiceProfile)
    func stop()
    func warmUp(profile: VoiceProfile)
}

/// macOS system voices via AVFoundation: offline, instant, nothing leaves the Mac.
@MainActor
public final class SystemSpeechEngine: NSObject, SpeechEngine, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private let warmer = AVSpeechSynthesizer()
    public var onSpeakingChanged: ((Bool) -> Void)?

    override public init() {
        super.init()
        synthesizer.delegate = self
    }

    public var isSpeaking: Bool { synthesizer.isSpeaking }

    public func speak(_ sentence: String, profile: VoiceProfile) {
        synthesizer.speak(Self.utterance(sentence, profile))
    }

    public func stop() {
        synthesizer.stopSpeaking(at: .immediate)
    }

    /// Load the voice ahead of time (render silently, discard) so the first sentence isn't late.
    public func warmUp(profile: VoiceProfile) {
        warmer.write(Self.utterance("Hello.", profile)) { _ in }
    }

    /// Every installed voice, for the settings picker and the agent's catalog.
    public static func catalog() -> [VoiceInfo] {
        AVSpeechSynthesisVoice.speechVoices().map { v in
            VoiceInfo(
                id: v.identifier, name: v.name, language: v.language,
                quality: v.quality == .premium ? "premium" : v.quality == .enhanced ? "enhanced" : "default",
                gender: v.gender == .female ? "female" : v.gender == .male ? "male" : "unknown",
                novelty: v.voiceTraits.contains(.isNoveltyVoice))
        }
    }

    public static func utterance(_ text: String, _ profile: VoiceProfile) -> AVSpeechUtterance {
        let u = AVSpeechUtterance(string: text)
        u.voice = profile.voice.flatMap(AVSpeechSynthesisVoice.init(identifier:)) ?? AVSpeechSynthesisVoice(language: "en-US")
        let rate = AVSpeechUtteranceDefaultSpeechRate * Float(profile.speed)
        u.rate = min(max(rate, AVSpeechUtteranceMinimumSpeechRate), AVSpeechUtteranceMaximumSpeechRate)
        u.pitchMultiplier = Float(min(max(profile.pitch, 0.5), 2.0))
        u.volume = Float(min(max(profile.volume, 0.0), 1.0))
        return u
    }

    /// Synthesize to memory without playing a sound (tests, smoke checks): seconds of speech.
    public static func renderSilently(_ text: String, profile: VoiceProfile) async -> Double {
        let synth = AVSpeechSynthesizer()
        return await withCheckedContinuation { continuation in
            var frames = 0.0, rate = 0.0, resumed = false
            synth.write(utterance(text, profile)) { buffer in
                guard let pcm = buffer as? AVAudioPCMBuffer, !resumed else { return }
                if pcm.frameLength == 0 {
                    resumed = true
                    continuation.resume(returning: rate > 0 ? frames / rate : 0)
                } else {
                    frames += Double(pcm.frameLength)
                    rate = pcm.format.sampleRate
                }
            }
        }
    }

    nonisolated public func speechSynthesizer(_ s: AVSpeechSynthesizer, didStart u: AVSpeechUtterance) { changed() }
    nonisolated public func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish u: AVSpeechUtterance) { changed() }
    nonisolated public func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel u: AVSpeechUtterance) { changed() }

    nonisolated private func changed() {
        Task { @MainActor in self.onSpeakingChanged?(self.synthesizer.isSpeaking) }
    }
}

// MARK: - control

/// Decides what Aura says aloud and when: replies sentence by sentence, approval requests as one
/// sentence ("verbal audit trail"), nothing at all while spoken replies are off.
@MainActor
@Observable
public final class SpeechController {
    public private(set) var isSpeaking = false
    public var profile: VoiceProfile {
        didSet { if !profile.speak { stop() } }
    }

    @ObservationIgnored private let engine: SpeechEngine
    @ObservationIgnored private var planner = SpeechPlanner()
    /// Set by stop(): the rest of the current reply stays silent (never restarts from the top).
    @ObservationIgnored private var silenced = false

    public init(engine: SpeechEngine, profile: VoiceProfile = VoiceProfile()) {
        self.engine = engine
        self.profile = profile
        engine.onSpeakingChanged = { [weak self] speaking in self?.isSpeaking = speaking }
    }

    /// A new reply segment begins (a new turn, or Aura speaking again after a tool).
    public func beginReply() {
        planner.reset()
        silenced = false
    }

    public func reply(_ text: String, final: Bool) {
        guard profile.speak, !silenced else { return }
        for sentence in planner.next(text, final: final) { engine.speak(sentence, profile: profile) }
    }

    public func approvalRequested(_ actions: [PendingAction]) {
        guard profile.speak else { return }
        engine.speak(SpeechPlanner.approvalSentence(actions), profile: profile)
    }

    public func stop() {
        engine.stop()
        silenced = true
    }

    /// Speak a sample with the current voice even if spoken replies are off (the user asked).
    public func preview(_ text: String) {
        engine.stop()
        engine.speak(text, profile: profile)
    }

    public func warmUp() {
        if profile.speak { engine.warmUp(profile: profile) }
    }
}
