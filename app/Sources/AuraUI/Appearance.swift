import AppKit
import SwiftUI

// MARK: - chat appearance (Settings → Appearance): font, size and bubble colors

/// The chat's typeface. The system designs follow macOS; the named families ship with every Mac.
public enum ChatFontStyle: String, CaseIterable, Sendable {
    case system, rounded, serif, monospaced, avenir, georgia, helvetica

    public var label: String {
        switch self {
        case .system: "System"
        case .rounded: "Rounded"
        case .serif: "Serif (New York)"
        case .monospaced: "Monospaced"
        case .avenir: "Avenir Next"
        case .georgia: "Georgia"
        case .helvetica: "Helvetica Neue"
        }
    }

    func font(size: CGFloat, weight: Font.Weight) -> Font {
        switch self {
        case .system: .system(size: size, weight: weight)
        case .rounded: .system(size: size, weight: weight, design: .rounded)
        case .serif: .system(size: size, weight: weight, design: .serif)
        case .monospaced: .system(size: size, weight: weight, design: .monospaced)
        case .avenir: .custom("Avenir Next", size: size).weight(weight)
        case .georgia: .custom("Georgia", size: size).weight(weight)
        case .helvetica: .custom("Helvetica Neue", size: size).weight(weight)
        }
    }
}

/// The chat's text: a base size and a typeface; headings, code and status lines scale from it.
public struct ChatTextStyle: Equatable, Sendable {
    public var size: CGFloat
    public var style: ChatFontStyle

    public static let standard = ChatTextStyle(size: ChatAppearance.defaultSize, style: .system)

    public init(size: CGFloat, style: ChatFontStyle) {
        self.size = size
        self.style = style
    }

    public func font(_ scale: CGFloat = 1, weight: Font.Weight = .regular) -> Font {
        style.font(size: (size * scale).rounded(), weight: weight)
    }

    /// Code stays monospaced whatever the chat's typeface.
    public func code(_ scale: CGFloat = 0.92) -> Font {
        .system(size: (size * scale).rounded(), design: .monospaced)
    }
}

public extension EnvironmentValues {
    @Entry var chatText = ChatTextStyle.standard
    @Entry var bubbles = BubbleColors.standard
}

/// The two bubble colors, and a readable text color on each.
public struct BubbleColors: Equatable, Sendable {
    public var user: Color
    public var aura: Color

    public static let standard = BubbleColors(user: .accentColor, aura: ChatAppearance.color(hex: ChatAppearance.defaultAura)!)

    public init(user: Color, aura: Color) {
        self.user = user
        self.aura = aura
    }

    public var userText: Color { ChatAppearance.textColor(on: user) }
    public var auraText: Color { ChatAppearance.textColor(on: aura) }
}

/// Stored preferences (UserDefaults via @AppStorage) and the color arithmetic behind them.
public enum ChatAppearance {
    public static let sizeKey = "chatFontSize"
    public static let styleKey = "chatFontStyle"
    public static let userBubbleKey = "chatUserBubble"  // "" = the system accent color (blue)
    public static let auraBubbleKey = "chatAuraBubble"

    public static let defaultSize = 13.0  // macOS body text
    public static let sizes = 11.0...22.0
    public static let defaultAura = "#7C4DFF"  // violet; white text on it passes WCAG AA

    /// Quick choices, so a color can be picked without leaving the settings popover.
    public static let swatches: [(name: String, hex: String)] = [
        ("Blue", ""), ("Violet", defaultAura), ("Indigo", "#4F46E5"), ("Teal", "#0E7C7E"),
        ("Green", "#1F7A3D"), ("Orange", "#B35400"), ("Pink", "#D6336C"), ("Graphite", "#5B6270"),
    ]

    public static func textStyle(size: Double, style: String) -> ChatTextStyle {
        ChatTextStyle(size: CGFloat(min(max(size, sizes.lowerBound), sizes.upperBound)),
                      style: ChatFontStyle(rawValue: style) ?? .system)
    }

    public static func bubbles(user: String, aura: String) -> BubbleColors {
        BubbleColors(user: color(hex: user) ?? .accentColor,
                     aura: color(hex: aura) ?? color(hex: defaultAura)!)
    }

    /// "#RRGGBB" → Color (sRGB); nil for "" or anything malformed.
    public static func color(hex: String) -> Color? {
        rgb(hex).map { Color(.sRGB, red: $0.r, green: $0.g, blue: $0.b) }
    }

    /// Color → "#RRGGBB" (sRGB), for storing what the color picker chose.
    public static func hex(_ color: Color) -> String {
        let c = NSColor(color).usingColorSpace(.sRGB) ?? .systemBlue
        let v = [c.redComponent, c.greenComponent, c.blueComponent].map { Int((min(max($0, 0), 1) * 255).rounded()) }
        return String(format: "#%02X%02X%02X", v[0], v[1], v[2])
    }

    /// White text unless white would be hard to read on the bubble (below WCAG's 3:1), then black.
    /// (Pure "whichever contrasts more" picks black on the system blue, unlike every Mac chat app.)
    public static func textColor(on bubble: Color) -> Color {
        let c = NSColor(bubble).usingColorSpace(.sRGB) ?? .systemBlue
        return prefersWhiteText(r: c.redComponent, g: c.greenComponent, b: c.blueComponent) ? .white : .black
    }

    static func prefersWhiteText(r: CGFloat, g: CGFloat, b: CGFloat) -> Bool {
        contrastWithWhite(r: r, g: g, b: b) >= 3
    }

    /// WCAG contrast ratio of white text on this color (1...21).
    static func contrastWithWhite(r: CGFloat, g: CGFloat, b: CGFloat) -> CGFloat {
        func linear(_ v: CGFloat) -> CGFloat { v <= 0.04045 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4) }
        let luminance = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
        return 1.05 / (luminance + 0.05)
    }

    static func rgb(_ hex: String) -> (r: CGFloat, g: CGFloat, b: CGFloat)? {
        let s = hex.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "#", with: "")
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        return (CGFloat((v >> 16) & 0xFF) / 255, CGFloat((v >> 8) & 0xFF) / 255, CGFloat(v & 0xFF) / 255)
    }
}

// MARK: - copying

enum Clipboard {
    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
}

// MARK: - moving the panel

/// A transparent strip that drags the window. The panel moves by its header only, so dragging
/// anywhere over the conversation selects text instead of moving the panel. (SwiftUI's
/// selectable text tells AppKit a drag on it "can move the window".)
struct WindowDragArea: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView { DragView() }
    func updateNSView(_ nsView: NSView, context: Context) {}

    private final class DragView: NSView {
        override var mouseDownCanMoveWindow: Bool { true }
        override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
    }
}
