import AuraKit
import Foundation
import SwiftUI

/// The panel's content. It draws no background of its own: the panel provides native glass
/// (NSGlassEffectView), so the system material shows through.
public struct ChatView: View {
    let store: ChatStore
    let avatar: Image?
    let backdrop: Image?
    let welcome: Image?  // shown while Aura starts and while the conversation is empty
    let wordmark: Image?  // Aura's name written in stars, at the top of the header
    let onRestart: () -> Void
    let onOpenHOLTerminal: () -> Void
    let onOpenWorkstation: () -> Void
    let onOpenVault: () -> Void
    let onOpenAuditLog: () -> Void
    let onOpenObserver: () -> Void
    @AppStorage(ChatAppearance.sizeKey) private var fontSize = ChatAppearance.defaultSize
    @AppStorage(ChatAppearance.styleKey) private var fontStyle = ChatFontStyle.system.rawValue
    @AppStorage(ChatAppearance.userBubbleKey) private var userBubble = ""
    @AppStorage(ChatAppearance.auraBubbleKey) private var auraBubble = ChatAppearance.defaultAura
    @AppStorage(ChatAppearance.userTextKey) private var userText = ""
    @AppStorage(ChatAppearance.auraTextKey) private var auraText = ""
    @AppStorage(ChatAppearance.bubbleTransparencyKey) private var bubbleTransparency = 0.0
    @AppStorage(ChatAppearance.textTransparencyKey) private var textTransparency = 0.0
    @AppStorage(ChatAppearance.userBubbleBorderKey) private var userBubbleBorder = ""
    @AppStorage(ChatAppearance.auraBubbleBorderKey) private var auraBubbleBorder = ""
    @AppStorage(ChatAppearance.userAvatarBorderKey) private var userAvatarBorder = ""
    @AppStorage(ChatAppearance.auraAvatarBorderKey) private var auraAvatarBorder = ""
    @AppStorage(ChatAppearance.userBubbleBorderTransparencyKey) private var userBubbleBorderTransparency = 1.0
    @AppStorage(ChatAppearance.auraBubbleBorderTransparencyKey) private var auraBubbleBorderTransparency = 1.0
    @AppStorage(ChatAppearance.userAvatarBorderTransparencyKey) private var userAvatarBorderTransparency = 0.55
    @AppStorage(ChatAppearance.auraAvatarBorderTransparencyKey) private var auraAvatarBorderTransparency = 0.5

    public init(store: ChatStore, avatar: Image?, backdrop: Image? = nil, welcome: Image? = nil, wordmark: Image? = nil, onRestart: @escaping () -> Void,
                onOpenHOLTerminal: @escaping () -> Void = {}, onOpenWorkstation: @escaping () -> Void = {},
                onOpenVault: @escaping () -> Void = {}, onOpenAuditLog: @escaping () -> Void = {},
                onOpenObserver: @escaping () -> Void = {}) {
        self.store = store
        self.avatar = avatar
        self.backdrop = backdrop
        self.welcome = welcome
        self.wordmark = wordmark
        self.onRestart = onRestart
        self.onOpenHOLTerminal = onOpenHOLTerminal
        self.onOpenWorkstation = onOpenWorkstation
        self.onOpenVault = onOpenVault
        self.onOpenAuditLog = onOpenAuditLog
        self.onOpenObserver = onOpenObserver
    }

    public var body: some View {
        VStack(spacing: 0) {
            HeaderView(store: store, avatar: avatar, wordmark: wordmark, onRestart: onRestart, onOpenHOLTerminal: onOpenHOLTerminal,
                       onOpenWorkstation: onOpenWorkstation, onOpenVault: onOpenVault, onOpenAuditLog: onOpenAuditLog,
                       onOpenObserver: onOpenObserver)
            if case let .offline(message) = store.connection {
                OfflineBanner(message: message, onRestart: onRestart)
            }
            Group {
                if store.items.isEmpty, store.phase == .idle {
                    WelcomeView(avatar: avatar, image: welcome, name: store.persona.name,
                                starting: store.connection == .starting).transition(.opacity)
                } else {
                    TranscriptView(store: store, avatar: avatar).transition(.opacity)
                }
            }
            .animation(.smooth(duration: 0.3), value: store.items.isEmpty)
            if let plan = store.activePlan {
                PlanProgressLine(plan: plan, store: store).transition(.opacity)
            }
            InputBar(store: store)
        }
        .frame(minWidth: 360, minHeight: 440)
        .background { AuraThemeBackdrop(starfield: backdrop) }
        .environment(\.chatText, ChatAppearance.textStyle(size: fontSize, style: fontStyle))
        .environment(\.bubbles, ChatAppearance.bubbles(user: userBubble, aura: auraBubble, userText: userText, auraText: auraText,
                                                         bubbleTransparency: bubbleTransparency, textTransparency: textTransparency,
                                                         userBorder: userBubbleBorder, auraBorder: auraBubbleBorder,
                                                         userAvatarBorder: userAvatarBorder, auraAvatarBorder: auraAvatarBorder,
                                                         userBubbleBorderTransparency: userBubbleBorderTransparency,
                                                         auraBubbleBorderTransparency: auraBubbleBorderTransparency,
                                                         userAvatarBorderTransparency: userAvatarBorderTransparency,
                                                         auraAvatarBorderTransparency: auraAvatarBorderTransparency))
    }
}

/// "Aura Starfield": a faint, blurred starfield from the portrait, under the system glass.
// MARK: - header

struct AvatarView: View {
    let image: Image?
    let size: CGFloat
    var pulsing = false
    @AppStorage(ChatAppearance.auraAvatarBorderKey) private var borderHex = ""
    @AppStorage(ChatAppearance.auraAvatarBorderTransparencyKey) private var borderTransparency = 0.5
    @State private var bright = false

