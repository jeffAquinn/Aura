import AppKit
import Carbon.HIToolbox
import SwiftUI

/// A menu-bar-only app has no console: ~/Library/Logs/Aura/app.log records what happened.
enum AppLog {
    static let url = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Logs/Aura/app.log")

    static func write(_ message: String) {
        let line = "\(Date().formatted(.iso8601)) \(message)\n"
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            handle.write(Data(line.utf8))
            try? handle.close()
        } else {
            try? Data(line.utf8).write(to: url)
        }
    }
}

/// The menu bar icon: click (or ⌥Space) toggles the panel; right-click opens the menu.
@MainActor
final class StatusItemController: NSObject {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let menu: NSMenu
    private let onClick: () -> Void

    init(menu: NSMenu, onClick: @escaping () -> Void) {
        self.menu = menu
        self.onClick = onClick
        super.init()
        guard let button = item.button else { return }
        let glyph = NSImage(systemSymbolName: "sparkle", accessibilityDescription: "Aura")
        glyph?.isTemplate = true  // monochrome: adapts to light/dark menu bars (HIG)
        button.image = glyph
        button.toolTip = "Aura"
        button.target = self
        button.action = #selector(clicked)
        button.sendAction(on: [.leftMouseUp, .rightMouseUp])
    }

    /// The icon's frame in screen coordinates, to hang the panel beneath it.
    var buttonFrame: NSRect? { item.button?.window?.frame }

    /// The Continuous Audit's high-priority alert: a red warning sign replaces the sparkle until
    /// the user answers it in the Workshop.
    func setAlert(_ tooltip: String?) {
        guard let button = item.button else { return }
        if let tooltip {
            let config = NSImage.SymbolConfiguration(paletteColors: [.white, .systemRed])
            let glyph = NSImage(systemSymbolName: "exclamationmark.triangle.fill", accessibilityDescription: tooltip)?
                .withSymbolConfiguration(config)
            glyph?.isTemplate = false  // keep the red: this one must stand out
            button.image = glyph
            button.toolTip = tooltip
        } else {
            let glyph = NSImage(systemSymbolName: "sparkle", accessibilityDescription: "Aura")
            glyph?.isTemplate = true
            button.image = glyph
            button.toolTip = "Aura"
        }
    }

    /// Whether macOS is actually showing the icon (a crowded menu bar can hide it).
    var diagnostics: String {
        let window = item.button?.window
        let shown = window?.occlusionState.contains(.visible) ?? false
        return "isVisible=\(item.isVisible) onScreen=\(shown) frame=\(window.map { NSStringFromRect($0.frame) } ?? "none")"
    }

    @objc private func clicked() {
        let event = NSApp.currentEvent
        if event?.type == .rightMouseUp || event?.modifierFlags.contains(.control) == true {
            item.menu = menu
            item.button?.performClick(nil)  // shows the menu
            item.menu = nil
        } else {
            onClick()
        }
    }
}

/// Aura's main chat window. It behaves like a normal app window: it comes forward when Aura is
/// selected, and yields to another app when that app becomes active.
@MainActor
final class AuraPanel: NSPanel {
    var onHide: (() -> Void)?

    init(rootView: some View) {
        // Borderless + native glass: a rounded sheet of system glass, like Spotlight.
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 620),
            styleMask: [.borderless, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        isFloatingPanel = false
        level = .normal
        hidesOnDeactivate = false
        isReleasedWhenClosed = false
        // Not movable by its background: dragging over the conversation selects text. The header
        // moves the panel (WindowDragArea).
        isMovableByWindowBackground = false
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = true
        minSize = NSSize(width: 360, height: 440)
        setFrameAutosaveName("AuraPanel")  // remembers its size

        let glass = NSGlassEffectView()
        // Custom themes supply their own contrast scrim. Clear glass preserves the chosen
        // background rather than adding a second dark material layer over it.
        glass.style = .clear
        glass.cornerRadius = Self.cornerRadius
        glass.contentView = NSHostingView(rootView: rootView)
        glass.autoresizingMask = [.width, .height]
        // The window's shape is this rounded frame in every state, so macOS never outlines (or
        // shadows) the panel's square edges when it stops being the active window.
        let frame = NSView()
        frame.wantsLayer = true
        frame.layer?.cornerRadius = Self.cornerRadius
        frame.layer?.cornerCurve = .continuous
        frame.layer?.masksToBounds = true
        glass.frame = frame.bounds
        frame.addSubview(glass)
        contentView = frame
    }

