import AppKit
import AuraKit
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
    private func render(_ name: String, _ store: ChatStore, dark: Bool = false) throws -> CGSize {
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

    @Test func toolStatusReadsLikeASentence() {
        #expect(ToolStatusView.readable("[Tool: File_System_Tool] Reading 'budget.csv'...") == "Reading 'budget.csv'…")
        #expect(ToolStatusView.readable("Plain status") == "Plain status")
    }
}