    var body: some View {
        Group {
            if let image {
                image.resizable().scaledToFill()
            } else {
                Image(systemName: "sparkle").resizable().scaledToFit().padding(size * 0.22)
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(Circle().strokeBorder(ChatAppearance.borderColor(hex: borderHex, fallback: .white,
                                                                  transparency: borderTransparency), lineWidth: 0.75))
        .shadow(color: .cyan.opacity(pulsing ? (bright ? 0.9 : 0.15) : 0), radius: pulsing ? size / 3 : 0)
        .scaleEffect(pulsing && bright ? 1.06 : 1)
        .onChange(of: pulsing, initial: true) { _, isPulsing in
            if isPulsing {
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { bright = true }
            } else {
                withAnimation(.default) { bright = false }
            }
        }
        .accessibilityLabel("Aura")
    }
}

struct HeaderView: View {
    let store: ChatStore
    let avatar: Image?
    var wordmark: Image? = nil
    let onRestart: () -> Void
    let onOpenHOLTerminal: () -> Void
    let onOpenWorkstation: () -> Void
    let onOpenVault: () -> Void
    let onOpenAuditLog: () -> Void
    let onOpenObserver: () -> Void
    @State private var showSettings = false
    @State private var confirmRestart = false
    @StateObject private var usage = AuraRuntimeUsage()
    @State private var width: CGFloat = 388  // the header's inner width, measured
    @Environment(\.rendersOffscreen) private var offscreen
    @Environment(\.openWorkshop) private var openWorkshop
    @Environment(\.openConnections) private var openConnections
    @Environment(\.openModels) private var openModels

    private var speaking: Bool { store.speech?.isSpeaking == true }

    init(store: ChatStore, avatar: Image?, wordmark: Image? = nil, onRestart: @escaping () -> Void = {},
         onOpenHOLTerminal: @escaping () -> Void = {}, onOpenWorkstation: @escaping () -> Void = {},
         onOpenVault: @escaping () -> Void = {}, onOpenAuditLog: @escaping () -> Void = {},
         onOpenObserver: @escaping () -> Void = {}) {
        self.store = store
        self.avatar = avatar
        self.wordmark = wordmark
        self.onRestart = onRestart
        self.onOpenHOLTerminal = onOpenHOLTerminal
        self.onOpenWorkstation = onOpenWorkstation
        self.onOpenVault = onOpenVault
        self.onOpenAuditLog = onOpenAuditLog
        self.onOpenObserver = onOpenObserver
    }

    var body: some View {
        // Aura's name in stars sits at the exact center of the header, horizontally and
        // vertically. The status column (resource use, the status lights, and the current stage
        // with its progress) has the left half of what remains, its rows against the left edge;
        // the panel's controls have the right half.
        let side = max(110, (width - HeaderWordmark.width) / 2 - 10)
        ZStack {
            HStack(alignment: .center, spacing: 0) {
                statusColumn(side).frame(width: side, alignment: .leading)
                Spacer(minLength: 0)
                controls.frame(width: side, alignment: .trailing)
            }
            HeaderWordmark(image: wordmark, name: store.persona.name, avatar: avatar,
                           pulsing: store.phase == .waking || store.connection == .starting || speaking)
        }
        .frame(maxWidth: .infinity)
        .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background { HeaderSurface() }
        // The panel moves by its header (its empty parts; the buttons stay buttons). Elsewhere,
        // drags select text.
        .background { if !offscreen { WindowDragArea() } }
        .help("Drag to move")
    }

    static let rowGap: CGFloat = 7

    private func statusColumn(_ side: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            AuraUsageView(usage: usage, performance: store.localPerformance).task { usage.start() }
            StatusChips(store: store).padding(.top, Self.rowGap)
            TurnProgress(stage: TurnStage(store: store, speaking: speaking), width: min(TurnProgress.width, side))
        }
    }

    private var controls: some View {
        HStack(spacing: 10) {
            Button {
                if speaking { store.speech?.stop() }
                Task { await store.updateVoice(VoicePatch(speak: !store.voice.speak)) }
            } label: {
                HeaderIcon(symbol: store.voice.speak ? "speaker.wave.2.fill" : "speaker.slash")
                    .symbolEffect(.variableColor.iterative, isActive: speaking)
            }
            .buttonStyle(.plain)
            .help(store.voice.speak ? "Spoken replies are on" : "Spoken replies are off")
            .disabled(store.transport == nil)
            ChatsMenu(store: store)
            Menu {
                Button("Settings…") { showSettings = true }
                Button("Models…") { openModels(nil) }
                Divider()
                Button("Connections…") { openConnections(nil) }
                Button("Open Workshop…") { openWorkshop(nil) }
                Button("Open HOL Terminal…", action: onOpenHOLTerminal)
                Button("Open Workstation…", action: onOpenWorkstation)
                Divider()
                Button("Open Vault in Finder", action: onOpenVault)
                Button("Open Observer…", action: onOpenObserver)
                Button("Open Audit Log", action: onOpenAuditLog)
                Divider()
                Button("Restart Aura Server") { confirmRestart = true }
            } label: {
                HeaderIcon(symbol: "ellipsis.circle")
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Open settings and Aura panels")
            .popover(isPresented: $showSettings, arrowEdge: .bottom) { SettingsView(store: store) }
            .confirmationDialog("Restart Aura Server?", isPresented: $confirmRestart, titleVisibility: .visible) {
                Button("Restart Server", role: .destructive, action: onRestart)
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This temporarily disconnects Aura and interrupts active sessions.")
            }
        }
    }
}

/// One of the header's controls: every icon the same size, weight and color.
struct HeaderIcon: View {
    let symbol: String

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: 15, weight: .regular))
            .foregroundStyle(.primary.opacity(0.72))
            .frame(width: 24, height: 24)
            .contentShape(.rect)
    }
}

/// The new-chat menu, as in Claude: New Chat, then the recent chats saved in the vault's Chats
/// folder (title and when it was last used; the open one is ticked). Choosing one opens it, and
/// the next message carries on from it.
struct ChatsMenu: View {
    let store: ChatStore
    static let recents = 15

    var body: some View {
        Menu {
            Button {
                Task { await store.newConversation() }
            } label: {
                Label("New Chat", systemImage: "square.and.pencil")
            }
            .disabled(store.isBusy || store.transport == nil)
            if let saved = store.savedChats, !saved.chats.isEmpty {
                Section("Recents") {
                    ForEach(saved.chats.prefix(Self.recents)) { chat in
                        Button {
                            Task { await store.openChat(chat.id) }
                        } label: {
                            if chat.id == saved.current {
                                Label(chat.title, systemImage: "checkmark")
                            } else {
                                Text(chat.title)
                            }
                            Text(chat.when)
                        }
                        .disabled(store.isBusy)
                    }
                }
            }
            Divider()
            Button("Show All Chats in Finder") {
                guard let vault = store.health?.vault else { return }
                let folder = URL(fileURLWithPath: vault).appending(path: "Chats", directoryHint: .isDirectory)
                NSWorkspace.shared.open(FileManager.default.fileExists(atPath: folder.path) ? folder : URL(fileURLWithPath: vault))
            }
            .disabled(store.health?.vault == nil)
        } label: {
            HeaderIcon(symbol: "square.and.pencil")
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("New chat, or open a recent one")
        .task { await store.loadChats() }
    }
}

/// The header's surface: a faint layer of material over the panel's glass, closed by a hairline
/// (a light edge over a dark one, like a bevel) and a soft shadow, so the header reads as sitting
/// just above the conversation.
struct HeaderSurface: View {
    var body: some View {
        Rectangle()
            .fill(.ultraThinMaterial)
            .opacity(0.15)  // 85% transparent: the panel's background shows through
            .overlay(alignment: .bottom) {
                VStack(spacing: 0) {
                    Rectangle().fill(.white.opacity(0.16)).frame(height: 0.5)
                    Rectangle().fill(.black.opacity(0.14)).frame(height: 0.5)
                }
            }
            .shadow(color: .black.opacity(0.10), radius: 8, y: 3)
            .ignoresSafeArea()
    }
}

/// Aura's name written in stars, centered at the top of the header. Drawn in screen blend mode with
/// its edges feathered away, so only the stars' light shows on the panel's background, never the
/// dark oval behind them. It glows softly while the model wakes or Aura speaks. Without the image
/// (tests, an unbundled build), the portrait and name stand in.
struct HeaderWordmark: View {
    let image: Image?
    let name: String
    let avatar: Image?
    var pulsing = false
    @State private var glow = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var scheme

    static let height: CGFloat = 46
    static let width: CGFloat = 92  // the image is about 2:1

    var body: some View {
        if let image {
            image.resizable().scaledToFit()
                .frame(height: Self.height)
                .mask(EllipticalGradient(colors: [.black, .black.opacity(0.9), .clear], center: .center,
                                         startRadiusFraction: 0.18, endRadiusFraction: 0.5))
                // Dark: only the stars' light shows. Light: screen would wash it out, so it shows as
                // a small, feathered patch of night sky instead.
                .blendMode(scheme == .dark ? .screen : .normal)
                .brightness(glow ? 0.12 : 0)
                .accessibilityLabel(name)
                .accessibilityAddTraits(.isHeader)
                .onChange(of: pulsing, initial: true) { _, isPulsing in
                    if isPulsing && !reduceMotion {
                        withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { glow = true }
                    } else {
                        withAnimation(.smooth(duration: 0.4)) { glow = false }
                    }
                }
        } else {
            AuraTitle(name: name, avatar: avatar, pulsing: pulsing)
        }
    }
}

/// Aura's portrait and name, centered at the top of the panel: one clean line, like a Mac app's
/// title, in the system face with a little extra letter spacing.
struct AuraTitle: View {
    let name: String
    let avatar: Image?
    var pulsing = false

