import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Shared window appearance.  The value is stored locally in UserDefaults and is read by every
/// native Aura surface; it is never part of the Vault or an agent prompt.
public enum Theme: String, CaseIterable, Sendable {
    case glass, starfield, custom
    public static let key = "auraTheme"

    var label: String {
        switch self {
        case .glass: "Clean Glass"
        case .starfield: "Aura Starfield"
        case .custom: "My Background"
        }
    }
}

public enum UserProfile {
    public static let nameKey = "auraUserDisplayName"
    public static let avatarPathKey = "auraUserAvatarPath"
    public static let backgroundPathKey = "auraUserBackgroundPath"
    private static let limit = 20 * 1_024 * 1_024

    static func image(_ path: String) -> Image? {
        guard !path.isEmpty, let image = NSImage(contentsOfFile: path), image.size.width > 0, image.size.height > 0 else { return nil }
        return Image(nsImage: image)
    }

    static func importImage(_ url: URL, named name: String) throws -> String {
        let bytes = try Data(contentsOf: url, options: [.mappedIfSafe])
        guard bytes.count <= limit else { throw ProfileImageError.tooLarge }
        guard let image = NSImage(data: bytes), image.size.width > 0, image.size.height > 0 else { throw ProfileImageError.invalid }
        let root = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                               appropriateFor: nil, create: true)
            .appending(path: "Aura/Profile", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let ext = ["png", "jpg", "jpeg", "heic", "tiff", "gif"].contains(url.pathExtension.lowercased())
            ? url.pathExtension.lowercased() : "png"
        let destination = root.appending(path: "\(name).\(ext)")
        try bytes.write(to: destination, options: .atomic)
        return destination.path
    }

    enum ProfileImageError: LocalizedError { case tooLarge, invalid
        var errorDescription: String? { self == .tooLarge ? "Choose an image smaller than 20 MB." : "That file is not a supported image." }
    }
}

/// Shared backdrop for the chat panel and all auxiliary Aura windows. A light scrim keeps text
/// readable without hiding the user's chosen image beneath transparent chat bubbles.
public struct AuraThemeBackdrop: View {
    @AppStorage(Theme.key) private var selected = Theme.glass.rawValue
    @AppStorage(UserProfile.backgroundPathKey) private var backgroundPath = ""
    let starfield: Image?

    public init(starfield: Image? = nil) { self.starfield = starfield }

    public var body: some View {
        switch Theme(rawValue: selected) ?? .glass {
        case .glass: Color.clear
        case .starfield:
            if let image = starfield ?? UserProfile.image(Bundle.main.path(forResource: "Starfield", ofType: "png") ?? "") {
                image.resizable().scaledToFill().blur(radius: 18).opacity(0.38)
            } else { Color.clear }
        case .custom:
            if let image = UserProfile.image(backgroundPath) {
                image.resizable().scaledToFill().overlay(Color.black.opacity(0.16))
            } else { Color.clear }
        }
    }
}

/// Explicit variant used by visual regression tests, where the chosen theme is injected rather
/// than read from UserDefaults.
struct ThemeBackdrop: View {
    let theme: Theme
    let image: Image?
    var body: some View {
        switch theme {
        case .glass: Color.clear
        case .starfield:
            if let image { image.resizable().scaledToFill().blur(radius: 18).opacity(0.38) } else { Color.clear }
        case .custom: Color.clear
        }
    }
}

/// Applies the selected visual language to any full-window Aura feature.
public struct AuraThemedPage<Content: View>: View {
    let content: Content
    public init(@ViewBuilder content: () -> Content) { self.content = content() }
    public var body: some View {
        // The panel provides the glass boundary; adding a second material here would mute a
        // custom image again. Individual controls keep their own subtle surfaces for contrast.
        content.background { AuraThemeBackdrop() }
    }
}

