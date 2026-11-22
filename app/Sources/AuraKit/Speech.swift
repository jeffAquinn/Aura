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

    /// "I need your approval to delete file old-draft.txt." Code runs get Gemma's spoken status.
    public static func approvalSentence(_ actions: [PendingAction]) -> String {
        if actions.allSatisfy({ $0.tool == "run_python" }) {
            return "I'm preparing to run a Python script in the sandbox, and I need your approval."
        }
        if actions.count == 1, let build = actions[0].build {
            let engine = EngineInfo.named(build.engine)
            let skill = build.skill.replacingOccurrences(of: "_", with: " ")
            return "I'd like \(engine?.label ?? "a Developer") to \(build.improve ? "improve" : "build") the \(skill) skill. "
                + "Only the specification on the card goes to \(engine?.vendor ?? "the vendor"). I need your approval."
        }
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

/// Plays one clip of audio; a protocol so tests never make a sound.
@MainActor
public protocol AudioOutput: AnyObject {
    func play(_ wav: Data, volume: Float, finished: @escaping @MainActor () -> Void) -> Bool
    func stop()
}

/// AVAudioPlayer-backed output.
@MainActor
public final class SpeakerOutput: NSObject, AudioOutput, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var finished: (@MainActor () -> Void)?

    override public init() { super.init() }

    public func play(_ wav: Data, volume: Float, finished: @escaping @MainActor () -> Void) -> Bool {
        guard let player = try? AVAudioPlayer(data: wav) else { return false }
        player.delegate = self
        player.volume = volume
        self.player = player
        self.finished = finished
        return player.play()
    }

    public func stop() {
        player?.stop()
        player = nil
        finished = nil
    }

    nonisolated public func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            let done = self.finished
            self.finished = nil
            done?()
        }
    }
}

/// Aura's neural voices (Kokoro), synthesized by the local Aura Server one sentence at a time.
/// Every sentence is requested as soon as it's known, so the next one is ready while this one
/// plays; playback always follows the order the sentences were spoken in.
@MainActor
public final class NeuralSpeechEngine: SpeechEngine {
    public typealias Fetch = @Sendable (String, VoiceProfile) async throws -> Data
    public var onSpeakingChanged: ((Bool) -> Void)?
    public private(set) var isSpeaking = false

    private let fetcher: () -> Fetch?
    private let output: AudioOutput
    private var pending: [(task: Task<Data, Error>, volume: Float)] = []
    private var playing = false
    private var generation = 0

    /// `fetcher` returns the current way to reach the server (it changes if the server restarts).
    public init(output: AudioOutput = SpeakerOutput(), fetcher: @escaping () -> Fetch?) {
        self.output = output
        self.fetcher = fetcher
    }

    public func speak(_ sentence: String, profile: VoiceProfile) {
        guard let fetch = fetcher() else { return }
        pending.append((Task { try await fetch(sentence, profile) }, Float(profile.volume)))
        if !playing {
            playing = true
            setSpeaking(true)
            playNext()
        }
    }

    public func stop() {
        generation += 1
        pending.forEach { $0.task.cancel() }
        pending.removeAll()
        output.stop()
        playing = false
        setSpeaking(false)
    }

    public func warmUp(profile: VoiceProfile) {
        guard let fetch = fetcher() else { return }
        Task { _ = try? await fetch("Hello.", profile) }  // loads the model and voice; discarded
    }

    private func playNext() {
        guard !pending.isEmpty else {
            playing = false
            setSpeaking(false)
            return
        }
        let (task, volume) = pending.removeFirst()
        let gen = generation
        Task { @MainActor [weak self] in
            let wav = try? await task.value
            guard let self, gen == self.generation else { return }  // stopped meanwhile
            let started = wav.map { clip in
                self.output.play(clip, volume: volume) { [weak self] in
                    guard let self, gen == self.generation else { return }
                    self.playNext()
                }
            } ?? false
            if !started { self.playNext() }  // a failed sentence is skipped, not fatal
        }
    }

    private func setSpeaking(_ value: Bool) {
        guard isSpeaking != value else { return }
        isSpeaking = value
        onSpeakingChanged?(value)
    }
}

/// Chooses the engine per voice: Aura's neural voices ("kokoro:…") or macOS system voices.
@MainActor
public final class RoutingSpeechEngine: SpeechEngine {
    private let system: SpeechEngine
    private let neural: SpeechEngine
    public var onSpeakingChanged: ((Bool) -> Void)? {
        didSet {
            system.onSpeakingChanged = { [weak self] _ in self?.forward() }
            neural.onSpeakingChanged = { [weak self] _ in self?.forward() }
        }
    }

    public init(system: SpeechEngine, neural: SpeechEngine) {
        self.system = system
        self.neural = neural
    }

    public var isSpeaking: Bool { system.isSpeaking || neural.isSpeaking }

    public static func isNeural(_ profile: VoiceProfile) -> Bool { profile.voice?.hasPrefix("kokoro:") == true }

    private func engine(_ profile: VoiceProfile) -> SpeechEngine { Self.isNeural(profile) ? neural : system }
    public func speak(_ sentence: String, profile: VoiceProfile) { engine(profile).speak(sentence, profile: profile) }
    public func warmUp(profile: VoiceProfile) { engine(profile).warmUp(profile: profile) }
    public func stop() {
        system.stop()
        neural.stop()
    }

    private func forward() { onSpeakingChanged?(isSpeaking) }
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

    /// A short spoken status line ("Running the pdf text skill."), when spoken replies are on
    /// and the user hasn't stopped this reply.
    public func announce(_ status: String) {
        guard profile.speak, !silenced else { return }
        let line = status
            .replacingOccurrences(of: "...", with: ".")
            .replacingOccurrences(of: "…", with: ".")
            .replacingOccurrences(of: "_", with: " ")  // "pdf_text" reads as "pdf text"
        engine.speak(line, profile: profile)
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