    var body: some View {
        HStack(spacing: 7) {
            AvatarView(image: avatar, size: 24, pulsing: pulsing)
            Text(name)
                .font(.system(size: 15, weight: .semibold))
                .tracking(0.6)
                .foregroundStyle(.primary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }
}

/// Where the current turn is, for the progress bar under the status: each stage it moves
/// through has its place on the bar. nil when nothing is happening.
struct TurnStage: Equatable {
    let label: String
    let progress: Double
    var tint: Color = .accentColor

    init(label: String, progress: Double, tint: Color = .accentColor) {
        self.label = label
        self.progress = progress
        self.tint = tint
    }

    @MainActor init?(store: ChatStore, speaking: Bool) {
        switch store.connection {
        case .starting: self.init(label: "Starting Aura", progress: 0.3); return
        case .offline: return nil
        case .connected: break
        }
        if store.isAnsweringElsewhere {
            self.init(label: "Answering from Slack", progress: 0.5); return
        }
        switch store.phase {
        case .idle:
            if speaking { self.init(label: "Speaking the reply", progress: 1) } else { return nil }
        case .waking: self.init(label: "Loading the model", progress: 0.2, tint: .orange)
        case .thinking: self.init(label: "Thinking", progress: 0.4)
        case .streaming:
            // A tool line is the newest thing: Aura is working; otherwise it is writing.
            if store.items.last?.role == .status {
                self.init(label: Self.tool(store.items.last?.text ?? ""), progress: 0.6)
            } else {
                self.init(label: "Writing the reply", progress: 0.85)
            }
        case .awaitingApproval: self.init(label: "Waiting for your approval", progress: 0.7, tint: .orange)
        case .stopping: self.init(label: "Stopping", progress: 0.95, tint: .orange)
        }
    }

    /// "[Tool: Web_Search_Tool] Searching…" reads as "Using Web Search".
    static func tool(_ status: String) -> String {
        guard let match = status.firstMatch(of: /\[Tool: ([^\]]+)\]/) else { return "Working" }
        let name = String(match.1).replacingOccurrences(of: "_Tool", with: "").replacingOccurrences(of: "_", with: " ")
        return "Using \(name)"
    }
}

/// A slim progress bar with the stage's name: it glides from stage to stage, fills when the turn
/// ends, then fades away. Nothing is shown while Aura is idle.
struct TurnProgress: View {
    let stage: TurnStage?
    var width: CGFloat = TurnProgress.width
    @State private var done: TurnStage?  // the full bar after a turn, while it fades
    @State private var fading: Task<Void, Never>?

    private var shown: TurnStage? { stage ?? done }
    static let width: CGFloat = 220

    var body: some View {
        Group {
            if let shown {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(shown.label)
                            .lineLimit(1)
                            .contentTransition(.opacity)
                        Spacer(minLength: 8)
                        Text("\(Int((shown.progress * 100).rounded()))%")
                            .monospacedDigit()
                            .contentTransition(.numericText(value: shown.progress))
                    }
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.secondary)
                    GeometryReader { geometry in
                        ZStack(alignment: .leading) {
                            Capsule().fill(.quaternary)
                            Capsule().fill(shown.tint.gradient)
                                .frame(width: max(3, geometry.size.width * shown.progress))
                        }
                    }
                    .frame(height: 3)
                }
                .frame(width: width)
                .transition(.opacity)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(shown.label), \(Int(shown.progress * 100)) percent")
            }
        }
        .padding(.top, shown == nil ? 0 : HeaderView.rowGap + 2)
        .animation(.smooth(duration: 0.35), value: shown)
        .onChange(of: stage) { previous, next in
            fading?.cancel()
            guard next == nil, let previous else { done = nil; return }
            // The turn ended: fill the bar, then let it fade.
            done = TurnStage(label: previous.label == "Loading the model" ? "Ready" : "Done", progress: 1)
            fading = Task {
                try? await Task.sleep(for: .milliseconds(700))
                guard !Task.isCancelled else { return }
                done = nil
            }
        }
    }
}

/// Three quiet status lights, each showing only the thing it is named for: the connection to
/// Aura Server, the model the next message will use, and Aura's safeguards.
struct StatusChips: View {
    let store: ChatStore

    var body: some View {
        // One line when there's room; otherwise one light per line, their dots aligned.
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) { lights }
            VStack(alignment: .leading, spacing: 4) { lights }
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private var lights: some View {
        StatusLight(label: connection.label, color: connection.color, detail: connection.detail)
        StatusLight(label: model.label, color: model.color, detail: model.detail)
        StatusLight(label: safeguards.label, color: safeguards.color, detail: safeguards.detail)
    }

    typealias Light = (label: String, color: Color, detail: String)

    var connection: Light {
        switch store.connection {
        case .starting: ("Starting", .orange, "Aura Server is starting.")
        case let .offline(message): ("Offline", .red, "Aura Server is unavailable: \(message)")
        case .connected: ("Connected", .green, "Aura Server is connected.")
        }
    }

    var model: Light {
        let runtime = store.health?.runtimeHealth
        let detail = runtime.map { "\($0.model): \($0.detail)" } ?? "The model hasn't been checked yet."
        // A turn waking the model knows before the next health check does.
        if store.phase == .waking { return ("Model loading", .orange, detail) }
        switch runtime?.state {
        case "loaded": return ("Model ready", .green, detail)
        case "loading": return ("Model loading", .orange, detail)
        case "idle": return ("Model idle", .secondary, detail)  // normal: it loads with the next message
        case "unavailable": return ("No model", .red, detail)
        case "unknown": return ("Cloud model", .accentColor, detail)
        default: return ("Checking", .secondary, detail)
        }
    }

    var safeguards: Light {
        guard let governance = store.health?.governanceHealth else {
            return ("Checking", .secondary, "Aura's safeguards haven't been checked yet.")
        }
        let connections = governance.optionalConnectionAttention
        let detail = connections.isEmpty ? governance.agent.detail
            : "Optional connection attention: \(connections.joined(separator: ", ")). \(governance.agent.detail)"
        return governance.state == "attention" ? ("Review needed", .orange, detail) : ("Safeguards on", .green, detail)
    }
}

/// One status light: a small dot and a short word, with the full detail on hover.
private struct StatusLight: View {
    let label: String
    let color: Color
    let detail: String

    var body: some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(label).lineLimit(1).fixedSize()
        }
        .font(.caption2.weight(.medium))
        .foregroundStyle(.secondary)
        .animation(.smooth(duration: 0.3), value: label)
        .help(detail)
        .accessibilityLabel("\(label): \(detail)")
    }
}

struct OfflineBanner: View {
    let message: String
    let onRestart: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "bolt.horizontal.circle").font(.title3).foregroundStyle(.red)
            Text(message).font(.callout).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            Button("Restart Aura", action: onRestart).controlSize(.small)
        }
        .padding(10)
        .background(.red.opacity(0.10), in: .rect(cornerRadius: 12))
        .padding(.horizontal, 12)
        .padding(.bottom, 6)
    }
}

// MARK: - transcript

/// While Aura starts, and whenever the conversation is empty: Aura's welcome image, feathered
/// into the panel with no hard edge, and a short greeting. While starting, the image breathes
/// slowly as the loading cue (not with Reduce Motion).
struct WelcomeView: View {
    let avatar: Image?
    var image: Image? = nil
    var name = "Aura"
    var starting = false
    @State private var breathe = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 10) {
            Spacer(minLength: 8)
            if let image {
                image.resizable().scaledToFill()
                    .frame(width: 220, height: 220)
                    .mask(RadialGradient(colors: [.black, .black.opacity(0.85), .clear], center: .center,
                                         startRadius: 30, endRadius: 112))
                    .scaleEffect(breathe ? 1.04 : 1)
                    .opacity(breathe ? 1 : 0.9)
                    .padding(.bottom, 2)
                    .accessibilityHidden(true)
            } else {
                AvatarView(image: avatar, size: 64)
            }
            Text(starting ? "Getting \(name) ready…" : "Hi, I'm \(name).")
                .font(.title3.weight(.semibold))
                .contentTransition(.opacity)
            Text(starting ? "Starting Aura Server and your model." : "Ask me anything, search the web, run some Python,\nor have me work in your vault.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .contentTransition(.opacity)
            Text("⌥Space shows or hides this panel")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .padding(.top, 4)
                .opacity(starting ? 0 : 1)
            Spacer(minLength: 8)
        }
        .frame(maxWidth: .infinity)
        .padding(20)
        .animation(.smooth(duration: 0.4), value: starting)
        .onChange(of: starting, initial: true) { _, isStarting in
            if isStarting && !reduceMotion {
                withAnimation(.easeInOut(duration: 2.4).repeatForever(autoreverses: true)) { breathe = true }
            } else {
                withAnimation(.smooth(duration: 0.6)) { breathe = false }
            }
        }
    }
}

