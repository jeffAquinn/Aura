import AppKit
@testable import AuraKit
import SwiftUI
import Testing
@testable import AuraUI

/// The chat box at the bottom of the panel, drawn the way the app draws it: a real AppKit text
/// field in a window (ImageRenderer can't draw typed text). Only this view is drawn: nothing on
/// screen is captured.
@MainActor
@Suite(.serialized)
struct ComposerLayoutTests {
    static let long = "This is a long message typed into the chat box so that it has to wrap onto several lines, "
        + "and it keeps going so the field must grow taller while every word stays inside the rounded border."

    private func save(_ view: NSView, _ name: String) {
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: SnapshotTests.outDir.appending(path: "\(name).png"))
    }

    private static func fields(in view: NSView) -> [NSTextField] {
        (view as? NSTextField).map { [$0] } ?? view.subviews.flatMap(fields)
    }

    /// Type `text` into the empty composer the way keystrokes arrive (the field editor), letting
    /// SwiftUI lay out between chunks, then draw it at the size the window gives it.
    @Test(arguments: [(ChatAppearance.defaultSize, 0, "system"), (ChatAppearance.defaultSize, 2, "system"),
                      (18, 2, "system"), (ChatAppearance.defaultSize, 3, "system"), (18, 2, "georgia")])
    func typedTextStaysInsideTheBorder(size: Double, copies: Int, font: String) async {
        let store = ChatStore(items: [])
        store.transport = FakeComposerTransport()
        let style = ChatAppearance.textStyle(size: size, style: font)
        let host = NSHostingView(rootView: VStack(spacing: 0) { Spacer(); InputBar(store: store) }
            .frame(width: 420, height: 420)
            .environment(\.chatText, style)
            .environment(\.bubbles, .standard))
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 420, height: 420), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        guard let field = Self.fields(in: host).last else { Issue.record("no text field"); return }
        window.makeFirstResponder(field)
        let editor = window.fieldEditor(true, for: field) as? NSTextView
        for word in Array(repeating: Self.long, count: copies).joined(separator: " ").split(separator: " ") {
            editor?.insertText(String(word) + " ", replacementRange: editor?.selectedRange() ?? NSRange())
            await Task.yield()
            host.layoutSubtreeIfNeeded()
        }
        try? await Task.sleep(for: .milliseconds(100))
        host.layoutSubtreeIfNeeded()
        save(host, "composer-typed-\(font)-\(Int(size))-x\(copies)")
        let fieldFrame = field.convert(field.bounds, to: host)
        let used: CGRect = {
            guard let editor, let lm = editor.layoutManager, let tc = editor.textContainer else { return .zero }
            lm.ensureLayout(for: tc)
            return editor.convert(lm.usedRect(for: tc), to: host)
        }()
        let scroll = field.enclosingScrollView
        let box = scroll.map { $0.convert($0.bounds, to: host) } ?? .zero
        if copies == 0 { return }  // the empty box: drawn for comparison only
        #expect(used.height > 0 && fieldFrame.insetBy(dx: -2, dy: -2).contains(used))  // the field holds its text
        #expect(scroll != nil && box.height <= InputBar.maxHeight(style) + 1)  // the box stops at 15 lines
        #expect(host.bounds.contains(box))
        if let scroll, let editor, used.height > box.height {  // the rest scrolls, showing the end (where the typing is)
            let doc = scroll.documentView?.bounds.height ?? 0
            #expect(abs(scroll.contentView.bounds.maxY - doc) < 2 || scroll.contentView.bounds.minY > 0)
            // Editing above what's shown: the box follows the caret to the top.
            editor.setSelectedRange(NSRange(location: 0, length: 0))
            editor.scrollRangeToVisible(NSRange(location: 0, length: 0))
            try? await Task.sleep(for: .milliseconds(100))
            host.layoutSubtreeIfNeeded()
            let caret = editor.convert(editor.layoutManager?.boundingRect(forGlyphRange: NSRange(location: 0, length: 1),
                                                                          in: editor.textContainer!) ?? .zero, to: host)
            #expect(box.insetBy(dx: -2, dy: -2).contains(CGPoint(x: caret.midX, y: caret.midY)),
                    "the caret at the start is out of view: caret \(caret), box \(box)")
        }
    }
}

final class FakeComposerTransport: AuraTransport, @unchecked Sendable {
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
}
