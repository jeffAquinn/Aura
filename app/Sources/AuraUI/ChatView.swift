import AuraKit
import Foundation
import SwiftUI

/// The panel's content. It draws no background of its own: the panel provides native glass
/// (NSGlassEffectView), so the system material shows through.
public struct ChatView: View {
    let store: ChatStore
    let avatar: Image?
    let backdrop: Image?
    let onRestart: () -> Void
    let onOpenHOLTerminal: () -> Void
    let onOpenWorkstation: () -> Void
    let onOpenVault: () -> Void
    let onOpenAuditLog: () -> Void
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

    public init(store: ChatStore, avatar: Image?, backdrop: Image? = nil, onRestart: @escaping () -> Void,
                onOpenHOLTerminal: @escaping () -> Void = {}, onOpenWorkstation: @escaping () -> Void = {},
                onOpenVault: @escaping () -> Void = {}, onOpenAuditLog: @escaping () -> Void = {}) {
        self.store = store
        self.avatar = avatar
        self.backdrop = backdrop
        self.onRestart = onRestart
        self.onOpenHOLTerminal = onOpenHOLTerminal
        self.onOpenWorkstation = onOpenWorkstation
        self.onOpenVault = onOpenVault
        self.onOpenAuditLog = onOpenAuditLog
    }

