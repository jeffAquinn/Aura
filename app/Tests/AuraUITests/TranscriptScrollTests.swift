import AppKit
@testable import AuraKit
import SwiftUI
import Testing
@testable import AuraUI

/// Sending a message in a long conversation must never leave the transcript blank: the bubbles
/// stay on screen while the new one arrives. Drawn in a window (offscreen), frame by frame.
@MainActor
@Suite(.serialized)
struct TranscriptScrollTests {
    private static func conversation(_ count: Int) -> [ChatStore.Item] {
        (0..<count).map { i in
            i.isMultiple(of: 2)
                ? ChatStore.Item(role: .user, text: "Question \(i): what happened with the build this morning?")
                : ChatStore.Item(role: .aura, text: i % 3 == 1 ? Self.rich : String(repeating: "Here is a fairly long answer that wraps over several lines in the panel. ", count: 1 + i % 7))
        }
    }

    static let rich = """
    ## The Resolution
    The goal is to move from **Search** to **Inquiry**.

    ### I. Semantic Density
    - Atomic notes: smaller, focused notes allow precise chunking.
    - Strict taxonomy: every file placed by the Codex.
    - Metadata enrichment: purpose, status and links.

    | Pillar | Focus |
    |--------|-------|
    | Content | Density |
    | Links | Mapping |

    ```python
    for note in vault.notes():
        index(note)
    ```
    How would you like to proceed?
    """

    /// How much of the transcript's area shows something other than the plain background (0 to 1).
    private static func inked(_ view: NSView) -> Double {
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return 0 }
        view.cacheDisplay(in: view.bounds, to: rep)
        let background = rep.colorAt(x: 2, y: 2) ?? .white
        var inked = 0, total = 0
        for y in stride(from: 0, to: rep.pixelsHigh, by: 6) {
            for x in stride(from: 0, to: rep.pixelsWide, by: 6) {
                guard let c = rep.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB),
                      let b = background.usingColorSpace(.deviceRGB) else { continue }
                total += 1
                if abs(c.redComponent - b.redComponent) + abs(c.greenComponent - b.greenComponent)
                    + abs(c.blueComponent - b.blueComponent) > 0.08 { inked += 1 }
            }
        }
        return total == 0 ? 0 : Double(inked) / Double(total)
    }

    @Test func sendingInALongConversationNeverBlanksTheTranscript() async throws {
        let store = ChatStore(items: Self.conversation(60))
        store.transport = HangingTransport()
        let host = NSHostingView(rootView: TranscriptView(store: store, avatar: nil)
            .frame(width: 420, height: 520)
            .environment(\.chatText, .standard)
            .environment(\.bubbles, .standard))
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 420, height: 520), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.contentView = host
        for _ in 0..<10 { host.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        let before = Self.inked(host)
        #expect(before > 0.05, "the conversation should be visible before sending (\(before))")

        store.send("And one more question about the release?")
        var lowest = 1.0
        for _ in 0..<25 {  // about half a second, frame by frame
            host.layoutSubtreeIfNeeded()
            lowest = min(lowest, Self.inked(host))
            try await Task.sleep(for: .milliseconds(20))
        }
        print("transcript ink before \(before), lowest after sending \(lowest)")
        #expect(lowest > before * 0.5, "the transcript went (nearly) blank after sending: \(lowest) vs \(before) before")
    }

    @Test func theWholePanelKeepsItsBubblesThroughATurn() async throws {
        let store = ChatStore(items: Self.conversation(60))
        let transport = ScriptedTurnTransport()
        store.transport = transport
        let host = NSHostingView(rootView: ChatView(store: store, avatar: nil, onRestart: {})
            .frame(width: 420, height: 620)
            .environment(\.rendersOffscreen, true))
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 420, height: 620), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.contentView = host
        for _ in 0..<10 { host.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        let before = Self.inked(host)
        store.send("And one more question about the release?")
        var samples: [Double] = []
        for step in 0..<40 {
            if step == 3 { transport.continuation?.yield(.waking) }
            if step == 10 { transport.continuation?.yield(.thinking) }
            if step == 15 { transport.continuation?.yield(.text("Here is the answer, ")) }
            if step == 20 { transport.continuation?.yield(.text("arriving word by word.")) }
            if step == 30 { transport.continuation?.finish() }
            host.layoutSubtreeIfNeeded()
            samples.append(Self.inked(host))
            try await Task.sleep(for: .milliseconds(20))
        }
        print("panel ink before \(before); during: \(samples.map { ($0 * 100).rounded() / 100 })")
        #expect(samples.min()! > before * 0.6, "the panel lost its bubbles during the turn")
    }
}

/// Plays a turn's events when the test says so.
final class ScriptedTurnTransport: AuraTransport, @unchecked Sendable {
    var continuation: AsyncThrowingStream<AuraEvent, Error>.Continuation?
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> {
        let (stream, continuation) = AsyncThrowingStream<AuraEvent, Error>.makeStream()
        self.continuation = continuation
        return stream
    }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { _ in } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
}

/// A turn that keeps thinking (the server hasn't answered yet).
final class HangingTransport: AuraTransport, @unchecked Sendable {
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { _ in } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { _ in } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
}