struct TranscriptView: View {
    let store: ChatStore
    let avatar: Image?
    @State private var position = ScrollPosition(edge: .bottom)
    /// Whether the view follows the conversation's end, as a chat window does. Scrolling up yourself
    /// stops it; scrolling back to the bottom, or sending a message, starts it again.
    @State private var following = true
    @State private var phase = ScrollPhase.idle

    var body: some View {
        ScrollView {
            TranscriptContent(store: store, avatar: avatar, lazy: true)
        }
        .scrollIndicators(.automatic)
        .scrollPosition($position)
        .defaultScrollAnchor(.bottom, for: .initialOffset)
        .onScrollPhaseChange { _, new in phase = new }
        // Only your own scrolling changes `following`; content growing under a still view doesn't.
        .onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.containerSize.height >= geometry.contentSize.height - 48
        } action: { _, atBottom in
            if phase != .idle { following = atBottom }
        }
        // A reply streaming in, a new bubble, the typing dots: stay at the end while following.
        .onScrollGeometryChange(for: CGFloat.self) { $0.contentSize.height } action: { _, _ in
            if following { position.scrollTo(edge: .bottom) }
        }
        // Your own new message always brings the view to the end, once laid out (the lazy list
        // measures a new row a moment after it's added, so one scroll alone stopped short).
        .onChange(of: store.items.count) { old, new in
            guard new > old, store.items.last?.role == .user else { return }
            following = true
            position.scrollTo(edge: .bottom)
            Task { @MainActor in
                for _ in 0..<3 {
                    try? await Task.sleep(for: .milliseconds(60))
                    if following { position.scrollTo(edge: .bottom) }
                }
            }
        }
    }
}

/// A new bubble fades in as it rises a little into place. Only the newest one animates, and only
/// itself: the bubbles already shown never fade, move or redraw when one is added.
struct Arrival: ViewModifier {
    @State private var shown: Bool

    init(animated: Bool) { _shown = State(initialValue: !animated) }

    func body(content: Content) -> some View {
        content
            .opacity(shown ? 1 : 0)
            .offset(y: shown ? 0 : 8)
            .onAppear {
                guard !shown else { return }
                withAnimation(.smooth(duration: 0.25)) { shown = true }
            }
    }
}

/// The messages themselves (separate from the scroll view so snapshot tests can render them).
struct TranscriptContent: View {
    let store: ChatStore
    let avatar: Image?
    var lazy = true

    var body: some View {
        let rows = Group {
            let vaultRoot = store.health?.vault
            let newest = store.items.last?.id
            ForEach(Array(store.items.enumerated()), id: \.element.id) { index, item in
                ItemView(item: item, store: store, avatar: avatar, vaultRoot: vaultRoot)
                    .modifier(Arrival(animated: item.id == newest))
                    .padding(.top, spacing(before: index))
            }
            if store.phase == .thinking || store.phase == .waking {
                TypingIndicator(waking: store.phase == .waking, avatar: avatar).padding(.top, 10)
            } else if store.phase == .stopping {
                TypingIndicator(waking: false, stopping: true, avatar: avatar)
                    .accessibilityLabel("Stopping the current task")
                    .padding(.top, 10)
            }
        }
        Group {
            if lazy { LazyVStack(alignment: .leading, spacing: 0) { rows } } else { VStack(alignment: .leading, spacing: 0) { rows } }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    /// Generous space between turns, tight space between a tool line and what it belongs to.
    private func spacing(before index: Int) -> CGFloat {
        guard index > 0 else { return 0 }
        let (previous, current) = (store.items[index - 1].role, store.items[index].role)
        if current == .status || previous == .status { return 6 }
        if previous == .user || current == .user { return 16 }
        return 10
    }
}

struct ItemView: View {
    let item: ChatStore.Item
    let store: ChatStore
    let avatar: Image?
    var vaultRoot: String? = nil  // a value, so a status refresh doesn't redraw every bubble
    @Environment(\.chatText) private var chat
    @Environment(\.bubbles) private var bubbles
    @AppStorage(ChatAppearance.avatarSizeKey) private var avatarSize = "small"
    @AppStorage(ChatAppearance.textPaddingKey) private var textPadding = ChatAppearance.defaultTextPadding

    var body: some View {
        switch item.role {
        case .user:
            VStack(alignment: .trailing, spacing: 3) {
                HStack(alignment: .center) {
                    Spacer(minLength: 56)
                    UserAvatarView(size: ChatAppearance.avatarSize(avatarSize))
                    LongText(item.text)
                        .font(chat.font())
                        .textSelection(.enabled)
                        .padding(.horizontal, ChatAppearance.textPadding(textPadding))
                        .padding(.vertical, ChatAppearance.verticalTextPadding(textPadding))
                        .foregroundStyle(bubbles.userText)
                        .background(bubbles.userBackground, in: .rect(cornerRadius: 16))
                        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(bubbles.userBorder, lineWidth: 1))
                        .contextMenu { MessageMenu(item: item, store: store) }
                }
                if item.origin == "slack" {  // the owner wrote it in Slack (the Unified Command Stream)
                    Label("From Slack", systemImage: "iphone")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.trailing, 6)
                        .accessibilityLabel("Sent from Slack")
                }
                if let developer = item.developer {  // handed to Claude Code or Codex in the chat box
                    Label("To \(EngineInfo.named(developer)?.label ?? developer)", systemImage: DeveloperPicker.symbol)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.trailing, 6)
                }
            }
        case .aura:
            AuraMessage(item: item, store: store, avatar: avatar, vaultRoot: vaultRoot)
        case .status:
            ToolStatusView(text: item.text)
                .contextMenu { MessageMenu(item: item, store: store) }
        case .approval:
            if let ref = item.actions.first?.step {
                PlanStepBanner(ref: ref)
            }
            if item.isFileChanges {
                FileChangesCard(item: item, store: store)
            } else if item.actions.count == 1, let task = item.actions[0].developerTask {
                DeveloperTaskCard(item: item, task: task, store: store)
            } else if item.actions.count == 1, let plan = item.actions[0].plan {
                PlanCard(item: item, plan: plan, store: store)
            } else if item.actions.count == 1, item.actions[0].tool == ReviewCard.tool {
                ReviewCard(item: item, store: store)
            } else if item.actions.count == 1, let build = item.actions[0].build {
                SpecificationApprovalCard(item: item, proposal: build, store: store)
            } else {
                ApprovalCard(item: item, store: store)
            }
        case .error:
            Label(item.text, systemImage: "exclamationmark.triangle.fill")
                .font(chat.font(0.92))
                .foregroundStyle(.orange)
                .padding(.leading, 32)
                .contextMenu { MessageMenu(item: item, store: store) }
        case .task:
            TaskCard(skill: item.text, taskID: item.actions.first?.description)
        case .build:
            TaskCard(skill: item.text, taskID: item.actions.first?.description, building: true)
        case .incoming:
            if let message = item.incoming {
                IncomingMessageCard(message: message)
                    .contextMenu { MessageMenu(item: item, store: store) }
            }
        case .hol:
            HOLTranscriptCard(text: item.text)
                .contextMenu { MessageMenu(item: item, store: store) }
        }
    }
}