    public var body: some View {
        VStack(spacing: 0) {
            HeaderView(store: store, avatar: avatar, onRestart: onRestart, onOpenHOLTerminal: onOpenHOLTerminal,
                       onOpenWorkstation: onOpenWorkstation, onOpenVault: onOpenVault, onOpenAuditLog: onOpenAuditLog)
            if case let .offline(message) = store.connection {
                OfflineBanner(message: message, onRestart: onRestart)
            }
            if store.items.isEmpty, store.phase == .idle {
                WelcomeView(avatar: avatar, name: store.persona.name)
            } else {
                TranscriptView(store: store, avatar: avatar)
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
    let onRestart: () -> Void
    let onOpenHOLTerminal: () -> Void
    let onOpenWorkstation: () -> Void
    let onOpenVault: () -> Void
    let onOpenAuditLog: () -> Void
    @State private var showSettings = false
    @State private var confirmRestart = false
    @StateObject private var usage = AuraRuntimeUsage()
    @Environment(\.rendersOffscreen) private var offscreen
    @Environment(\.openWorkshop) private var openWorkshop
    @Environment(\.openConnections) private var openConnections

    private var speaking: Bool { store.speech?.isSpeaking == true }

    init(store: ChatStore, avatar: Image?, onRestart: @escaping () -> Void = {},
         onOpenHOLTerminal: @escaping () -> Void = {}, onOpenWorkstation: @escaping () -> Void = {},
         onOpenVault: @escaping () -> Void = {}, onOpenAuditLog: @escaping () -> Void = {}) {
        self.store = store
        self.avatar = avatar
        self.onRestart = onRestart
        self.onOpenHOLTerminal = onOpenHOLTerminal
        self.onOpenWorkstation = onOpenWorkstation
        self.onOpenVault = onOpenVault
        self.onOpenAuditLog = onOpenAuditLog
    }

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 10) {
                    AvatarView(image: avatar, size: 30, pulsing: store.phase == .waking || store.connection == .starting || speaking)
                    Text(store.persona.name).font(.headline)
                    Divider().frame(height: 14)
                    AuraUsageView(usage: usage)
                        .task { usage.start() }
                }
                VStack(alignment: .leading, spacing: 1) {
                    if let subtitle {
                        Text(subtitle).font(.caption.weight(.medium)).lineLimit(1)
                    }
                    StatusChips(activityColor: dotColor, activityDetail: activityDetail, health: store.health,
                                localPerformance: store.localPerformance)
                }
                .padding(.leading, 40)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
            .overlay { if !offscreen { WindowDragArea() } }  // the panel moves by its header; elsewhere, drags select text
            .help("Drag to move")
            Button {
                if speaking { store.speech?.stop() }
                Task { await store.updateVoice(VoicePatch(speak: !store.voice.speak)) }
            } label: {
                Image(systemName: store.voice.speak ? "speaker.wave.2.fill" : "speaker.slash")
                    .symbolEffect(.variableColor.iterative, isActive: speaking)
            }
            .buttonStyle(.plain)
            .foregroundStyle(store.voice.speak ? Color.accentColor : .secondary)
            .help(store.voice.speak ? "Spoken replies are on" : "Spoken replies are off")
            .disabled(store.transport == nil)
            Menu {
                Button("Settings…") { showSettings = true }
                Divider()
                Button("Connections…") { openConnections(nil) }
                Button("Open Workshop…") { openWorkshop(nil) }
                Button("Open HOL Terminal…", action: onOpenHOLTerminal)
                Button("Open Workstation…", action: onOpenWorkstation)
                Divider()
                Button("Open Vault in Finder", action: onOpenVault)
                Button("Open Audit Log", action: onOpenAuditLog)
                Divider()
                Button("Restart Aura Server") { confirmRestart = true }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .menuStyle(.borderlessButton)
                .foregroundStyle(.secondary)
                .help("Open settings and Aura panels")
                .popover(isPresented: $showSettings, arrowEdge: .bottom) { SettingsView(store: store) }
                .confirmationDialog("Restart Aura Server?", isPresented: $confirmRestart, titleVisibility: .visible) {
                    Button("Restart Server", role: .destructive, action: onRestart)
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("This temporarily disconnects Aura and interrupts active sessions.")
                }
            Button {
                Task { await store.newConversation() }
            } label: {
                Image(systemName: "square.and.pencil")
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .help("New conversation")
            .disabled(store.isBusy || store.transport == nil || store.items.isEmpty)
        }
        .padding(.horizontal, 16)
        .padding(.top, 16)
        .padding(.bottom, 10)
    }

    private var subtitle: String? {
        switch store.connection {
        case .starting: "Starting Aura…"
        case .offline: "Aura is offline"
        case .connected:
            switch store.phase {
            case .idle: speaking ? "Speaking a reply" : nil
            case .thinking: "Thinking through your request"
            case .waking: "Waking the selected model"
            case .streaming: "Writing a reply"
            case .stopping: "Stopping the current task"
            case .awaitingApproval: "Waiting for your approval"
            }
        }
    }

    private var activityDetail: String {
        switch store.connection {
        case .starting: "Aura Server is starting."
        case let .offline(message): "Aura Server is unavailable: \(message)"
        case .connected: "Aura Server is connected."
        }
    }

    private var dotColor: Color {
        switch store.connection {
        case .starting: .orange
        case .offline: .red
        case .connected:
            switch store.phase {
            case .idle: .green
            case .waking, .stopping, .awaitingApproval: .orange
            case .thinking, .streaming: .accentColor
            }
        }
    }
}

/// Three compact, evidence-scoped status chips. Each dot stays beside the label it represents.
private struct StatusChips: View {
    let activityColor: Color
    let activityDetail: String
    let health: Health?
    let localPerformance: LocalModelPerformance?

    var body: some View {
        HStack(spacing: 4) {
            StatusChip(label: activityLabel, color: activityColor, detail: activityDetail)
            StatusChip(label: runtimeLabel, color: runtimeColor, detail: runtimeDetail)
            StatusChip(label: governanceLabel, color: governanceColor, detail: governanceDetail)
            if let localPerformance { LocalPerformanceChip(performance: localPerformance) }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Session: \(activityDetail). Runtime: \(runtimeDetail). Safeguards: \(governanceDetail)")
    }

    private var activityLabel: String {
        activityDetail.hasPrefix("Aura Server is connected") ? "Connected" : activityDetail.hasPrefix("Aura Server is starting") ? "Starting" : "Offline"
    }

    private var runtimeLabel: String {
        switch health?.runtimeHealth?.state {
        case "loaded": "Model loaded"
        case "idle": "Model idle"
        case "unavailable": "Model unavailable"
        default: "Checking model"
        }
    }

    private var governanceLabel: String {
        guard let governance = health?.governanceHealth else { return "Checking safeguards" }
        return governance.state == "attention" ? "Review needed" : "Safeguards ready"
    }

    private var runtimeColor: Color {
        switch health?.runtimeHealth?.state {
        case "unavailable": .red
        case "idle", "unknown": .orange
        case "loaded": .green
        default: .secondary
        }
    }

    private var governanceColor: Color { health?.governanceHealth?.state == "attention" ? .orange : health == nil ? .secondary : .green }
    private var runtimeDetail: String { health?.runtimeHealth?.detail ?? "Runtime state has not been checked yet." }
    private var governanceDetail: String {
        guard let governance = health?.governanceHealth else { return "Governance state has not been checked yet." }
        let connections = governance.optionalConnectionAttention
        return connections.isEmpty ? governance.agent.detail : "Optional connection attention: \(connections.joined(separator: ", ")). \(governance.agent.detail)"
    }
}

/// The local model's last completed generation, shown alongside the status it belongs to.
private struct LocalPerformanceChip: View {
    let performance: LocalModelPerformance
    @Environment(\.bubbles) private var bubbles

    private var label: String {
        "\(performance.totalDuration.formatted(.number.precision(.fractionLength(1))))s · \(performance.outputTokensPerSecond.formatted(.number.precision(.fractionLength(1)))) tok/s"
    }

    private var detail: String {
        "Local model performance: \(performance.totalDuration.formatted(.number.precision(.fractionLength(2)))) seconds total; \(performance.outputTokens) generated tokens at \(performance.outputTokensPerSecond.formatted(.number.precision(.fractionLength(2)))) tokens per second."
    }

    var body: some View {
        Label(label, systemImage: "speedometer")
            .font(.caption2.weight(.medium))
            .monospacedDigit()
            .foregroundStyle(.secondary)
            .padding(.horizontal, 6)
            .padding(.vertical, 3)
            .overlay(Capsule().strokeBorder(bubbles.auraBorder, lineWidth: 1))
            .help(detail)
            .accessibilityLabel(detail)
    }
}

/// A small outlined capsule that uses the configured Aura-message border, so status reads as
/// part of the same visual system without impersonating a message bubble.
private struct StatusChip: View {
    let label: String
    let color: Color
    let detail: String
    @Environment(\.bubbles) private var bubbles

    var body: some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(label).lineLimit(1)
        }
        .font(.caption2.weight(.medium))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 6)
        .padding(.vertical, 3)
        .overlay(Capsule().strokeBorder(bubbles.auraBorder, lineWidth: 1))
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

struct WelcomeView: View {
    let avatar: Image?
    var name = "Aura"

    var body: some View {
        VStack(spacing: 10) {
            Spacer()
            AvatarView(image: avatar, size: 64)
            Text("Hi, I'm \(name).").font(.title3.weight(.semibold))
            Text("Ask me anything, search the web, run some Python,\nor have me work in your vault.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Text("⌥Space shows or hides this panel")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .padding(.top, 4)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(20)
    }
}

struct TranscriptView: View {
    let store: ChatStore
    let avatar: Image?

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                TranscriptContent(store: store, avatar: avatar, lazy: true)
                Color.clear.frame(height: 1).id("bottom")
            }
            .scrollIndicators(.automatic)
            // A restored conversation arrives before this view exists (the welcome view shows
            // while the transcript is empty), so the onChange handlers below never fire for it.
            // Start at the last message instead of the top.
            .defaultScrollAnchor(.bottom, for: .initialOffset)
            .onChange(of: store.items.last?.text) { proxy.scrollTo("bottom", anchor: .bottom) }
            .onChange(of: store.items.count) { proxy.scrollTo("bottom", anchor: .bottom) }
            .onChange(of: store.phase) { proxy.scrollTo("bottom", anchor: .bottom) }
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
            ForEach(Array(store.items.enumerated()), id: \.element.id) { index, item in
                ItemView(item: item, store: store, avatar: avatar)
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
                    Text(item.text)
                        .font(chat.font())
                        .textSelection(.enabled)
                        .lineSpacing(4)
                        .padding(.horizontal, ChatAppearance.textPadding(textPadding))
                        .padding(.vertical, ChatAppearance.verticalTextPadding(textPadding))
                        .foregroundStyle(bubbles.userText)
                        .background(bubbles.userBackground, in: .rect(cornerRadius: 16))
                        .overlay(RoundedRectangle(cornerRadius: 16).strokeBorder(bubbles.userBorder, lineWidth: 1))
                        .contextMenu { MessageMenu(item: item, store: store) }
                }
                if let developer = item.developer {  // handed to Claude Code or Codex in the chat box
                    Label("To \(EngineInfo.named(developer)?.label ?? developer)", systemImage: DeveloperPicker.symbol)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.trailing, 6)
                }
            }
        case .aura:
            AuraMessage(item: item, store: store, avatar: avatar)
        case .status:
            ToolStatusView(text: item.text)
                .contextMenu { MessageMenu(item: item, store: store) }
        case .approval:
            if item.isFileChanges {
                FileChangesCard(item: item, store: store)
            } else if item.actions.count == 1, let task = item.actions[0].developerTask {
                DeveloperTaskCard(item: item, task: task, store: store)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label("HOL session", systemImage: "terminal.fill")
                .font(chat.font(0.82, weight: .semibold))
                .foregroundStyle(.secondary)
            Text(text)
                .font(chat.code(0.84))
                .textSelection(.enabled)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .background(Color.accentColor.opacity(0.09), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Color.accentColor.opacity(0.28), lineWidth: 1))
        .padding(.leading, 32)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Human Out of Loop session output: \(text)")
    }
}

