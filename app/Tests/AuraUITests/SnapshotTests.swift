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
    static let welcome: Image? = NSImage(contentsOf: repo.appending(path: "assets/Welcome.jpeg"))
        .map { Image(nsImage: $0) }
    static let wordmark: Image? = NSImage(contentsOf: repo.appending(path: "assets/Header.png"))
        .map { Image(nsImage: $0) }

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
    private func render(_ name: String, _ store: ChatStore, dark: Bool = false, theme: Theme = .glass,
                        text: ChatTextStyle = .standard, bubbles: BubbleColors = .standard) throws -> CGSize {
        let panel = VStack(spacing: 0) {
            HeaderView(store: store, avatar: Self.avatar, wordmark: Self.wordmark)
            if case let .offline(message) = store.connection {
                OfflineBanner(message: message, onRestart: {})
            }
            if store.items.isEmpty, store.phase == .idle {
                WelcomeView(avatar: Self.avatar, image: Self.welcome, starting: store.connection == .starting).frame(height: 400)
            } else {
                TranscriptContent(store: store, avatar: Self.avatar, lazy: false)
            }
            if let plan = store.activePlan { PlanProgressLine(plan: plan) }
            InputBar(store: store)
        }
        .frame(width: 420)
        .background { ThemeBackdrop(theme: theme, image: Self.backdrop) }
        .background(.regularMaterial)
        .background { Self.backdrop?.resizable().scaledToFill().blur(radius: 30) }
        .clipShape(.rect(cornerRadius: 20))
        .environment(\.colorScheme, dark ? .dark : .light)
        .environment(\.rendersOffscreen, true)
        .environment(\.chatText, text)
        .environment(\.bubbles, bubbles)

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

    @Test func longUnbrokenTextStaysInsideItsBubbles() throws {
        let url = "https://example.com/" + String(repeating: "averyveryverylongpathsegment/", count: 5) + "end"
        let store = ChatStore(items: [
            item(.user, "Open \(url) please"),
            item(.aura, "Here it is: \(url)\n\n`\(String(repeating: "x", count: 120))`"),
        ])
        let size = try render("37-long-unbroken-text", store)
        #expect(size.width == 840)  // the panel didn't grow to fit the text
    }

    @Test func headerShowsMemoryAndTheLastLocalSpeed() throws {
        let store = ChatStore(items: conversation, phase: .streaming)  // mid-reply: the stage bar shows
        store.apply(.performance(LocalModelPerformance(totalDuration: 4.2, promptDuration: 1.1, promptTokens: 2400,
                                                       promptTokensPerSecond: 2181.8, generationDuration: 3.1,
                                                       outputTokens: 99, outputTokensPerSecond: 31.9)))
        try render("38-header-usage", store)
        #expect(store.localPerformance?.outputTokensPerSecond == 31.9)
    }

    @Test func memoryInUseIsAPlausibleShareOfTheMac() {
        let used = AuraRuntimeUsage.memoryInUse() ?? 0
        #expect(used > 256 * 1_048_576 && used < ProcessInfo.processInfo.physicalMemory)
    }

    /// The Models window's four pages: Roles holds the role pickers (step 2); Cloud shows today's
    /// connection, read-only, until step 4.
    @Test func theModelsWindowHasItsFourPages() throws {
        #expect(ModelsPage.allCases.map(\.title) == ["Roles", "Local", "Cloud", "Model settings"])
        let catalog = ModelCatalog(
            roles: ["startup": ModelChoice(provider: "ollama", model: "qwen3.5:9b"),
                    "chat": ModelChoice(provider: "ollama", model: "gemma4:26b"),
                    "web_search": ModelChoice(provider: "llamacpp", model: "Ternary-Bonsai-2.5-27B-Q2_0"),
                    "deep_research": ModelChoice(provider: "anthropic", model: "claude-opus-5-5")],
            roleLabels: ["startup": "Model on startup", "chat": "Chat", "web_search": "Web Search", "deep_research": "Deep Research"],
            ollamaModels: ["gemma4:26b", "qwen3.5:9b"], anthropicModels: ["claude-opus-5-5", "claude-sonnet-5-5"],
            anthropicKeySet: true, codingEngine: "claude", sleepMode: SleepMode(model: "qwen3.5:9b"), startupModelProblem: nil)
        let store = ChatStore(items: [])
        store.localModels = LocalModels(
            ollama: .init(answering: true, models: [.init(name: "gemma4:26b", gb: 17.0, loaded: true),
                                                    .init(name: "qwen3.5:9b", gb: 6.6, loaded: false)]),
            llamacpp: .init(installed: true, folder: "/Users/you/models",
                            models: [.init(name: "Ternary-Bonsai-2.5-27B-Q2_0", gb: 7.1, loaded: false)],
                            server: .init(running: false, model: nil)),
            sleepMode: SleepMode(model: "qwen3.5:9b"), idleUnload: "10m")
        store.secrets = [SecretInfo(name: "anthropic_api_key", label: "Anthropic API key (Aura cloud models)", set: true,
                                    hint: "sk-ant-…1234")]
        for page in ModelsPage.allCases {
            let size = try renderWide("39-models-\(page.rawValue)", width: 640,
                                      ModelsPageContent(page: page, catalog: catalog, store: store))
            #expect(size.height > 200)
        }
    }

    /// Step 6: a Claude model greys out the sampling fields; Bonsai shows its recommended values.
    @Test func theModelSettingsPageGreysOutWhatAProviderRejects() throws {
        let catalog = ModelCatalog(
            roles: ["chat": ModelChoice(provider: "anthropic", model: "claude-sonnet-5-5")], roleLabels: [:],
            ollamaModels: ["qwen3.5:9b"], anthropicModels: ["claude-sonnet-5-5"], anthropicKeySet: true,
            codingEngine: "claude", sleepMode: SleepMode(model: ""), startupModelProblem: nil,
            llamacppModels: ["Ternary-Bonsai-2.5-27B-Q2_0"])
        let ranges: [String: [Double]] = ["temperature": [0, 2], "top_p": [0, 1], "top_k": [1, 200], "min_p": [0, 1],
                                          "presence_penalty": [-2, 2], "repeat_penalty": [0.5, 2],
                                          "context_budget": [4096, 262_144], "max_reply_tokens": [1024, 32768]]
        let claude = ModelSettingsInfo(provider: "anthropic", model: "claude-sonnet-5-5", saved: ["effort": .string("high")],
                                       effective: ["effort": .string("high")], recommended: [:],
                                       accepted: ["context_budget", "effort", "max_reply_tokens"],
                                       efforts: ["low", "medium", "high", "max"], ranges: ranges)
        let bonsaiValues: [String: JSONValue] = ["temperature": .number(0.7), "top_p": .number(0.8), "top_k": .number(20)]
        let bonsai = ModelSettingsInfo(provider: "llamacpp", model: "Ternary-Bonsai-2.5-27B-Q2_0", saved: [:],
                                       effective: bonsaiValues, recommended: bonsaiValues,
                                       accepted: ["temperature", "top_p", "top_k", "min_p", "presence_penalty",
                                                  "repeat_penalty", "effort", "context_budget", "max_reply_tokens"],
                                       efforts: ["none", "low", "medium", "high"], ranges: ranges)
        for (name, info) in [("claude", claude), ("bonsai", bonsai)] {
            let page = ModelSettingsPage(store: ChatStore(items: []), catalog: catalog, navigation: ModelsNavigation(), initial: info)
            #expect(try renderWide("40-model-settings-\(name)", width: 640, page.padding(24)).height > 400)
        }
        #expect(ModelSettingsPage.texts(["top_k": .number(20), "temperature": .number(0.7)]) == ["top_k": "20", "temperature": "0.7"])
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

    @Test func richBubbleRendersChartAndExternalApprovalCard() throws {
        let rich = """
        ## Weekly activity

        ```chart
        Day,Messages
        Mon,12
        Tue,19
        Wed,7
        ```

        Watch the [walkthrough](https://video.example.com/demo.mp4) when you choose to allow it.
        Legacy documentation is at http://docs.example.com/archive.
        """
        let size = try render("36-rich-bubble", ChatStore(items: [item(.aura, rich)]), dark: true)
        #expect(size.height > 500)  // chart plus HTTPS and HTTP approval cards render
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

    @Test func planCardPendingAnsweredAndRunning() throws {
        let plan = PlanProposal(goal: "Write a research note on tides", steps: [
            .init(id: "s1", does: "Find what the vault already has", tool: "search_vault", doneWhen: "matching notes listed"),
            .init(id: "s2", does: "Search for recent results", tool: "web_search", doneWhen: "three sources"),
            .init(id: "s3", does: "Draft the note", tool: "think", after: ["s1", "s2"], doneWhen: "a draft citing each source"),
            .init(id: "s4", does: "Save it to Knowledge/", tool: "write_file", after: ["s3"], doneWhen: "the note is written"),
        ], replaces: .init(id: "p-1a2b3c4d", goal: "Tidy the Ideas area"))
        let card = PendingAction(tool: "make_plan", description: "Start this plan? Write a research note on tides", plan: plan)
        let ask = item(.user, "Make a plan for a research note on tides.")
        let pending = try render("39-plan-card", ChatStore(items: [ask, item(.approval, actions: [card])], phase: .awaitingApproval))
        #expect(pending.height > 700)  // four steps, the replace note and the buttons all drew
        let running = ChatStore(items: [ask, item(.approval, actions: [card], decisions: [true])])
        running.activePlan = ActivePlan(id: "p-9", goal: plan.goal, done: 1, total: 4, steps: [
            .init(id: "s1", does: "Find what the vault already has", status: "done"),
            .init(id: "s2", does: "Search for recent results", status: "waiting"),
            .init(id: "s3", does: "Draft the note", status: "pending"),
            .init(id: "s4", does: "Save it to Knowledge/", status: "pending"),
        ])
        try render("40-plan-started", running, dark: true)
        let write = PendingAction(tool: "write_file", description: "write 'Knowledge/tides.md' (412 B, new file)",
                                  step: .init(plan: "p-9", goal: plan.goal, step: "s4", index: 4, total: 4, does: "Save it to Knowledge/"))
        let stepCard = try render("41-plan-step-card", ChatStore(items: [ask, item(.approval, actions: [write])], phase: .awaitingApproval))
        #expect(stepCard.height > 400)
        #expect(running.activePlan?.current?.id == "s2")
    }

    @Test func claudeCodeFromTheChatBox() throws {
        let task = "Move forms.md into The_Absolute_R/ and add it to the Map of the Ascent's index."
        let handOff = PendingAction(tool: "ask_developer", description: """
            have Claude Code work on a copy of your whole vault (your Claude plan, up to 10 minutes):
                Sent to Anthropic: this task and a copy of your whole vault (4 files, 4.1 KB), which Claude Code reads. Nothing else from your Mac or this conversation.
                Then you allow or deny each file it changed. Nothing is written to your vault before that.
                │ \(task)
            """, args: ["task": .string(task), "engine": .string("claude")])
        let changes = [
            PendingAction(tool: "apply_change", description: """
                change 'Map_of_the_Ascent.md' (+1 −0):
                    │ @@ -24,3 +24,4 @@
                    │  ## Index
                    │  - [The Architecture of Emergence](<Conversations/The_Architecture_of_Emergence.md>)
                    │ +- [The Forms](<The_Absolute_R/forms.md>)
                """, args: ["path": .string("Map_of_the_Ascent.md"), "change": .string("modify"), "engine": .string("claude")]),
            PendingAction(tool: "apply_change", description: "create 'The_Absolute_R/forms.md' (31 B, +2 lines):\n    │ +# The Forms\n    │ +The Good, the True.",
                          args: ["path": .string("The_Absolute_R/forms.md"), "change": .string("create"), "engine": .string("claude")]),
            PendingAction(tool: "apply_change", description: "delete 'forms.md' (31 B, −2 lines)",
                          args: ["path": .string("forms.md"), "change": .string("delete"), "engine": .string("claude")]),
        ]
        var ask = item(.user, "File my forms note where it belongs.")
        ask.developer = "claude"
        let store = ChatStore(items: [
            ask,
            item(.approval, actions: [handOff], decisions: [true]),
            item(.status, "[Tool: Developer] Handing the task to Claude Code..."),
            item(.status, "[Tool: Developer] · Claude Code: Writing The_Absolute_R/forms.md"),
            item(.approval, actions: changes),
        ], phase: .awaitingApproval)
        store.developer = "claude"
        try render("30-claude-code-from-chat", store)
        let card = FileChangesCard(item: item(.approval, actions: changes), store: store, openAll: true)
        try renderWide("31-file-changes", width: 520, card.padding())
        try renderWide("32-file-changes-dark", width: 520, dark: true, card.padding())
        try renderWide("34-developer-chips", width: 300, HStack { DeveloperChip(developer: nil); DeveloperChip(developer: "claude"); DeveloperChip(developer: "codex") }.padding())
        try render("35-hand-off-pending", ChatStore(items: [ask, item(.approval, actions: [handOff])], phase: .awaitingApproval), dark: true)
        let parsed = try #require(handOff.developerTask)
        #expect(parsed.engine == "claude" && parsed.task == task && parsed.notes.count == 2)
        #expect(parsed.summary == "have Claude Code work on a copy of your whole vault (your Claude plan, up to 10 minutes)")
        var answered = item(.approval, actions: changes, decisions: [true, true, false])
        answered.edited = false
        try renderWide("33-file-changes-answered", width: 520, FileChangesCard(item: answered, store: store).padding())
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

    @Test func continuousAuditAlert() throws {
        let alert = SkillAlert(
            skill: "pdf_text",
            message: "Warning: The pdf_text skill has been flagged with a new vulnerability. Would you like to uninstall it?",
            findings: [Finding(check: "provenance", severity: "red", message: "4 known vulnerabilities in pyyaml 5.3",
                               detail: "GHSA-6757-jp84-gxfx, GHSA-8q59-q68h-6hv4, PYSEC-2020-96, PYSEC-2021-142")])
        let view = VStack(alignment: .leading, spacing: 16) {
            AlertBanner(alert: alert, onKeep: {}, onUninstall: {})
            SecurityCheckRow(clock: AuditClock(lastQuick: ISO8601DateFormatter().string(from: .now.addingTimeInterval(-300))), onCheck: {})
                .font(.callout).frame(width: 260)
            SecurityCheckRow(clock: AuditClock(lastError: "ConnectError: no network"), onCheck: {})
                .font(.callout).frame(width: 260)
        }
        .padding(20)
        .frame(width: 760)
        .background(.background)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try #require(renderer.cgImage)
        let png = try #require(NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]))
        try png.write(to: Self.outDir.appending(path: "13-continuous-audit-alert.png"))
        #expect(image.width == 1520)
    }

    // MARK: - Sprint 6: the Developer

    static let spec = """
    ## Goal
    Extract the text of a PDF in the vault, page by page.
    ## Inputs
    path: the PDF's path in the vault.
    ## Output
    The text, with a '--- page N ---' line before each page.
    ## Steps
    Open the file under /vault, read each page with pypdf, join the pages.
    ## Acceptance tests
    A two-page PDF with 'Hello' and 'World' gives both words, in order, under page headings.
    ## Constraints
    No network. Vault read-only. pypdf==7.1.1 only.
    """

    static let specCard = PendingAction(
        tool: "develop_skill",
        description: """
        have Claude Code build the new skill 'pdf_text' ($2.00 total, 10 minutes per round, up to 2 repair rounds):
            Sent to Anthropic: this specification, the skill format guide and an example skill. Nothing from your vault or this conversation.
            The result must pass the Security Governor; you review it in the Workshop before anything is installed.
        """ + spec.split(separator: "\n").map { "\n    │ \($0)" }.joined(),
        args: ["name": .string("pdf_text"), "specification": .string(spec), "engine": .string("claude"), "improve": .bool(false)])

    @Test func specificationApprovalCard() throws {
        let ask = item(.user, "Can you get Claude Code to build a skill that pulls the text out of PDFs in my vault?")
        let size = try render("14-spec-approval", ChatStore(items: [ask, item(.approval, actions: [Self.specCard])], phase: .awaitingApproval))
        #expect(size.height > 900)  // the specification is shown in full, not truncated
        var approved = item(.approval, actions: [Self.specCard], decisions: [true])
        approved.edited = true
        try render("15-spec-approved", ChatStore(items: [
            ask, approved,
            item(.build, "pdf_text", actions: [PendingAction(tool: "build", description: "b-1234abcd")]),
            item(.aura, "Claude Code is building **pdf_text** now. I'll let you know when it's ready for your review."),
        ]), dark: true)
        #expect(SpecificationApprovalCard.outcome([true], edited: true) == "Approved with your edits")
        #expect(SpecificationApprovalCard.outcome([false], edited: false) == "Denied: nothing was sent")
        let proposal = try #require(Self.specCard.build)
        #expect(SpecificationApprovalCard.summary(proposal, engine: "claude") == proposal.summary)
        #expect(SpecificationApprovalCard.summary(proposal, engine: "codex") == "have Codex build the new skill 'pdf_text':")
        #expect(BuildView.title(BuildInfo(id: "b", skill: "pdf_text", stage: "ready")) == "Built pdf_text")
        #expect(BuildView.title(BuildInfo(id: "b", skill: "pdf_text", improve: true, stage: "developing")) == "Improving pdf_text")
    }

    private func renderWide(_ name: String, width: CGFloat = 760, dark: Bool = false, _ view: some View) throws -> CGSize {
        let framed = view.frame(width: width).background(.background)
            .environment(\.colorScheme, dark ? .dark : .light)
            .environment(\.rendersOffscreen, true)
        let renderer = ImageRenderer(content: framed)
        renderer.scale = 2
        let image = try #require(renderer.cgImage, "\(name) did not render")
        let png = try #require(NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]))
        try png.write(to: Self.outDir.appending(path: "\(name).png"))
        return CGSize(width: image.width, height: image.height)
    }

    @Test func holPreflightMakesTheContainerBoundaryAndInputStateVisible() throws {
        let approved = HOLOverview(
            snapshot: String(repeating: "a", count: 64),
            vaultProject: "/Users/example/Aura_Vault/Aura",
            sessions: [HOLSession(name: "aura_session_review", purpose: "Review Aura changes.", runner: "codex", prompt: "")],
            savepoints: [], defaults: HOLDefaults(model: ModelChoice(provider: "ollama", model: "qwen"), effort: "medium"),
            catalog: ModelCatalog(roles: [:], roleLabels: [:], ollamaModels: ["qwen"], anthropicModels: [], anthropicKeySet: false, codingEngine: "claude", sleepMode: SleepMode(model: ""), startupModelProblem: nil), notice: "Approved sessions only.")
        let terminal = HOLTerminalState(id: "abc123", running: true, output: [], next: 0)

        #expect(HOLTerminalReadiness(overview: nil, terminal: nil, busy: false).badge == "CONNECTING")
        #expect(HOLTerminalReadiness(overview: HOLOverview(snapshot: String(repeating: "b", count: 64), vaultProject: "", sessions: [], savepoints: [], defaults: approved.defaults, catalog: approved.catalog, notice: ""), terminal: nil, busy: false).badge == "BLOCKED")
        #expect(HOLTerminalReadiness(overview: approved, terminal: nil, busy: false).badge == "STANDBY")
        let active = HOLTerminalReadiness(overview: approved, terminal: terminal, busy: false)
        #expect(active.badge == "INPUT READY" && active.terminalID == "abc123")
        #expect(active.instruction.contains("Vault container"))
    }

    @Test func workshopBuildLiveAndReady() throws {
        let t = { (s: Int) in ISO8601DateFormatter().string(from: Date(timeIntervalSince1970: 1_795_000_000 + Double(s))) }
        let running = BuildInfo(
            id: "b-1234abcd", skill: "pdf_text", engine: "claude", specification: Self.spec, stage: "developing",
            round: 2, maxRounds: 3, costUsd: 0.41, budgetUsd: 2, message: "Claude Code is writing the skill (round 2 of 3)…",
            history: [BuildRound(round: 1, task: "1a2b3c4d", passed: false,
                                 findings: ["test test_refuses_paths_outside_the_vault failed: AssertionError: no error raised"])],
            feed: [FeedLine(t: t(0), text: "Claude Code is writing the skill (round 1 of 3)…"),
                   FeedLine(t: t(4), text: "Reading SPEC.md"), FeedLine(t: t(6), text: "Reading SKILL_FORMAT.md"),
                   FeedLine(t: t(19), text: "Writing skill/skill.py"), FeedLine(t: t(25), text: "Running: python3 run_tests.py"),
                   FeedLine(t: t(31), text: "The Security Governor is auditing round 1 (tests included)…"),
                   FeedLine(t: t(74), text: "Round 1 didn't pass (1 finding); sent back to Claude Code."),
                   FeedLine(t: t(76), text: "Claude Code is writing the skill (round 2 of 3)…"),
                   FeedLine(t: t(83), text: "Editing skill/skill.py"),
                   FeedLine(t: t(90), text: "“Paths are now resolved against /vault and anything outside it is refused.”")])
        let size = try renderWide("16-workshop-build", BuildView(build: running, onStop: {}, onReview: {}))
        #expect(size.width == 1520)
        let ready = BuildInfo(id: "b-1234abcd", skill: "pdf_text", engine: "claude", specification: Self.spec, stage: "ready",
                              round: 2, maxRounds: 3, costUsd: 0.63, budgetUsd: 2, message: "Ready for your review: passed every check in round 2.",
                              taskId: "9f8e7d6c",
                              history: running.history + [BuildRound(round: 2, task: "9f8e7d6c", passed: true, risk: "green")],
                              feed: running.feed + [FeedLine(t: t(140), text: "Ready for your review: passed every check in round 2.")])
        try renderWide("17-workshop-build-ready", dark: true, BuildView(build: ready, onStop: {}, onReview: {}))
        try renderWide("18-built-by", VStack { BuiltByCard(origin: TaskOrigin(build: "b-1234abcd", engine: "Claude Code", round: 2,
                                                                                   costUsd: 0.63, specification: Self.spec)) }.padding(20))
        let sidebar = VStack(alignment: .leading, spacing: 10) {
            BuildRow(build: running)
            BuildRow(build: ready)
            BuildRow(build: BuildInfo(id: "b-2", skill: "word_count", engine: "codex", stage: "deployed", budgetUsd: 0))
            BuildRow(build: BuildInfo(id: "b-3", skill: "csv_stats", engine: "codex", stage: "failed", budgetUsd: 0))
        }.padding(12)
        try renderWide("19-workshop-build-rows", width: 300, sidebar)
    }

    @Test func workstationShowsRegistryHealthAndEveryAgentCollection() throws {
        let health = AgentGovernanceHealth(state: "attention",
                                           agents: ["active": 1, "suspended": 1, "expired": 0, "due": 1],
                                           detail: "2 agent record(s) need review")
        let store = WorkshopStore(
            agents: [
                AgentInfo(name: "research_scout", version: "1.0.0", purpose: "Summarize public sources.", origin: "imported", error: nil, state: "active", reviewBy: "2027-02-22T00:00:00+00:00"),
                AgentInfo(name: "old_scout", version: "1.0.0", purpose: "Needs a fresh audit.", origin: "created", error: nil, state: "suspended"),
            ],
            agentInbox: [AgentInfo(name: "candidate", version: "1.0.0", purpose: "Awaiting audit.", origin: "imported", error: nil, state: "candidate")],
            retiredAgents: [AgentInfo(name: "retired_scout", version: "1.0.0", purpose: "Archived history.", origin: "created", error: nil, state: "retired")],
            agentHealth: health
        )
        let size = try renderWide("19b-workstation", width: 1040, WorkstationView(store: store))
        #expect(size.width == 2080 && size.height > 500)
    }

    @Test func newSkillSheetAndKeys() throws {
        let store = WorkshopStore(developer: DeveloperInfo(engines: [
            EngineInfo(name: "claude", label: "Claude Code", vendor: "Anthropic", capsCost: true, problem: "Add an Anthropic API key in Settings → Keys."),
            EngineInfo(name: "codex", label: "Codex", vendor: "OpenAI", capsCost: false),
        ]))
        try renderWide("20-new-skill-sheet", width: 620, NewBuildSheet(store: store, improve: nil))
        let chat = ChatStore(items: [])
        let keys = VStack(alignment: .leading, spacing: 14) {
            KeyRow(secret: SecretInfo(name: "anthropic_api_key", label: "Anthropic API key (Claude Code, the Developer)", set: false), store: chat)
            KeyRow(secret: SecretInfo(name: "tavily_api_key", label: "Tavily API key (web search)", set: true, hint: "tvly-…3f9a"), store: chat)
        }.padding(16)
        try renderWide("21-keys", width: 360, keys)
    }

    // MARK: - chat appearance (font, size, bubble colors)

    @Test func chatAppearanceLargeSerifAndCustomColors() throws {
        let standard = try render("22-bubbles-default", ChatStore(items: conversation))
        let custom = try render("23-bubbles-custom", ChatStore(items: conversation), dark: true,
                                text: ChatTextStyle(size: 17, style: .serif),
                                bubbles: ChatAppearance.bubbles(user: "#0F8B8D", aura: "#F2E8FF"))
        #expect(custom.height > standard.height)  // larger text, taller conversation
        let preview = VStack(alignment: .leading, spacing: 12) {
            BubblePreview(text: .standard, colors: .standard)
            BubblePreview(text: ChatTextStyle(size: 15, style: .rounded), colors: ChatAppearance.bubbles(user: "#2E9E4F", aura: "#5B6270"))
        }.padding(12)
        let renderer = ImageRenderer(content: preview.frame(width: 360).background(.background))
        renderer.scale = 2
        let image = try #require(renderer.cgImage)
        let png = try #require(NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]))
        try png.write(to: Self.outDir.appending(path: "24-bubble-preview.png"))
    }

    @Test func colorsRoundTripAndTextStaysReadable() {
        #expect(ChatAppearance.hex(ChatAppearance.color(hex: "#7C4DFF")!) == "#7C4DFF")
        #expect(ChatAppearance.color(hex: "") == nil && ChatAppearance.color(hex: "#12345") == nil && ChatAppearance.color(hex: "zzzzzz") == nil)
        #expect(ChatAppearance.prefersWhiteText(r: 124 / 255, g: 77 / 255, b: 1))  // the default violet: white text
        #expect(ChatAppearance.prefersWhiteText(r: 0, g: 122 / 255, b: 1))  // the system blue: white text
        #expect(!ChatAppearance.prefersWhiteText(r: 242 / 255, g: 232 / 255, b: 1))  // a pale lilac: black text
        #expect(!ChatAppearance.prefersWhiteText(r: 1, g: 0.85, b: 0.2))  // yellow: black text
        for swatch in ChatAppearance.swatches where !swatch.hex.isEmpty && swatch.hex != "#FFFFFF" {  // dark presets: white text at AA (4.5:1)
            let c = ChatAppearance.rgb(swatch.hex)!
            #expect(ChatAppearance.contrastWithWhite(r: c.r, g: c.g, b: c.b) >= 4.5, "\(swatch.name)")
        }
        #expect(!ChatAppearance.prefersWhiteText(r: 1, g: 1, b: 1))  // White swatch intentionally selects black text.
        #expect(ChatAppearance.textStyle(size: 99, style: "nope") == ChatTextStyle(size: 22, style: .system))  // clamped, defaulted
        #expect(ChatAppearance.textStyle(size: 15, style: "georgia") == ChatTextStyle(size: 15, style: .georgia))
        #expect(ChatAppearance.textPadding(99) == 32 && ChatAppearance.verticalTextPadding(18) == 21)
        #expect(ChatAppearance.avatarSize("small") == 30 && ChatAppearance.avatarSize("medium") == 38
                    && ChatAppearance.avatarSize("large") == 46)
        #expect(ChatAppearance.bubbles(user: "", aura: "").aura == ChatAppearance.color(hex: ChatAppearance.defaultAura))
    }

    // MARK: - Settings → Connections

    @Test func googleConnectionSetup() throws {
        let services = [
            ConnectionService(id: "gmail", label: "Gmail", purpose: "Read and search your mail, and write drafts. Aura never sends mail.", granted: true),
            ConnectionService(id: "calendar", label: "Calendar", purpose: "Read your calendars and events, and add or change events.", granted: true),
            ConnectionService(id: "drive", label: "Drive", purpose: "Search and read your files, and create new ones.", granted: false),
        ]
        let fresh = ConnectionInfo(id: "google", label: "Google", state: "not_set_up", services: services.map {
            ConnectionService(id: $0.id, label: $0.label, purpose: $0.purpose) })
        try renderWide("25-google-setup", width: 720, ConnectionSetupView(store: ConnectionsStore(connections: [fresh]), connection: fresh))
        let connected = ConnectionInfo(
            id: "google", label: "Google", state: "connected", configured: true, account: "me@gmail.com", services: services,
            credentials: [SecretInfo(name: "google_client_id", label: "Google OAuth client ID", set: true, hint: "123456…apps.googleusercontent.com"),
                          SecretInfo(name: "google_client_secret", label: "Google OAuth client secret", set: true, hint: "GOCSPX-…uvwx")],
            checks: [ConnectionCheck(service: "gmail", ok: true, detail: "Working."),
                     ConnectionCheck(service: "calendar", ok: false,
                                     detail: "The Google Calendar API isn't enabled in your Google Cloud project yet. Enable it, wait a minute, then check again.",
                                     fixUrl: "https://console.developers.google.com/apis/api/calendar-json.googleapis.com/overview?project=781294236515")])
        try renderWide("26-google-connected", width: 720, dark: true,
                       ConnectionSetupView(store: ConnectionsStore(connections: [connected]), connection: connected))
        let slack = ConnectionInfo(
            id: "slack", label: "Slack", state: "not_set_up",
            services: [ConnectionService(id: "read", label: "Read messages", purpose: "Read and search your channels, private channels and direct messages."),
                       ConnectionService(id: "send", label: "Send messages", purpose: "Post messages as you. Aura always shows you the exact message and asks first.")],
            setup: ConnectionSetup(redirectUrl: "http://localhost:47615/slack/callback", manifest: "{}"))
        try renderWide("27-slack-setup", width: 720, ConnectionSetupView(store: ConnectionsStore(connections: [slack]), connection: slack))
        let discord = ConnectionInfo(
            id: "discord", label: "Discord", state: "connected", configured: true, account: "Aura",
            services: [ConnectionService(id: "read", label: "Read messages", purpose: "Read the channels and messages in the servers you invite the bot to.", granted: false),
                       ConnectionService(id: "send", label: "Send messages", purpose: "Post as the bot. Aura always shows you the exact message and asks first.", granted: true)],
            credentials: [SecretInfo(name: "discord_bot_token", label: "Discord bot token", set: true, hint: "MTA1…SECR")],
            checks: [ConnectionCheck(service: "read", ok: false, detail: "Message Content intent is off, so Discord hides message text from the bot. Turn it on under Privileged Gateway Intents, then check again.",
                                     fixUrl: "https://discord.com/developers/applications/1055/bot", fixLabel: "Open the Bot page"),
                     ConnectionCheck(service: "send", ok: true, detail: "In 1 server: Aura HQ. Aura will ask before every message.")],
            setup: ConnectionSetup(inviteUrl: "https://discord.com/oauth2/authorize?client_id=1055&scope=bot&permissions=68608",
                                   portalUrl: "https://discord.com/developers/applications/1055/bot"))
        try renderWide("28-discord-connected", width: 720, ConnectionSetupView(store: ConnectionsStore(connections: [discord]), connection: discord))
    }

    @Test func liveMessagesInTheChatAndInSettings() throws {
        let store = ChatStore(items: [
            item(.user, "Did anyone message me?"),
            .message(IncomingMessage(id: 1, source: "Slack", place: "#aura-chat", author: "Jeff Quinn",
                                     text: "@Aura can you view this message?", kind: "mention")),
            .message(IncomingMessage(id: 2, source: "Discord", place: "a direct message", author: "Jeff",
                                     text: "Lunch at noon? (1 attachment)", kind: "dm")),
            .message(IncomingMessage(id: 3, source: "Slack", place: "#social", author: "Sam", text: "Pizza's here 🍕")),
            ChatStore.Item(role: .user, text: "What's on my calendar tomorrow?", origin: "slack"),  // asked from the phone
            item(.aura, "Tomorrow you have **Dentist** at 9:00 and **Team sync** at 14:00."),
            item(.aura, "Two messages are for you: Jeff asked in **#aura-chat** whether I can see it, and DM'd you about lunch at noon. Want me to reply?"),
        ])
        try render("29-live-messages", store)
        let slackServices = [ConnectionService(id: "read", label: "Read messages", purpose: "Read and search your channels.", granted: true),
                             ConnectionService(id: "send", label: "Send messages", purpose: "Post messages as you.", granted: true)]
        let setup = ConnectionSetup(redirectUrl: "http://localhost:47615/slack/callback", manifest: "{}")
        let notYet = ConnectionInfo(id: "slack", label: "Slack", state: "connected", configured: true, account: "ns8jq008 in Aura",
                                    services: slackServices, setup: setup,
                                    live: LiveInfo(credentials: [SecretInfo(name: "slack_bot_token", label: "Slack bot token", set: false),
                                                                 SecretInfo(name: "slack_app_token", label: "Slack app-level token", set: false)]))
        try renderWide("30-slack-live-setup", width: 720, ConnectionSetupView(store: ConnectionsStore(connections: [notYet]), connection: notYet))
        try renderWide("30b-slack-live-refused", width: 720, LiveMessagesCard(
            store: ConnectionsStore(connections: [notYet]), connection: notYet, live: notYet.live!, values: .constant(["xoxb-…", "xoxe.xapp-1-…", "", ""]),
            replacing: .constant(false), confirmRemove: .constant(false),
            refusal: "These tokens come from different Slack apps: the bot token from app A0CL2TP2S8H, the Access Token (App-Level Tokens) from app A0CKLRY3N0P. Copy them all from the same app (each app's App ID is on its Basic Information page), then save again."))
        let listening = ConnectionInfo(id: "slack", label: "Slack", state: "connected", configured: true, account: "ns8jq008 in Aura",
                                       services: slackServices, setup: setup,
                                       live: LiveInfo(state: "listening", detail: "Listening: DMs to Aura, @Aura mentions, and the channels you've invited it to.",
                                                      configured: true,
                                                      credentials: [SecretInfo(name: "slack_bot_token", label: "Slack bot token", set: true, hint: "xoxb-…0000"),
                                                                    SecretInfo(name: "slack_app_token", label: "Slack app-level token", set: true, hint: "xapp-…ffff")],
                                                      chat: ChatSwitch(enabled: true, channel: "aura-chat")))
        try renderWide("31-slack-live-listening", width: 720, dark: true,
                       ConnectionSetupView(store: ConnectionsStore(connections: [listening]), connection: listening))
        let discord = ConnectionInfo(id: "discord", label: "Discord", state: "connected", configured: true, account: "Aura",
                                     setup: ConnectionSetup(inviteUrl: "https://discord.com/oauth2/authorize?client_id=1055&scope=bot&permissions=68608"),
                                     live: LiveInfo(state: "problem", detail: "The Message Content intent is off, so Discord won't deliver messages. Turn it on under Bot → Privileged Gateway Intents, then press Try Again.",
                                                    configured: true))
        try renderWide("32-discord-live-problem", width: 720, ConnectionSetupView(store: ConnectionsStore(connections: [discord]), connection: discord))
    }

    @Test func toolStatusReadsLikeASentence() {
        #expect(ToolStatusView.readable("[Tool: File_System_Tool] Reading 'budget.csv'...") == "Reading 'budget.csv'…")
        #expect(ToolStatusView.readable("Plain status") == "Plain status")
    }
}