/// Read-only, bounded observability from a Human-Out-of-Loop session.  It intentionally has no
/// reply affordance, so the user's ordinary chat messages cannot reach the isolated container.
struct HOLTranscriptCard: View {
    let text: String
    @Environment(\.chatText) private var chat
    @AppStorage(ChatAppearance.holBubbleKey) private var holBubble = ChatAppearance.defaultAura
    @AppStorage(ChatAppearance.holTextKey) private var holText = ""
    @AppStorage(ChatAppearance.holBubbleTransparencyKey) private var holBubbleTransparency = 0.0
    @AppStorage(ChatAppearance.holTextTransparencyKey) private var holTextTransparency = 0.0
    @AppStorage(ChatAppearance.holBubbleBorderKey) private var holBubbleBorder = ""
    @AppStorage(ChatAppearance.holBubbleBorderTransparencyKey) private var holBubbleBorderTransparency = 1.0

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label("HOL session", systemImage: "terminal.fill")
                .font(chat.font(0.82, weight: .semibold))
                .foregroundStyle(.secondary)
            Text(text)
                .font(chat.code(0.84))
                .foregroundStyle(colors.auraText)
                .textSelection(.enabled)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .background(colors.auraBackground, in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(colors.auraBorder, lineWidth: 1))
        .padding(.leading, 32)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Human Out of Loop session output: \(text)")
    }

    private var colors: BubbleColors {
        ChatAppearance.bubbles(user: "", aura: holBubble, userText: "", auraText: holText,
                               bubbleTransparency: holBubbleTransparency, textTransparency: holTextTransparency,
                               userBorder: "", auraBorder: holBubbleBorder,
                               auraBubbleBorderTransparency: holBubbleBorderTransparency)
    }
}

/// A live Slack or Discord message: who wrote it, where, and what they said. Aura answers only
/// the user's own messages to it in Slack (when switched on); anything else, the user can ask it to.
struct IncomingMessageCard: View {
    let message: IncomingMessage
    @Environment(\.chatText) private var chat

    /// People's words, shown as written.
    private var messageText: AttributedString { AttributedString(message.text) }

    private var badge: String? {
        switch message.kind {
        case "mention": "mentions Aura"
        case "reply": "not posted"
        default: nil
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: message.source == "Slack" ? "number.square.fill" : "bubble.left.and.bubble.right.fill")
                .font(chat.font(1.05))
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(message.heading).font(chat.font(0.82).weight(.semibold)).foregroundStyle(.secondary)
                    if let badge {
                        Text(badge)
                            .font(chat.font(0.75).weight(.medium))
                            .padding(.horizontal, 6).padding(.vertical, 1)
                            .background(Color.accentColor.opacity(0.18), in: .capsule)
                    }
                }
                (Text(message.author).bold() + Text(": ") + Text(messageText))
                    .font(chat.font(0.95))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
        }
        .padding(10)
        .background(Color.primary.opacity(0.06), in: .rect(cornerRadius: 12))
        .overlay(alignment: .leading) {
            if message.isToAura {  // written to Aura: a stripe in Aura's color
                Capsule().fill(Color.accentColor).frame(width: 3).padding(.vertical, 8)
            }
        }
        .padding(.leading, 32)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("New \(message.source) message in \(message.place) from \(message.author): \(message.text)")
    }
}

/// Aura's reply: in its bubble (violet unless the user chose another color), with a copy button
/// on hover and Copy Message / Copy Conversation on right-click.
/// A reply's Markdown, parsed again only when its own text changes: a long reply isn't re-parsed
/// (and its text laid out again) because something else in the panel changed.
struct ParsedMarkdown: View, Equatable {
    let text: String
    let vaultRoot: String?

    var body: some View { MarkdownView(text, vaultRoot: vaultRoot) }
}

struct AuraMessage: View {
    let item: ChatStore.Item
    let store: ChatStore
    let avatar: Image?
    var vaultRoot: String? = nil
    @Environment(\.bubbles) private var bubbles
    @AppStorage(ChatAppearance.avatarSizeKey) private var avatarSize = "small"
    @AppStorage(ChatAppearance.textPaddingKey) private var textPadding = ChatAppearance.defaultTextPadding
    @State private var hovering = false
    @State private var copied = false

    var body: some View {
        HStack(alignment: .bottom, spacing: 6) {
            HStack(alignment: .center, spacing: 10) {
                AvatarView(image: avatar, size: ChatAppearance.avatarSize(avatarSize))
                ParsedMarkdown(text: item.text, vaultRoot: vaultRoot).equatable()
                    .foregroundStyle(bubbles.auraText)
                    .tint(bubbles.auraText)  // links: underlined, in the bubble's text color
                    .padding(.horizontal, ChatAppearance.textPadding(textPadding))
                    .padding(.vertical, ChatAppearance.verticalTextPadding(textPadding))
                    .background(bubbles.auraBackground, in: .rect(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(bubbles.auraBorder, lineWidth: 1))
                    .contextMenu { MessageMenu(item: item, store: store) }
            }
            Button {
                Clipboard.copy(ChatStore.copyText(item))
                copied = true
            } label: {
                Image(systemName: copied ? "checkmark" : "doc.on.doc").font(.caption)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help("Copy this reply")
            .opacity(hovering || copied ? 1 : 0)
            Spacer(minLength: 8)
        }
        .onHover { hovering = $0; if !$0 { copied = false } }
    }
}

/// Right-click on any message.
struct MessageMenu: View {
    let item: ChatStore.Item
    let store: ChatStore

    var body: some View {
        Button("Copy Message") { Clipboard.copy(ChatStore.copyText(item)) }
        Button("Copy Conversation") { Clipboard.copy(store.conversationText()) }
    }
}

/// A skill audit (or a Developer build) started from the chat: it continues in the Workshop.
struct TaskCard: View {
    let skill: String
    let taskID: String?
    var building = false
    @Environment(\.openWorkshop) private var openWorkshop

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: building ? "wrench.and.screwdriver.fill" : "hammer.fill").foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(building ? "Building the **\(skill)** skill" : "Auditing the **\(skill)** skill").font(.callout)
                Text(building ? "In an isolated container, then the Security Governor. Follow it live in the Workshop."
                    : "Checks run in a shadow project; you decide in the Workshop.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Button("Open Workshop") { openWorkshop(taskID) }.controlSize(.small)
        }
        .padding(10)
        .background(Color.primary.opacity(0.06), in: .rect(cornerRadius: 12))
        .padding(.leading, 32)
    }
}

/// Opens the Workshop window, optionally on a task (like SwiftUI's OpenURLAction).
public struct OpenWorkshopAction: Sendable {
    private let handler: @MainActor @Sendable (String?) -> Void

    public init(_ handler: @escaping @MainActor @Sendable (String?) -> Void) {
        self.handler = handler
    }

    @MainActor public func callAsFunction(_ taskID: String?) { handler(taskID) }
}

public extension EnvironmentValues {
    /// Provided by the app.
    @Entry var openWorkshop = OpenWorkshopAction { _ in }
}

/// Tool activity: smaller and quieter than Aura's speech.
struct ToolStatusView: View {
    let text: String
    @Environment(\.chatText) private var chat

    var body: some View {
        Label {
            Text(Self.readable(text))  // copy: right-click → Copy Message
        } icon: {
            Image(systemName: icon)
        }
        .font(chat.font(0.85))
        .foregroundStyle(.secondary)
        .lineLimit(2)
        .padding(.leading, 32)
    }

