import Foundation
import Testing
@testable import AuraKit

/// Records what would have been said; never makes a sound.
@MainActor
final class FakeEngine: SpeechEngine {
    var spoken: [String] = []
    var stops = 0
    var warmUps = 0
    var isSpeaking = false
    var onSpeakingChanged: ((Bool) -> Void)?
    func speak(_ sentence: String, profile: VoiceProfile) { spoken.append(sentence) }
    func stop() { stops += 1 }
    func warmUp(profile: VoiceProfile) { warmUps += 1 }
}

struct SpeechPlannerTests {
    /// Feed a reply in fragments, as the stream delivers it; return everything released.
    private func stream(_ fragments: [String]) -> [[String]] {
        var planner = SpeechPlanner()
        var text = ""
        var released: [[String]] = []
        for fragment in fragments {
            text += fragment
            released.append(planner.next(text, final: false))
        }
        released.append(planner.next(text, final: true))
        return released
    }

    @Test func sentencesAreReleasedOnlyOnceComplete() {
        let out = stream(["It's 5.", "3°C and", " overcast. Take", " a coat."])
        // Four fragments, then the end of the reply: the last sentence waits for the end.
        #expect(out == [[], [], ["It's 5.3°C and overcast."], [], ["Take a coat."]])
    }

    @Test func abbreviationsDoNotSplitSentences() {
        #expect(SpeechPlanner.sentences(SpeechPlanner.speakable("Dr. Smith arrived at 9 a.m. today. Then he left.")) ==
            ["Dr. Smith arrived at 9 a.m. today.", "Then he left."])
    }

    @Test func markdownIsNeverReadAloud() {
        let said = SpeechPlanner.speakable("""
        ## Plan
        Here is **bold** and `code` and [a site](https://example.com).
        - first item
        - second item
        """)
        #expect(said == "Plan. Here is bold and code and a site. first item. second item.")
        #expect(!said.contains("*") && !said.contains("#") && !said.contains("`"))
    }

    @Test func codeAndTablesAreAnnouncedNotRecited() {
        let said = SpeechPlanner.speakable("Try this:\n```python\nprint(1)\n```\nAnd:\n```\nx = 2\n```\n| a | b |\n|---|---|\n| 1 | 2 |")
        #expect(said == "Try this: I've put the code in the chat. And: There's a table in the chat.")
    }

    @Test func urlsAndEmojiAreSmoothed() {
        #expect(SpeechPlanner.speakable("See https://example.com/x?y=1 for more ✨ details.") == "See a link for more details.")
    }

    @Test func approvalRequestsBecomeOnePlainSentence() {
        let actions = [
            PendingAction(tool: "delete_path", description: "delete file 'old-draft.txt' (6 B)"),
            PendingAction(tool: "update_persona", description: "rename Aura → Nova\n    reason: you asked"),
        ]
        #expect(SpeechPlanner.approvalSentence(actions) ==
            "I need your approval to delete file old-draft.txt, and to rename Aura → Nova.")
    }

    @Test func aBuildRequestSaysWhoBuildsItAndWhatLeavesTheMac() {
        let card = PendingAction(tool: "develop_skill", description: "have Claude Code build the new skill 'pdf_text' ($2.00 total):\n    │ ## Goal",
                                 args: ["name": .string("pdf_text"), "specification": .string("## Goal"), "engine": .string("claude")])
        #expect(SpeechPlanner.approvalSentence([card]) ==
            "I'd like Claude Code to build the pdf text skill. Only the specification on the card goes to Anthropic. I need your approval.")
    }

    @Test func developerHandOffsAndTheirChangesAreSaidPlainly() {
        let handOff = PendingAction(tool: "ask_developer", description: "have Claude Code work on a copy of your whole vault (…):",
                                    args: ["task": .string("Tidy the notes."), "engine": .string("Claude Code")])
        #expect(SpeechPlanner.approvalSentence([handOff]) ==
            "I'd like Claude Code to work on a copy of your vault. The card shows exactly what it gets. I need your approval.")
        let changes = ["a.md", "b.md"].map {
            PendingAction(tool: "apply_change", description: "change '\($0)' (+1 −0):\n    │ +x",
                          args: ["path": .string($0), "change": .string("modify"), "engine": .string("codex")])
        }
        #expect(SpeechPlanner.approvalSentence(changes) ==
            "Codex is done. It made 2 changes in your vault; allow or deny each one on the card.")
    }

    @Test func codeRunsGetASpokenStatusUpdate() {
        let card = PendingAction(tool: "run_python", description: "run Python in the sandbox (no internet, vault read-only, 30-second limit):\n    │ print(1)")
        #expect(SpeechPlanner.approvalSentence([card]) ==
            "I'm preparing to run a Python script in the sandbox, and I need your approval.")
    }
}

@MainActor
struct SpeechControllerTests {
    private func store(speak: Bool, events: [[AuraEvent]], approve: [[AuraEvent]] = []) -> (ChatStore, FakeEngine) {
        let engine = FakeEngine()
        let s = ChatStore()
        s.transport = FakeTransport(chat: events, approve: approve)
        s.speech = SpeechController(engine: engine)
        s.apply(.voice(VoiceProfile(speak: speak)))
        return (s, engine)
    }