public struct UserAvatarView: View {
    @AppStorage(UserProfile.avatarPathKey) private var avatarPath = ""
    let size: CGFloat
    public init(size: CGFloat = 22) { self.size = size }
    public var body: some View {
        Group {
            if let image = UserProfile.image(avatarPath) { image.resizable().scaledToFill() }
            else { Image(systemName: "person.crop.circle.fill").resizable().scaledToFit().padding(size * 0.12).foregroundStyle(.secondary) }
        }
        .frame(width: size, height: size).clipShape(Circle())
        .overlay(Circle().strokeBorder(.white.opacity(0.45), lineWidth: 0.75))
        .accessibilityLabel("Your avatar")
    }
}

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
    public var userTextOverride: Color?
    public var auraTextOverride: Color?
    public var bubbleTransparency: Double
    public var textTransparency: Double

    public static let standard = BubbleColors(user: .accentColor, aura: ChatAppearance.color(hex: ChatAppearance.defaultAura)!)

    public init(user: Color, aura: Color, userTextOverride: Color? = nil, auraTextOverride: Color? = nil,
                bubbleTransparency: Double = 0, textTransparency: Double = 0) {
        self.user = user
        self.aura = aura
        self.userTextOverride = userTextOverride
        self.auraTextOverride = auraTextOverride
        self.bubbleTransparency = bubbleTransparency
        self.textTransparency = textTransparency
    }

    public var userText: Color { (userTextOverride ?? ChatAppearance.textColor(on: user)).opacity(1 - textTransparency) }
    public var auraText: Color { (auraTextOverride ?? ChatAppearance.textColor(on: aura)).opacity(1 - textTransparency) }
    public var userBackground: Color { user.opacity(1 - bubbleTransparency) }
    public var auraBackground: Color { aura.opacity(1 - bubbleTransparency) }
}

/// Stored preferences (UserDefaults via @AppStorage) and the color arithmetic behind them.
public enum ChatAppearance {
    public static let sizeKey = "chatFontSize"
    public static let styleKey = "chatFontStyle"
    public static let userBubbleKey = "chatUserBubble"  // "" = the system accent color (blue)
    public static let auraBubbleKey = "chatAuraBubble"
    public static let userTextKey = "chatUserText"
    public static let auraTextKey = "chatAuraText"
    public static let bubbleTransparencyKey = "chatBubbleTransparency"
    public static let textTransparencyKey = "chatTextTransparency"
    public static let avatarSizeKey = "chatAvatarSize"

    public static let defaultSize = 13.0  // macOS body text
    public static let sizes = 11.0...22.0
    public static let defaultAura = "#7C4DFF"  // violet; white text on it passes WCAG AA

    /// Quick choices, so a color can be picked without leaving the settings popover.
    public static let swatches: [(name: String, hex: String)] = [
        ("Blue", ""), ("White", "#FFFFFF"), ("Black", "#000000"), ("Violet", defaultAura), ("Indigo", "#4F46E5"), ("Teal", "#0E7C7E"),
        ("Green", "#1F7A3D"), ("Orange", "#B35400"), ("Pink", "#D6336C"), ("Graphite", "#5B6270"),
    ]

    public static func textStyle(size: Double, style: String) -> ChatTextStyle {
        ChatTextStyle(size: CGFloat(min(max(size, sizes.lowerBound), sizes.upperBound)),
                      style: ChatFontStyle(rawValue: style) ?? .system)
    }

    public static func bubbles(user: String, aura: String, userText: String = "", auraText: String = "",
                               bubbleTransparency: Double = 0, textTransparency: Double = 0) -> BubbleColors {
        BubbleColors(user: color(hex: user) ?? .accentColor,
                     aura: color(hex: aura) ?? color(hex: defaultAura)!,
                     userTextOverride: color(hex: userText), auraTextOverride: color(hex: auraText),
                     bubbleTransparency: min(max(bubbleTransparency, 0), 1), textTransparency: min(max(textTransparency, 0), 1))
    }

    public static func avatarSize(_ value: String) -> CGFloat {
        switch value { case "large": 38; case "medium": 30; default: 22 }
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