    private var icon: String {
        if text.contains("Web_Search_Tool") { return "globe" }
        if text.contains("Code_Interpreter_Tool") { return "terminal" }
        if text.contains("Voice_Tool") { return "speaker.wave.2" }
        if text.contains("Persona_Tool") { return "person.crop.circle" }
        if text.contains("Skill_Tool") { return "puzzlepiece.extension" }
        if text.contains("Governor") { return "checkmark.shield" }
        if text.contains("Workshop") { return "hammer" }
        if text.contains("[Tool: Gmail]") { return "envelope" }
        if text.contains("[Tool: Calendar]") { return "calendar" }
        if text.contains("[Tool: Drive]") { return "doc.text" }
        if text.contains("[Tool: Slack]") { return "number" }
        if text.contains("[Tool: Discord]") { return "bubble.left.and.bubble.right" }
        if text.contains("[Tool: Connections]") { return "link" }
        if text.contains("[Tool: Developer]") { return DeveloperPicker.symbol }
        return "folder"
    }

    /// "[Tool: File_System_Tool] Reading 'a.csv'..." -> "Reading 'a.csv'…" (the icon names the tool).
    static func readable(_ status: String) -> String { StatusLine.readable(status) }
}

/// Safety first: Deny is the default button (Return); allowing takes a deliberate click.
struct ApprovalCard: View {
    let item: ChatStore.Item
    let store: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("\(store.persona.name) wants to:", systemImage: "hand.raised.fill")
                .font(.subheadline.weight(.semibold))
            ForEach(Array(item.actions.enumerated()), id: \.offset) { _, action in
                Text(action.description)
                    .font(.system(.callout, design: .monospaced))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let decisions = item.decisions {
                Label(Self.outcome(decisions, elsewhere: item.answeredElsewhere), systemImage: decisions.contains(true) ? "checkmark.circle" : "xmark.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                HStack(spacing: 8) {
                    Spacer()
                    Button("Allow") { store.decide(Array(repeating: true, count: item.actions.count)) }
                    Button("Deny") { store.decide(Array(repeating: false, count: item.actions.count)) }
                        .keyboardShortcut(.defaultAction)
                }
                .controlSize(.regular)
            }
        }
        .padding(12)
        .background(.orange.opacity(0.10), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.orange.opacity(0.4), lineWidth: 0.75))
        .padding(.leading, 32)
    }

    static func outcome(_ decisions: [Bool], elsewhere: String? = nil) -> String {
        if decisions.isEmpty, elsewhere == "slack" { return "Answered in Slack" }
        if decisions.isEmpty { return "Expired: Aura restarted before you answered" }
        if decisions.allSatisfy({ $0 }) { return "Allowed" }
        return decisions.contains(true) ? "Partly allowed" : "Denied"
    }
}

/// "Thinking…" / "Waking up…": quieter than speech, with a gentle pulse.
struct TypingIndicator: View {
    let waking: Bool
    var stopping = false
    let avatar: Image?
    @State private var animating = false

    var body: some View {
        HStack(spacing: 10) {
            AvatarView(image: avatar, size: 22, pulsing: waking)
            Text(stopping ? "Stopping…" : waking ? "Waking up…" : "Thinking")
                .font(.caption)
                .foregroundStyle(.tertiary)
            if !waking && !stopping {
                HStack(spacing: 3) {
                    ForEach(0..<3, id: \.self) { i in
                        Circle()
                            .frame(width: 4, height: 4)
                            .opacity(animating ? 0.9 : 0.25)
                            .animation(.easeInOut(duration: 0.6).repeatForever().delay(Double(i) * 0.2), value: animating)
                    }
                }
                .foregroundStyle(.tertiary)
            }
        }
        .onAppear { animating = true }
    }
}

// MARK: - input

struct InputBar: View {
    let store: ChatStore
    @State private var draft = ""
    @State private var fieldHeight: CGFloat = 0  // the text's full height (it can exceed what's shown)
    @FocusState private var focused: Bool
    @Environment(\.chatText) private var chat
    @Environment(\.bubbles) private var bubbles

    var body: some View {
        HStack(alignment: .center, spacing: 0) {
            HStack(spacing: 4) {
                DeveloperPicker(store: store)
                if store.developer == nil { ChatModelPicker(store: store) }
            }
            // Keep the controls visually centered in their own left-hand lane whether it holds
            // Aura's two selectors or one chosen Developer.
            .frame(width: store.developer == nil ? 146 : 86, alignment: .center)

            Rectangle()
                .fill(bubbles.userBorder.opacity(0.5))
                // A bare Rectangle is vertically flexible; constraining it keeps the composer
                // to the field's natural one-line height until the user adds more text.
                .frame(width: 1, height: 26)
                .padding(.horizontal, 5)
                .accessibilityHidden(true)

            // The field is always as tall as its text; the box shows up to `maxLines` of it and
            // scrolls the rest, kept at the bottom where the typing is. (A capped field alone
            // drew lines past the cap over the border: the bigger the font, the sooner.)
            ScrollView(.vertical) {
                TextField(placeholder, text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .font(chat.font())
                    .foregroundStyle(bubbles.userText)
                    // Keep writing assistance local to macOS.  Suggestions and spell checking use
                    // the person's own Writing Tools settings; draft text is never sent anywhere
                    // until they press Send.
                    .autocorrectionDisabled(false)
                    .lineLimit(1...)
                    .focused($focused)
                    .onSubmit(send)
                    .disabled(store.transport == nil || store.phase == .awaitingApproval)
                    .padding(.vertical, Self.fieldPadding)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { fieldHeight = $0 }
                    .help("Spell checking and text completion follow your Mac’s Writing Tools settings.")
            }
            .scrollIndicators(fieldHeight > cap ? .automatic : .never)
            .defaultScrollAnchor(.bottom)
            .frame(height: min(max(fieldHeight, chat.lineHeight + 2 * Self.fieldPadding), cap))  // one line until measured
            // The field's editor only scrolls its own clip view: when the caret moves above or below
            // what the box shows (arrow keys, a click, ⌘↑), scroll the box to it.
            .onReceive(NotificationCenter.default.publisher(for: NSTextView.didChangeSelectionNotification)) { note in
                guard focused, let editor = note.object as? NSTextView else { return }
                Self.reveal(editor)
            }
            .layoutPriority(1)
            if store.isBusy {
                RoundButton(symbol: store.isStopping ? "hourglass" : "stop.fill", enabled: !store.isStopping, action: store.stop)
                    .help(store.isStopping ? "Stopping current task" : "Stop")
            } else {
                RoundButton(symbol: "arrow.up", enabled: canSend, action: send).help("Send")
            }
        }
        .padding(.leading, 8)
        .padding(.trailing, 6)
        .padding(.vertical, 6)
        .background(bubbles.userBackground, in: .rect(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).strokeBorder(bubbles.userBorder, lineWidth: 1))
        .padding(12)
        .onChange(of: store.focusRequests) { focused = true }
    }

    private var canSend: Bool {
        store.canSend && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    static let maxLines: CGFloat = 15
    static let fieldPadding: CGFloat = 4  // above and below the text

    /// The box's tallest: `maxLines` lines in the chat's typeface, plus the field's padding.
    static func maxHeight(_ chat: ChatTextStyle) -> CGFloat {
        chat.lineHeight * maxLines + 2 * fieldPadding
    }

    private var cap: CGFloat { Self.maxHeight(chat) }

    /// Scroll the box (the closest scroll view around the field) so the caret is in view.
    static func reveal(_ editor: NSTextView) {
        guard let layout = editor.layoutManager, let container = editor.textContainer,
              let document = editor.enclosingScrollView?.documentView else { return }
        let at = min(editor.selectedRange().location, max(layout.numberOfGlyphs - 1, 0))
        var caret = layout.numberOfGlyphs == 0 ? .zero
            : layout.boundingRect(forGlyphRange: NSRange(location: at, length: 1), in: container)
        caret.origin.x += editor.textContainerOrigin.x
        caret.origin.y += editor.textContainerOrigin.y
        document.scrollToVisible(editor.convert(caret, to: document).insetBy(dx: 0, dy: -4))
    }

    private var placeholder: String {
        if store.transport == nil { return "Aura is offline" }
        if store.isAnsweringElsewhere { return "Aura is answering from Slack…" }
        if store.phase == .awaitingApproval { return "Answer the approval request first" }
        if let developer = store.developer, let engine = EngineInfo.named(developer) {
            return "Ask \(engine.label), through \(store.persona.name)…"
        }
        return "Ask \(store.persona.name)…"
    }

    private func send() {
        guard canSend else { return }
        store.send(draft)
        draft = ""
    }
}

/// Who the next message goes to: Aura (who may still decide to bring in a Developer), or straight to
/// Claude Code or Codex through Aura. Either way, sending a task and every file it changes need the
/// user's approval. The choice stays until it's changed.
struct DeveloperPicker: View {
    let store: ChatStore
    static let symbol = "chevron.left.forwardslash.chevron.right"
    private var engines: [EngineInfo] { store.developerInfo?.engines ?? EngineInfo.known }

    var body: some View {
        Menu {
            Picker("Send to", selection: Binding(get: { store.developer ?? "" },
                                                 set: { store.developer = $0.isEmpty ? nil : $0 })) {
                Text(store.persona.name).tag("")
                ForEach(engines) { engine in
                    Text("\(engine.label) (\(engine.plan))").tag(engine.name).disabled(engine.problem != nil)
                }
            }
            .pickerStyle(.inline)
        } label: {
            DeveloperChip(developer: store.developer)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help(store.developer == nil ? "Send to \(store.persona.name), or hand it to Claude Code or Codex"
              : "Direct handoff to \(EngineInfo.named(store.developer ?? "")?.label ?? "a Developer"): Aura's chat model is bypassed. Add 'Folder: path' on its own line to use a smaller vault folder.")
        .accessibilityLabel(accessibilityLabel)
    }

    private var accessibilityLabel: String {
        guard let developer = store.developer,
              let engine = EngineInfo.named(developer) else {
            return "Send to \(store.persona.name), Claude Code, or Codex"
        }
        return "Direct handoff to \(engine.label); Aura's chat model is bypassed"
    }
}

/// The chat box's choice, at a glance: a quiet code symbol, or the chosen Developer's name.
struct DeveloperChip: View {
    let developer: String?

    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: DeveloperPicker.symbol)
            if let developer, let engine = EngineInfo.named(developer) {
                Text(engine.label).lineLimit(1)
            }
        }
        .font(.system(size: 11, weight: .semibold))
        .foregroundStyle(developer == nil ? Color.secondary : Color.accentColor)
        .padding(.horizontal, 7)
        .frame(height: 26)
        .background(developer == nil ? Color.clear : Color.accentColor.opacity(0.14), in: .capsule)
    }
}

