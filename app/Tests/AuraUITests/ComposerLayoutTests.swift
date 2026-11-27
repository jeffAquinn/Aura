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

    static func fieldsIn(_ view: NSView) -> [NSTextField] { fields(in: view) }

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

/// In the same serialized suite: chat-box windows drawn in parallel steal each other's focus.
extension ComposerLayoutTests {
    /// A long model name in the chat box's model menu stays in its own lane: it never runs over
    /// the divider into the text field or past the rounded border.
    @Test func aLongModelNameStaysInItsLane() async throws {
        let store = ChatStore(items: [])
        store.transport = FakeComposerTransport()
        store.chatModel = ModelChoice(provider: "llamacpp", model: "Ternary-Bonsai-2.5-27B-Q2_0-extra-long-name")
        let host = NSHostingView(rootView: VStack(spacing: 0) { Spacer(); InputBar(store: store) }
            .frame(width: 360, height: 120)
            .environment(\.chatText, .standard)
            .environment(\.bubbles, .standard))
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 360, height: 120), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.contentView = host
        for _ in 0..<5 { host.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        if let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
            host.cacheDisplay(in: host.bounds, to: rep)
            try? rep.representation(using: .png, properties: [:])?.write(to: SnapshotTests.outDir.appending(path: "composer-long-model.png"))
        }
        let field = try #require(ComposerLayoutTests.fieldsIn(host).last)
        let fieldFrame = field.convert(field.bounds, to: host)
        #expect(fieldFrame.width > 120, "the text field was squeezed by the model name: \(fieldFrame)")
        // Nothing is drawn past the panel's edges (the long name used to run off the left).
        func all(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(all) }
        for view in all(host) where view !== host && !view.isHidden && view.bounds.width > 0 {
            let frame = view.convert(view.bounds, to: host)
            #expect(frame.minX >= -1 && frame.maxX <= host.bounds.maxX + 1, "\(type(of: view)) runs outside: \(frame)")
        }
    }
}

/// In the same serialized suite: chat-box windows drawn in parallel steal each other's focus.
extension ComposerLayoutTests {
    /// A pasted URL or path with no spaces wraps inside the box instead of running past its edge.
    @Test func aLongUnbrokenStringWrapsInsideTheBorder() async throws {
        let store = ChatStore(items: [])
        store.transport = FakeComposerTransport()
        let host = NSHostingView(rootView: VStack(spacing: 0) { Spacer(); InputBar(store: store) }
            .frame(width: 360, height: 300)
            .environment(\.chatText, .standard)
            .environment(\.bubbles, .standard))
        let window = NSWindow(contentRect: CGRect(x: 0, y: 0, width: 360, height: 300), styleMask: [.titled],
                              backing: .buffered, defer: false)
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        let field = try #require(ComposerLayoutTests.fieldsIn(host).last)
        window.makeFirstResponder(field)
        let editor = try #require(window.fieldEditor(true, for: field) as? NSTextView)
        let url = "https://example.com/" + String(repeating: "averyveryverylongpathsegment/", count: 6) + "end"
        editor.insertText(url, replacementRange: editor.selectedRange())
        for _ in 0..<5 { await Task.yield(); host.layoutSubtreeIfNeeded(); try await Task.sleep(for: .milliseconds(30)) }
        if let rep = host.bitmapImageRepForCachingDisplay(in: host.bounds) {
            host.cacheDisplay(in: host.bounds, to: rep)
            try? rep.representation(using: .png, properties: [:])?.write(to: SnapshotTests.outDir.appending(path: "composer-unbroken.png"))
        }
        let lm = try #require(editor.layoutManager), tc = try #require(editor.textContainer)
        lm.ensureLayout(for: tc)
        let used = editor.convert(lm.usedRect(for: tc), to: host)
        let box = try #require(field.enclosingScrollView).convert(field.enclosingScrollView!.bounds, to: host)
        #expect(used.maxX <= box.maxX + 2 && used.minX >= box.minX - 2, "the text runs outside the box: \(used) vs \(box)")
    }
}

final class FakeComposerTransport: AuraTransport, @unchecked Sendable {
    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.notConnectedToInternet) }
}
