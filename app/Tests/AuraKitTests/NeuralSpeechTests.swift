import Foundation
import Testing
@testable import AuraKit

/// Records what would have played; "finishes" each clip right away. Never makes a sound.
@MainActor
final class FakeOutput: AudioOutput {
    var played: [String] = []
    var stops = 0

    func play(_ wav: Data, volume: Float, finished: @escaping @MainActor () -> Void) -> Bool {
        let text = String(decoding: wav, as: UTF8.self)
        if text == "BROKEN" { return false }
        played.append(text)
        Task { @MainActor in finished() }
        return true
    }

    func stop() { stops += 1 }
}

@MainActor
struct NeuralSpeechEngineTests {
    private func eventually(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<20_000 where !condition() { await Task.yield() }
        return condition()
    }

    /// The "server": returns the sentence as its own audio, after a per-sentence delay.
    private func engine(_ output: FakeOutput, delays: [String: UInt64] = [:]) -> NeuralSpeechEngine {
        NeuralSpeechEngine(output: output) {
            { text, _ in
                if let ms = delays[text] { try await Task.sleep(nanoseconds: ms * 1_000_000) }
                return Data(text.utf8)
            }
        }
    }

    @Test func playsSentencesInOrderEvenWhenALaterOneRendersFirst() async {
        let output = FakeOutput()
        let e = engine(output, delays: ["One.": 80, "Two.": 1])
        let voice = VoiceProfile(voice: "kokoro:af_heart", speak: true)
        e.speak("One.", profile: voice)
        e.speak("Two.", profile: voice)
        e.speak("Three.", profile: voice)
        #expect(await eventually { output.played.count == 3 })
        #expect(output.played == ["One.", "Two.", "Three."])
        #expect(await eventually { !e.isSpeaking })
    }

    @Test func stopSilencesEverythingStillPending() async {
        let output = FakeOutput()
        let e = engine(output, delays: ["One.": 50, "Two.": 50])
        let voice = VoiceProfile(voice: "kokoro:af_heart", speak: true)
        e.speak("One.", profile: voice)
        e.speak("Two.", profile: voice)
        e.stop()
        try? await Task.sleep(nanoseconds: 150_000_000)
        #expect(output.played.isEmpty && output.stops == 1 && !e.isSpeaking)
    }

    @Test func aSentenceThatFailsIsSkippedNotFatal() async {
        let output = FakeOutput()
        let e = engine(output)
        let voice = VoiceProfile(voice: "kokoro:af_heart", speak: true)
        for s in ["One.", "BROKEN", "Three."] { e.speak(s, profile: voice) }
        #expect(await eventually { output.played == ["One.", "Three."] })
    }

    @Test func routingSendsAurasVoicesToTheNeuralEngine() {
        let system = FakeEngine(), neural = FakeEngine()
        let router = RoutingSpeechEngine(system: system, neural: neural)
        router.speak("Hi from Heart.", profile: VoiceProfile(voice: "kokoro:af_heart"))
        router.speak("Hi from Voice 6.", profile: VoiceProfile(voice: "com.apple.voice.enhanced.en-US.Voice6"))
        router.speak("Hi from the default.", profile: VoiceProfile())
        #expect(neural.spoken == ["Hi from Heart."])
        #expect(system.spoken == ["Hi from Voice 6.", "Hi from the default."])
        router.stop()
        #expect(system.stops == 1 && neural.stops == 1)
    }
}