/// An icon and its text, close together (the top row is narrow). Every icon gets the same width,
/// so the gaps between items are the same whatever the symbol's shape.
private struct CompactLabel: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 3) { configuration.icon.frame(width: 12); configuration.title }
    }
}

/// Live resource use in one row at the top left: CPU, memory, storage, and the local model's
/// last speed (after a local reply).
private struct AuraUsageView: View {
    @ObservedObject var usage: AuraRuntimeUsage
    let performance: LocalModelPerformance?

    var body: some View {
        // One line when there's room; otherwise two (CPU and memory over storage and speed).
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) { cpu; memory; storage; speed }
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 3) {
                GridRow { cpu; memory }
                GridRow { storage; speed }
            }
        }
        .labelStyle(CompactLabel())
        .font(.system(size: 10, weight: .medium, design: .rounded).monospacedDigit())
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .fixedSize(horizontal: false, vertical: true)  // wraps to two lines in a narrow column
        .animation(.smooth(duration: 0.3), value: performance == nil)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Resource use: CPU \(usage.cpuText), memory \(usage.memoryText), storage \(usage.storageText)"
                            + (performance.map { ", \(Self.detail($0))" } ?? ""))
    }

    private var cpu: some View {
        Label(usage.cpuText, systemImage: "gauge.with.dots.needle.50percent")
            .help("CPU currently used by Aura's macOS app process.")
    }

    private var memory: some View {
        Label(usage.memoryText, systemImage: "memorychip").help(usage.memoryDetail)
    }

    private var storage: some View {
        Label(usage.storageText, systemImage: "internaldrive")
            .help("Aura app bundle, application data and logs. Local model downloads and disposable developer workspaces are excluded.")
    }

    @ViewBuilder private var speed: some View {
        if let performance {
            Label("\(performance.outputTokensPerSecond.formatted(.number.precision(.fractionLength(1)))) tok/s",
                  systemImage: "speedometer")
                .help(Self.detail(performance))
                .transition(.opacity)
        }
    }

    static func detail(_ performance: LocalModelPerformance) -> String {
        "Local model's last reply: \(performance.totalDuration.formatted(.number.precision(.fractionLength(2)))) seconds total; \(performance.outputTokens) tokens at \(performance.outputTokensPerSecond.formatted(.number.precision(.fractionLength(2)))) tokens per second."
    }
}

/// The Aura model for the next messages. It starts on the Startup model saved in Settings;
/// "Automatic" sends each message to its role's model instead.
/// Developer turns use the selected coding harness, so this control is shown only for Aura turns.
struct ChatModelPicker: View {
    let store: ChatStore
    @Environment(\.openModels) private var openModels
    static let automatic = ""
    static let labelLength = 14  // characters: the lane is 146 points, shared with the Developer button

    /// A long model file name, shortened in the middle so its family and size stay readable
    /// ("Ternary-…Q2_0"). macOS draws this menu as a native pop-up sized to its whole title, so
    /// the title itself must be short. The full name is in the menu and the help.
    static func short(_ name: String, _ length: Int = labelLength) -> String {
        guard name.count > length else { return name }
        let head = (length - 1) / 2, tail = length - 1 - head
        return "\(name.prefix(head))…\(name.suffix(tail))"
    }
    static let sections = [(provider: "ollama", title: "Local Ollama"), (provider: "llamacpp", title: "Local llama.cpp"),
                           (provider: "anthropic", title: "Anthropic cloud")]

    private var catalog: ModelCatalog? { store.modelCatalog }
    private var selection: Binding<String> {
        Binding(
            get: {
                guard let model = store.chatModel else { return Self.automatic }
                return "\(model.provider)|\(model.model)"
            },
            set: { value in
                let pieces = value.split(separator: "|", maxSplits: 1).map(String.init)
                store.pickChatModel(pieces.count == 2 ? ModelChoice(provider: pieces[0], model: pieces[1]) : nil)
            }
        )
    }

    var body: some View {
        Menu {
            Picker("Aura model", selection: selection) {
                Text("Automatic (model by task)").tag(Self.automatic)
                if let catalog {
                    ForEach(Self.sections, id: \.provider) { section in
                        let models = catalog.models(for: section.provider)
                        if !models.isEmpty {
                            Section(section.title) {
                                ForEach(models, id: \.self) { model in
                                    Text(model).tag("\(section.provider)|\(model)")
                                }
                            }
                        }
                    }
                }
            }
            .pickerStyle(.inline)
            Divider()
            Button("Model settings…") { openModels(.settings, model: store.chatModel ?? store.automaticModel) }
        } label: {
            HStack(spacing: 3) {
                Image(systemName: "cpu")
                // A long model file name keeps to its lane: shortened in the middle, so both its
                // family and its size stay readable (the full name is in the menu and the help).
                Text(modelLabel).lineLimit(1)
            }
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(store.chatModel == nil ? Color.secondary : Color.accentColor)
            .padding(.horizontal, 7)
            .frame(height: 26)
            .background(store.chatModel == nil ? Color.clear : Color.accentColor.opacity(0.14), in: .capsule)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .disabled(store.developer != nil || catalog == nil)
        .help(store.developer != nil ? "Developer turns use their selected coding harness"
              : "\(store.chatModel?.model ?? "Automatic"): choose the model for your Aura messages, or Automatic to pick one by task")
    }

    private var modelLabel: String {
        if let model = store.chatModel { return Self.short(model.model) }
        if let model = store.automaticModel { return "Auto · \(Self.short(model.model, Self.labelLength - 7))" }
        return "Auto"
    }
}

/// Aura asks to hand a job to Claude Code or Codex: who gets what (the task, and a measured copy of
/// a vault folder), then the task itself. Deny is the default (Return).
struct DeveloperTaskCard: View {
    let item: ChatStore.Item
    let task: DeveloperTask
    let store: ChatStore
    @Environment(\.chatText) private var chat