/// A live Slack or Discord message: who wrote it, where, and what they said. Aura answers only
/// the user's own messages to it in Slack (when switched on); anything else, the user can ask it to.
struct IncomingMessageCard: View {
    let message: IncomingMessage
    @Environment(\.chatText) private var chat

    /// Aura's own answers are Markdown (bold, links); other people's words are shown as written.
    private var messageText: AttributedString {
        if message.kind == "reply",
           let rich = try? AttributedString(markdown: message.text,
                                            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
            return rich
        }
        return AttributedString(message.text)
    }

    private var badge: String? {
        switch message.kind {
        case "mention": "mentions Aura"
        case "command": "you, to Aura"
        case "reply": "Aura answered"
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
struct AuraMessage: View {
    let item: ChatStore.Item
    let store: ChatStore
    let avatar: Image?
    @Environment(\.bubbles) private var bubbles
    @AppStorage(ChatAppearance.avatarSizeKey) private var avatarSize = "small"
    @AppStorage(ChatAppearance.textPaddingKey) private var textPadding = ChatAppearance.defaultTextPadding
    @State private var hovering = false
    @State private var copied = false

    var body: some View {
        HStack(alignment: .bottom, spacing: 6) {
            HStack(alignment: .center, spacing: 10) {
                AvatarView(image: avatar, size: ChatAppearance.avatarSize(avatarSize))
                MarkdownView(item.text, vaultRoot: store.health?.vault)
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
    static func readable(_ status: String) -> String {
        var s = status
        if s.hasPrefix("[Tool:"), let end = s.firstIndex(of: "]") {
            s = String(s[s.index(after: end)...]).trimmingCharacters(in: .whitespaces)
        }
        if s.hasPrefix("· ") { s = String(s.dropFirst(2)) }  // a Developer's live progress line
        if s.hasSuffix("...") { s = String(s.dropLast(3)) + "…" }
        return s
    }
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
                Label(Self.outcome(decisions), systemImage: decisions.contains(true) ? "checkmark.circle" : "xmark.circle")
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

    static func outcome(_ decisions: [Bool]) -> String {
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

            TextField(placeholder, text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(chat.font())
                .foregroundStyle(bubbles.userText)
                // Keep writing assistance local to macOS.  Suggestions and spell checking use
                // the person's own Writing Tools settings; draft text is never sent anywhere
                // until they press Send.
                .autocorrectionDisabled(false)
                .lineLimit(1...15)
                .focused($focused)
                .onSubmit(send)
                .disabled(store.transport == nil || store.phase == .awaitingApproval)
                .padding(.vertical, 4)
                .layoutPriority(1)
                .help("Spell checking and text completion follow your Mac’s Writing Tools settings.")
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

    private var placeholder: String {
        if store.transport == nil { return "Aura is offline" }
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

/// Live, scoped resource use for Aura's macOS app process.
private struct AuraUsageView: View {
    @ObservedObject var usage: AuraRuntimeUsage

    var body: some View {
        HStack(spacing: 7) {
            Label("CPU \(usage.cpuText)", systemImage: "gauge.with.dots.needle.50percent")
                .help("CPU currently used by Aura's macOS app process.")
            Label("Disk \(usage.storageText)", systemImage: "internaldrive")
                .help("Aura app bundle, application data and logs. Local model downloads and disposable developer workspaces are excluded.")
        }
        .font(.system(size: 10, weight: .medium, design: .rounded).monospacedDigit())
        .foregroundStyle(.secondary)
        .fixedSize()
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Aura resource use: CPU \(usage.cpuText), storage \(usage.storageText)")
    }
}

/// A per-message override. "Automatic" follows the user's Chat role configured in Settings.
/// Developer turns use the selected coding harness, so this control is shown only for Aura turns.
struct ChatModelPicker: View {
    let store: ChatStore
    static let automatic = ""

    private var catalog: ModelCatalog? { store.modelCatalog }
    private var selection: Binding<String> {
        Binding(
            get: {
                guard let model = store.chatModel else { return Self.automatic }
                return "\(model.provider)|\(model.model)"
            },
            set: { value in
                guard !value.isEmpty else { store.chatModel = nil; return }
                let pieces = value.split(separator: "|", maxSplits: 1).map(String.init)
                store.chatModel = pieces.count == 2 ? ModelChoice(provider: pieces[0], model: pieces[1]) : nil
            }
        )
    }

    var body: some View {
        Menu {
            Picker("Aura model", selection: selection) {
                Text("Automatic (Chat setting)").tag(Self.automatic)
                if let catalog {
                    if !catalog.ollamaModels.isEmpty {
                        Section("Local Ollama") {
                            ForEach(catalog.ollamaModels, id: \.self) { model in
                                Text(model).tag("ollama|\(model)")
                            }
                        }
                    }
                    if !catalog.anthropicModels.isEmpty {
                        Section("Anthropic cloud") {
                            ForEach(catalog.anthropicModels, id: \.self) { model in
                                Text(model).tag("anthropic|\(model)")
                            }
                        }
                    }
                }
            }
            .pickerStyle(.inline)
        } label: {
            HStack(spacing: 3) {
                Image(systemName: "cpu")
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
              : "Choose a model for the next Aura message, or follow the Chat setting")
    }

    private var modelLabel: String {
        if let model = store.chatModel { return model.model }
        if let model = store.automaticModel { return "Auto · \(model.model)" }
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
                Label(decisions.isEmpty ? ApprovalCard.outcome(decisions) : decisions.contains(true) ? "Sent to \(label)" : "Denied: nothing was sent",
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
                Label(decisions.isEmpty ? ApprovalCard.outcome(decisions)
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