    static let cornerRadius: CGFloat = 20

    override var canBecomeKey: Bool { true }

    // The shadow is redrawn when the panel becomes or stops being the key window: recompute it
    // from the rounded shape each time (otherwise a dark square edge can show).
    override func becomeKey() {
        super.becomeKey()
        DispatchQueue.main.async { [weak self] in self?.invalidateShadow() }
    }

    override func resignKey() {
        super.resignKey()
        DispatchQueue.main.async { [weak self] in self?.invalidateShadow() }
    }

    /// ⌘C, ⌘V, ⌘X, ⌘A, ⌘Z and ⇧⌘Z in the borderless chat window.
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if flags == .command || flags == [.command, .shift],
           let key = event.charactersIgnoringModifiers?.lowercased(),
           let action = Self.editActions[flags.contains(.shift) ? "⇧" + key : key],
           NSApp.sendAction(action, to: nil, from: self) {
            return true
        }
        return super.performKeyEquivalent(with: event)
    }

    private static let editActions: [String: Selector] = [
        "c": #selector(NSText.copy(_:)), "v": #selector(NSText.paste(_:)), "x": #selector(NSText.cut(_:)),
        "a": #selector(NSResponder.selectAll(_:)), "z": Selector(("undo:")), "⇧z": Selector(("redo:")),
    ]

    override func cancelOperation(_ sender: Any?) { hide() }  // Esc

    /// Hang the panel just below the menu bar, under the icon when its position is known.
    /// (At launch the icon may not be laid out yet; then use the top-right of the screen.)
    func show(below anchor: NSRect?) {
        let usable = anchor.flatMap { $0.width > 0 && $0.minX > 0 ? $0 : nil }  // not yet placed: unknown
        let screen = usable.flatMap { a in NSScreen.screens.first { $0.frame.intersects(a) } } ?? NSScreen.main
        if let visible = screen?.visibleFrame {
            let size = frame.size
            let midX = usable?.midX ?? (visible.maxX - size.width / 2 - 8)
            let x = min(max(midX - size.width / 2, visible.minX + 8), visible.maxX - size.width - 8)
            setFrameOrigin(NSPoint(x: x, y: visible.maxY - size.height - 6))
        }
        NSApp.activate()
        makeKeyAndOrderFront(nil)
        invalidateShadow()  // the shadow follows the glass's rounded shape
    }

    func hide() {
        guard isVisible else { return }
        orderOut(nil)
        onHide?()
    }
}

/// A system-wide hotkey via Carbon (no Accessibility permission needed).
@MainActor
final class HotKey {
    private static var actions: [UInt32: () -> Void] = [:]
    private static var handlerInstalled = false
    private static var nextID: UInt32 = 1
    private var ref: EventHotKeyRef?

    /// Returns nil if another app already owns the shortcut.
    init?(keyCode: UInt32, modifiers: UInt32, action: @escaping () -> Void) {
        Self.installHandler()
        let id = Self.nextID
        Self.nextID += 1
        let hotKeyID = EventHotKeyID(signature: OSType(0x4155_5241), id: id)  // 'AURA'
        guard RegisterEventHotKey(keyCode, modifiers, hotKeyID, GetApplicationEventTarget(), 0, &ref) == noErr else {
            return nil
        }
        Self.actions[id] = action
    }

    private static func installHandler() {
        guard !handlerInstalled else { return }
        handlerInstalled = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var hotKeyID = EventHotKeyID()
            GetEventParameter(
                event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            MainActor.assumeIsolated { HotKey.actions[hotKeyID.id]?() }  // delivered on the main thread
            return noErr
        }, 1, &spec, nil, nil)
    }
}