    private var label: String { EngineInfo.named(task.engine)?.label ?? "a Developer" }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("\(store.persona.name) wants to hand this to \(label):", systemImage: DeveloperPicker.symbol)
                .font(.subheadline.weight(.semibold))
            Text(task.task)
                .font(chat.font(0.95))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.primary.opacity(0.05), in: .rect(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 4) {
                Text(task.summary.prefix(1).uppercased() + task.summary.dropFirst())
                ForEach(task.notes, id: \.self) { Text($0) }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            if let decisions = item.decisions {
                Label(decisions.isEmpty ? ApprovalCard.outcome(decisions, elsewhere: item.answeredElsewhere) : decisions.contains(true) ? "Sent to \(label)" : "Denied: nothing was sent",
                      systemImage: decisions.contains(true) ? "checkmark.circle" : "xmark.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                HStack(spacing: 8) {
                    Spacer()
                    Button("Send to \(label)") { store.decide([true]) }
                    Button("Deny") { store.decide([false]) }
                        .keyboardShortcut(.defaultAction)
                }
                .controlSize(.regular)
            }
        }
        .padding(12)
        .background(.orange.opacity(0.10), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.orange.opacity(0.4), lineWidth: 0.75))
        .padding(.leading, 32)
    }
}

/// Allow or deny one file: drawn in SwiftUI (compact, and it renders in snapshots).
struct AllowDenySwitch: View {
    @Binding var allowed: Bool

    var body: some View {
        HStack(spacing: 0) {
            segment("Deny", selected: !allowed) { allowed = false }
            segment("Allow", selected: allowed) { allowed = true }
        }
        .padding(2)
        .background(Color.primary.opacity(0.08), in: .capsule)
        .accessibilityElement(children: .contain)
        .accessibilityValue(allowed ? "Allowed" : "Denied")
    }

    private func segment(_ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.caption.weight(.semibold))
                .padding(.horizontal, 9)
                .padding(.vertical, 3)
                .foregroundStyle(selected ? (title == "Allow" ? Color.white : Color.primary) : Color.secondary)
                .background(selected ? (title == "Allow" ? Color.green : Color.primary.opacity(0.14)) : Color.clear,
                            in: .capsule)
                .contentShape(.capsule)
        }
        .buttonStyle(.plain)
    }
}

/// The files a Developer changed in the vault, each with its diff and its own Allow / Deny. Every
/// file starts at Deny, Return denies them all, and applying the allowed ones takes a click.
struct FileChangesCard: View {
    let item: ChatStore.Item
    let store: ChatStore
    @State private var allowed: [Bool] = []
    @State private var open: Set<Int>

    /// `openAll`: show every diff from the start (snapshots and previews).
    init(item: ChatStore.Item, store: ChatStore, openAll: Bool = false) {
        self.item = item
        self.store = store
        _open = State(initialValue: openAll ? Set(item.actions.indices) : [])
    }

    private var changes: [FileChange] { item.actions.compactMap(FileChange.init) }
    private var engine: String {
        EngineInfo.named(EngineInfo.engineName(item.actions.first?.args?["engine"]?.string))?.label ?? "The Developer"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("\(engine) changed \(changes.count == 1 ? "1 file" : "\(changes.count) files"). Allow each one you want in your vault:",
                  systemImage: "doc.badge.gearshape")
                .font(.subheadline.weight(.semibold))
            ForEach(Array(changes.enumerated()), id: \.offset) { i, change in
                row(i, change)
            }
            if let decisions = item.decisions {
                Label(decisions.isEmpty ? ApprovalCard.outcome(decisions, elsewhere: item.answeredElsewhere)
                      : "Allowed \(decisions.filter { $0 }.count) of \(decisions.count)",
                      systemImage: decisions.contains(true) ? "checkmark.circle" : "xmark.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                HStack(spacing: 8) {
                    Button("Allow All") { allowed = Array(repeating: true, count: changes.count) }
                        .disabled(decisions.allSatisfy { $0 })
                    Spacer()
                    Button("Deny All") { store.decide(Array(repeating: false, count: changes.count)) }
                        .keyboardShortcut(.defaultAction)  // safety first, as on every approval card
                    Button(applyTitle) { store.decide(decisions) }
                        .disabled(!decisions.contains(true))
                }
                .controlSize(.regular)
            }
        }
        .padding(12)
        .background(.orange.opacity(0.10), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.orange.opacity(0.4), lineWidth: 0.75))
        .padding(.leading, 32)
        .onAppear { if allowed.count != changes.count { allowed = Array(repeating: false, count: changes.count) } }
    }

    private var decisions: [Bool] { changes.indices.map { allowed.indices.contains($0) && allowed[$0] } }

    private var applyTitle: String {
        let n = decisions.filter { $0 }.count
        return n == 0 ? "Apply Allowed" : n == decisions.count ? "Apply All" : "Apply \(n) of \(decisions.count)"
    }

    @ViewBuilder
    private func row(_ i: Int, _ change: FileChange) -> some View {
        let decided = item.decisions.flatMap { $0.indices.contains(i) ? $0[i] : nil }
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Button {
                    if open.contains(i) { open.remove(i) } else { open.insert(i) }
                } label: {
                    Image(systemName: open.contains(i) ? "chevron.down" : "chevron.right")
                        .font(.caption.weight(.semibold))
                        .frame(width: 12)
                }
                .buttonStyle(.plain)
                .disabled(change.diff.isEmpty)
                .opacity(change.diff.isEmpty ? 0.3 : 1)
                .help(change.diff.isEmpty ? "" : open.contains(i) ? "Hide the changes" : "Show the changes")
                Text(change.verb).font(.caption.weight(.semibold)).foregroundStyle(color(change.kind))
                Text(change.detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer(minLength: 4)
                if let decided {
                    Label(decided ? "Allowed" : "Denied", systemImage: decided ? "checkmark.circle.fill" : "xmark.circle")
                        .font(.caption)
                        .foregroundStyle(decided ? .green : .secondary)
                } else {
                    AllowDenySwitch(allowed: Binding(get: { allowed.indices.contains(i) && allowed[i] },
                                                     set: { if allowed.indices.contains(i) { allowed[i] = $0 } }))
                }
            }
            Text(change.path)  // whole, on its own line: the user decides on exactly this file
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, 18)
            if open.contains(i) {
                DiffView(lines: change.diff)
            }
        }
        .padding(.vertical, 2)
    }

    private func color(_ kind: FileChange.Kind) -> Color {
        switch kind {
        case .create, .createFolder: .green
        case .modify: .blue
        case .delete, .deleteFolder: .red
        }
    }
}

/// A unified diff: additions green, removals red, hunk headers quiet.
struct DiffView: View {
    let lines: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                Text(line.isEmpty ? " " : line)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(line.hasPrefix("@@") || line.hasPrefix("…") ? Color.secondary : Color.primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 6)
                    .background(background(line))
            }
        }
        .textSelection(.enabled)
        .padding(.vertical, 4)
        .background(Color.primary.opacity(0.04), in: .rect(cornerRadius: 6))
        .padding(.leading, 18)
    }

    private func background(_ line: String) -> Color {
        line.hasPrefix("+") ? .green.opacity(0.14) : line.hasPrefix("-") ? .red.opacity(0.14) : .clear
    }
}

struct RoundButton: View {
    let symbol: String
    let enabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .bold))
                .frame(width: 26, height: 26)
                .foregroundStyle(.white)
                .background(Circle().fill(enabled ? Color.accentColor : Color.secondary.opacity(0.35)))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }
}