    @Test func silentWhileSpokenRepliesAreOff() async {
        let (s, engine) = store(speak: false, events: [[.text("Hello there. "), .text("How are you?"), .done]])
        s.send("Hi")
        await s.turnTask?.value
        #expect(engine.spoken.isEmpty)
    }

    @Test func repliesAreSpokenSentenceBySentence() async {
        let (s, engine) = store(speak: true, events: [[.text("Hello there. How"), .text(" are you?"), .done]])
        s.send("Hi")
        await s.turnTask?.value
        #expect(engine.spoken == ["Hello there.", "How are you?"])
    }

    @Test func textAfterAToolIsANewSegmentAndNothingRepeats() async {
        let (s, engine) = store(speak: true, events: [[
            .text("Let me check."), .status("[Tool: Web_Search_Tool] Searching…"), .text("It's 6°C."), .done,
        ]])
        s.send("Weather?")
        await s.turnTask?.value
        #expect(engine.spoken == ["Let me check.", "It's 6°C."])
    }

    @Test func installedSkillsGetASpokenStatusLineButOtherToolsDont() async {
        let (s, engine) = store(speak: true, events: [[
            .status("[Tool: File_System_Tool] Reading 'a.pdf'..."),
            .status("[Tool: Skill_Tool] Running the pdf_text skill..."),
            .text("It has two pages."), .done,
        ]])
        s.send("Read a.pdf")
        await s.turnTask?.value
        #expect(engine.spoken == ["Running the pdf text skill.", "It has two pages."])
    }

    @Test func skillStatusLinesStaySilentWhenSpeechIsOffOrStopped() async {
        let (quiet, quietEngine) = store(speak: false, events: [[.status("[Tool: Skill_Tool] Running the pdf_text skill..."), .done]])
        quiet.send("Read a.pdf")
        await quiet.turnTask?.value
        #expect(quietEngine.spoken.isEmpty)

        let engine = FakeEngine()
        let speech = SpeechController(engine: engine, profile: VoiceProfile(speak: true))
        speech.beginReply()
        speech.stop()
        speech.announce("Running the pdf_text skill...")
        #expect(engine.spoken.isEmpty)
    }

    @Test func approvalCardsAreReadAloud() async {
        let card = PendingAction(tool: "delete_path", description: "delete file 'old-draft.txt' (6 B)")
        let (s, engine) = store(speak: true, events: [[.approval([card]), .done]])
        s.send("Delete it")
        await s.turnTask?.value
        #expect(engine.spoken == ["I need your approval to delete file old-draft.txt."])
    }

    @Test func aNewMessageOrStopInterruptsAndNeverRestartsTheReply() async {
        let (s, engine) = store(speak: true, events: [[.text("One. Two. Three"), .done]])
        s.send("Count")
        await s.turnTask?.value
        #expect(engine.spoken == ["One.", "Two.", "Three."])  // unpunctuated text is closed off
        engine.spoken.removeAll()
        s.speech?.stop()
        s.speech?.reply("One. Two. Three. Four.", final: true)  // late text after a stop
        #expect(engine.spoken.isEmpty && engine.stops >= 2)  // send() also interrupted
    }

    @Test func turningSpeechOffStopsImmediately() {
        let (s, engine) = store(speak: true, events: [])
        let stops = engine.stops
        s.apply(.voice(VoiceProfile(speak: false)))
        #expect(engine.stops == stops + 1 && s.voice.speak == false)
    }

    @Test func voiceAndPersonaEventsUpdateTheMirror() {
        let s = ChatStore()
        s.apply(.voice(VoiceProfile(voice: "com.apple.voice.compact.en-GB.Daniel", volume: 0.5, speak: true)))
        s.apply(.persona(name: "Nova"))
        #expect(s.voice.volume == 0.5 && s.voice.voice?.hasSuffix("Daniel") == true && s.persona.name == "Nova")
    }

    @Test func voiceAndPersonaEventsParse() {
        var parser = SSEParser()
        let events = [
            "event: voice", #"data: {"voice": null, "speed": 1.2, "pitch": 1.0, "volume": 0.5, "speak": true}"#,
            "event: persona", #"data: {"name": "Nova"}"#,
        ].compactMap { parser.feed($0) }
        #expect(events == [.voice(VoiceProfile(speed: 1.2, volume: 0.5, speak: true)), .persona(name: "Nova")])
    }
}

@MainActor
struct SystemSpeechEngineTests {
    @Test func catalogListsInstalledVoicesWithQualityAndNoveltyFlags() {
        let catalog = SystemSpeechEngine.catalog()
        #expect(!catalog.isEmpty)
        #expect(catalog.allSatisfy { ["premium", "enhanced", "default"].contains($0.quality) })
        #expect(catalog.contains { $0.language.hasPrefix("en") && !$0.novelty })
    }

    @Test func rendersSpeechSilently() async {
        // Synthesizes to memory buffers: proves the engine works without making a sound.
        let seconds = await SystemSpeechEngine.renderSilently("It's five point three degrees in London.", profile: VoiceProfile())
        #expect(seconds > 1 && seconds < 10)
        let faster = await SystemSpeechEngine.renderSilently("It's five point three degrees in London.", profile: VoiceProfile(speed: 1.8))
        #expect(faster < seconds)  // speed really changes the speech
    }
}
