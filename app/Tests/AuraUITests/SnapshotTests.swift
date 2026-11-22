import AppKit
@testable import AuraKit
import SwiftUI
import Testing
@testable import AuraUI

/// UI integrity: every panel state renders, at the panel's width, in light and dark mode.
/// PNGs go to $AURA_SNAPSHOT_DIR (default: a temp folder) for visual review.
///
/// Offscreen rendering can't sample the desktop, so a material over a starfield stands in for
/// the panel's live system glass (NSGlassEffectView).
@MainActor
struct SnapshotTests {
    static let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    static let outDir: URL = {
        let dir = ProcessInfo.processInfo.environment["AURA_SNAPSHOT_DIR"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.temporaryDirectory.appending(path: "aura-snapshots")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    /// Same face crop as scripts/build-assets.sh.
    static let portrait = NSImage(contentsOf: repo.appending(path: "assets/Aura.jpeg"))?
        .cgImage(forProposedRect: nil, context: nil, hints: nil)
    static let avatar: Image? = portrait?.cropping(to: CGRect(x: 1085, y: 215, width: 760, height: 760))
        .map { Image(decorative: $0, scale: 1) }
    static let backdrop: Image? = portrait?.cropping(to: CGRect(x: 2000, y: 0, width: 752, height: 1536))
        .map { Image(decorative: $0, scale: 1) }

    static let reply = """
    ## London right now
    It's **5.3°C** and overcast, feeling like *2.1°C* in the easterly wind.

    1. Take a warm coat
    2. Expect light rain later
       - umbrella advised

    | Time | Temp |
    |------|------|
    | Now | 5.3°C |
    | 18:00 | 4°C |

    ```python
    print(f"{5.3 * 9 / 5 + 32:.1f}°F")
    ```
    Source: `weatherapi.com`
    """

    @discardableResult
    private func render(_ name: String, _ store: ChatStore, dark: Bool = false, theme: Theme = .glass) throws -> CGSize {
        let panel = VStack(spacing: 0) {
            HeaderView(store: store, avatar: Self.avatar)
            if case let .offline(message) = store.connection {
                OfflineBanner(message: message, onRestart: {})
            }
            if store.items.isEmpty, store.phase == .idle {
                WelcomeView(avatar: Self.avatar).frame(height: 360)
            } else {
                TranscriptContent(store: store, avatar: Self.avatar, lazy: false)
            }
            InputBar(store: store)
        }
        .frame(width: 420)
        .background { ThemeBackdrop(theme: theme, image: Self.backdrop) }
        .background(.regularMaterial)
        .background { Self.backdrop?.resizable().scaledToFill().blur(radius: 30) }
        .clipShape(.rect(cornerRadius: 20))
        .environment(\.colorScheme, dark ? .dark : .light)

        let renderer = ImageRenderer(content: panel)
        renderer.scale = 2
        let image = try #require(renderer.cgImage, "\(name) did not render")
        let png = try #require(NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]))
        try png.write(to: Self.outDir.appending(path: "\(name).png"))
        return CGSize(width: image.width, height: image.height)
    }

    private func item(_ role: ChatStore.Item.Role, _ text: String = "", actions: [PendingAction] = [], decisions: [Bool]? = nil) -> ChatStore.Item {
        ChatStore.Item(role: role, text: text, actions: actions, decisions: decisions)
    }

    private var conversation: [ChatStore.Item] {
        [
            item(.user, "What's the weather in London right now?"),
            item(.status, "[Tool: Web_Search_Tool] Searching for 'current weather in London'..."),
            item(.aura, Self.reply),
        ]
    }

    @Test func welcome() throws {
        let size = try render("1-welcome", ChatStore(items: []))
        #expect(size.width == 840 && size.height > 500)
    }

    @Test func conversationWithMarkdownLightAndDark() throws {
        let light = try render("2-conversation-light", ChatStore(items: conversation))
        let dark = try render("3-conversation-dark", ChatStore(items: conversation), dark: true)
        #expect(light == dark)
        #expect(light.height > 900)  // heading, list, table and code all laid out as blocks
    }

    @Test func thinkingAndWaking() throws {
        try render("4-thinking", ChatStore(items: [item(.user, "Summarise my notes.txt")], phase: .thinking))
        try render("5-waking", ChatStore(items: [item(.user, "Good morning, Aura")], phase: .waking))
    }

    @Test func approvalPendingThenDenied() throws {
        let card = PendingAction(tool: "delete_path", description: "delete file 'old-draft.txt' (6 B)")
        try render("6-approval", ChatStore(
            items: [item(.user, "Delete old-draft.txt"), item(.approval, actions: [card])],
            phase: .awaitingApproval))
        try render("7-denied", ChatStore(items: [
            item(.user, "Delete old-draft.txt"),
            item(.approval, actions: [card], decisions: [false]),
            item(.aura, "[Action Denied by User]. I will not perform that action. How else can I help?"),
        ]))
    }

    @Test func offlineAndErrorStatesUsePlainLanguage() throws {
        let store = ChatStore(
            items: [item(.user, "Hello?"), item(.error, FriendlyMessage.turnFailed(URLError(.cannotConnectToHost)))],
            connection: .offline(FriendlyMessage.offline))
        try render("8-offline", store)
        #expect(store.connection == .offline("Aura is offline. Please check your connection."))
        #expect(store.items.last?.text == "Aura went offline before answering. Try again once it's back.")
    }

    @Test func voiceAndPersonaFlow() throws {
        let store = ChatStore(items: [
            item(.user, "Speak more softly, and always use British spelling."),
            item(.status, "[Tool: Voice_Tool] Adjusting my voice..."),
            item(.aura, "Done, I'll speak more softly."),
            item(.approval, actions: [PendingAction(tool: "update_persona", description: "change Aura's constraints:\n    + Always use British spelling.\n    reason: you asked me to")], decisions: [true]),
            item(.status, "[Tool: Persona_Tool] Updating my constraints..."),
            item(.aura, "Noted: I'll use British **colour** and **realise** from now on."),
        ])
        store.apply(.voice(VoiceProfile(volume: 0.5, speak: true)))
        try render("9-voice-persona", store)
        #expect(store.voice.speak)
    }

    @Test func starfieldTheme() throws {
        try render("10-starfield", ChatStore(items: conversation), dark: true, theme: .starfield)
    }

    @Test func settingsVoiceGrouping() throws {
        let store = ChatStore(items: [])
        let catalog = [
            VoiceInfo(id: "kokoro:af_heart", name: "Heart", language: "en-US", quality: "neural", gender: "female", novelty: false),
            VoiceInfo(id: "kokoro:bf_emma", name: "Emma", language: "en-GB", quality: "neural", gender: "female", novelty: false),
            VoiceInfo(id: "v6", name: "Voice 6", language: "en-US", quality: "enhanced", gender: "female", novelty: false),
            VoiceInfo(id: "sam", name: "Samantha", language: "en-US", quality: "default", gender: "female", novelty: false),
            VoiceInfo(id: "bells", name: "Bells", language: "en-US", quality: "default", gender: "unknown", novelty: true),
        ]
        let groups = VoiceGroups(catalog, languages: ["en-US"]).sections
        #expect(groups.map(\.title) == ["Aura voices", "macOS: recommended", "macOS: more voices"])
        #expect(groups.flatMap(\.voices).map(\.name) == ["Heart", "Emma", "Voice 6", "Samantha"])  // novelty hidden
        // The popover itself is native form controls, which can't render offscreen; its
        // grouping logic is what's checked here.
        _ = SettingsView(store: store).body
    }

    @Test func workshopAuditCard() throws {
        let green = AuditReportInfo(
            skill: "pdf_text", version: "1.0.0", purpose: "Extract the text of a PDF file in the vault, page by page.",
            findings: [
                Finding(check: "write-scope", severity: "pass", message: "only skills/pdf_text/ changed (3 files)"),
                Finding(check: "protected", severity: "pass", message: "the constitution, core and Governor are untouched"),
                Finding(check: "manifest", severity: "pass", message: "valid manifest; vault access: read; network: none"),
                Finding(check: "static-scan", severity: "pass", message: "no network, system or dynamic-code use found"),
                Finding(check: "provenance", severity: "pass", message: "pypdf==7.1.1: 12 years on PyPI, 127 releases, ready-built package"),
            ],
            tests: [TestResult(name: "test_extracts_text_page_by_page", status: "passed"),
                    TestResult(name: "test_refuses_paths_outside_the_vault", status: "passed")],
            suite: SuiteResult(passed: 213, failed: 0, summary: "213 passed, 1 skipped"), risk: "green", blocked: false)
        let red = AuditReportInfo(
            skill: "yaml_tool", version: "0.1.0", purpose: "Parse YAML files.",
            findings: [Finding(check: "provenance", severity: "red", message: "4 known vulnerabilities in pyyaml 5.3",
                               detail: "GHSA-6757-jp84-gxfx, GHSA-8q59-q68h-6hv4, PYSEC-2020-96"),
                       Finding(check: "static-scan", severity: "red", message: "uses the network (requests)", detail: "skills/yaml_tool/skill.py:3")],
            tests: [], suite: SuiteResult(passed: nil, failed: nil, summary: nil), risk: "red", blocked: true)
        let diff = FileDiff(file: "skill.py", status: "modified", rows: [
            DiffRow(left: "def run(args: dict) -> str:", right: "def run(args: dict) -> str:", kind: "same"),
            DiffRow(left: "    return extract(path)", right: "    return extract(path, limit=LIMIT)", kind: "changed"),
            DiffRow(left: nil, right: "LIMIT = 12_000", kind: "added"),
            DiffRow(left: "import os", right: nil, kind: "removed"),
        ])
        let view = VStack(alignment: .leading, spacing: 16) {
            HStack { Text("pdf_text 1.0.0").font(.title3.weight(.semibold)); Spacer(); RiskBadge(risk: "green") }
            AuditFindings(report: green)
            HStack { Text("yaml_tool 0.1.0").font(.title3.weight(.semibold)); Spacer(); RiskBadge(risk: "red") }
            AuditFindings(report: red)
            DiffTable(diff: diff)
        }
        .padding(20)
        .frame(width: 760)
        .background(.background)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try #require(renderer.cgImage)
        let png = try #require(NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]))
        try png.write(to: Self.outDir.appending(path: "12-workshop-audit-card.png"))
        #expect(image.width == 1520)
    }

    @Test func toolStatusReadsLikeASentence() {
        #expect(ToolStatusView.readable("[Tool: File_System_Tool] Reading 'budget.csv'...") == "Reading 'budget.csv'…")
        #expect(ToolStatusView.readable("Plain status") == "Plain status")
    }
}
