import AuraKit
import SwiftUI

/// The panel's content. It draws no background of its own: the panel provides native glass
/// (NSGlassEffectView), so the system material shows through.
public struct ChatView: View {
    let store: ChatStore
    let avatar: Image?
    let backdrop: Image?
    let onRestart: () -> Void
    @AppStorage(Theme.key) private var theme = Theme.glass.rawValue
    @AppStorage(ChatAppearance.sizeKey) private var fontSize = ChatAppearance.defaultSize
    @AppStorage(ChatAppearance.styleKey) private var fontStyle = ChatFontStyle.system.rawValue
    @AppStorage(ChatAppearance.userBubbleKey) private var userBubble = ""
    @AppStorage(ChatAppearance.auraBubbleKey) private var auraBubble = ChatAppearance.defaultAura

    public init(store: ChatStore, avatar: Image?, backdrop: Image? = nil, onRestart: @escaping () -> Void) {
        self.store = store
        self.avatar = avatar
        self.backdrop = backdrop
        self.onRestart = onRestart
    }

    public var body: some View {
        VStack(spacing: 0) {
            HeaderView(store: store, avatar: avatar)
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
        .background { ThemeBackdrop(theme: Theme(rawValue: theme) ?? .glass, image: backdrop) }
        .environment(\.chatText, ChatAppearance.textStyle(size: fontSize, style: fontStyle))
        .environment(\.bubbles, ChatAppearance.bubbles(user: userBubble, aura: auraBubble))
    }
}

/// "Aura Starfield": a faint, blurred starfield from the portrait, under the system glass.
struct ThemeBackdrop: View {
    let theme: Theme
    let image: Image?

    var body: some View {
        if theme == .starfield, let image {
            image.resizable().scaledToFill().blur(radius: 18).opacity(0.38).allowsHitTesting(false)
        }
    }
}

// MARK: - header

struct AvatarView: View {
    let image: Image?
    let size: CGFloat
    var pulsing = false
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
        .overlay(Circle().strokeBorder(.white.opacity(0.5), lineWidth: 0.75))
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
    @State private var showSettings = false
    @Environment(\.rendersOffscreen) private var offscreen

    private var speaking: Bool { store.speech?.isSpeaking == true }

    var body: some View {
        HStack(spacing: 10) {
            HStack(spacing: 10) {
                AvatarView(image: avatar, size: 30, pulsing: store.phase == .waking || store.connection == .starting || speaking)
                VStack(alignment: .leading, spacing: 1) {
                    Text(store.persona.name).font(.headline)
                    HStack(spacing: 5) {
                        Circle().fill(dotColor).frame(width: 6, height: 6)
                        Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
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
            Button { showSettings.toggle() } label: { Image(systemName: "gearshape") }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("Settings")
                .popover(isPresented: $showSettings, arrowEdge: .bottom) { SettingsView(store: store) }
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

    private var subtitle: String {
        switch store.connection {
        case .starting: "Starting…"
        case .offline: "Offline"
        case .connected:
            switch store.phase {
            case .idle: speaking ? "Speaking…" : "Ready"
            case .thinking: "Thinking…"
            case .waking: "Waking up…"
            case .streaming: "Typing…"
            case .awaitingApproval: "Needs your approval"
            }
        }
    }

    private var dotColor: Color {
        switch store.connection {
        case .starting: .orange
        case .offline: .red
        case .connected:
            switch store.phase {
            case .idle: .green
            case .waking, .awaitingApproval: .orange
            case .thinking, .streaming: .accentColor
            }
        }
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

    var body: some View {
        switch item.role {
        case .user:
            HStack {
                Spacer(minLength: 56)
                Text(item.text)
                    .font(chat.font())
                    .textSelection(.enabled)
                    .lineSpacing(2)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .foregroundStyle(bubbles.userText)
                    .background(bubbles.user, in: .rect(cornerRadius: 16))
                    .contextMenu { MessageMenu(item: item, store: store) }
            }
        case .aura:
            AuraMessage(item: item, store: store, avatar: avatar)
        case .status:
            ToolStatusView(text: item.text)
                .contextMenu { MessageMenu(item: item, store: store) }
        case .approval:
            if item.actions.count == 1, let build = item.actions[0].build {
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
        }
    }
}

/// A live Slack or Discord message: who wrote it, where, and what they said. Aura doesn't
/// answer it by itself; the user can ask it to.
struct IncomingMessageCard: View {
    let message: IncomingMessage
    @Environment(\.chatText) private var chat

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: message.source == "Slack" ? "number.square.fill" : "bubble.left.and.bubble.right.fill")
                .font(chat.font(1.05))
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(message.heading).font(chat.font(0.82).weight(.semibold)).foregroundStyle(.secondary)
                    if message.kind == "mention" {
                        Text("mentions Aura")
                            .font(chat.font(0.75).weight(.medium))
                            .padding(.horizontal, 6).padding(.vertical, 1)
                            .background(Color.accentColor.opacity(0.18), in: .capsule)
                    }
                }
                (Text(message.author).bold() + Text(": ") + Text(message.text))
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
    @State private var hovering = false
    @State private var copied = false

    var body: some View {
        HStack(alignment: .bottom, spacing: 6) {
            HStack(alignment: .top, spacing: 10) {
                AvatarView(image: avatar, size: 22)
                MarkdownView(item.text)
                    .foregroundStyle(bubbles.auraText)
                    .tint(bubbles.auraText)  // links: underlined, in the bubble's text color
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(bubbles.aura, in: .rect(cornerRadius: 16))
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
        return "folder"
    }

    /// "[Tool: File_System_Tool] Reading 'a.csv'..." -> "Reading 'a.csv'…" (the icon names the tool).
    static func readable(_ status: String) -> String {
        var s = status
        if s.hasPrefix("[Tool:"), let end = s.firstIndex(of: "]") {
            s = String(s[s.index(after: end)...]).trimmingCharacters(in: .whitespaces)
        }
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
    let avatar: Image?
    @State private var animating = false

    var body: some View {
        HStack(spacing: 10) {
            AvatarView(image: avatar, size: 22, pulsing: waking)
            Text(waking ? "Waking up…" : "Thinking")
                .font(.caption)
                .foregroundStyle(.tertiary)
            if !waking {
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

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(placeholder, text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .font(chat.font())
                .lineLimit(1...8)
                .focused($focused)
                .onSubmit(send)
                .disabled(store.transport == nil || store.phase == .awaitingApproval)
                .padding(.vertical, 4)
            if store.isBusy {
                RoundButton(symbol: "stop.fill", enabled: true, action: store.stop).help("Stop")
            } else {
                RoundButton(symbol: "arrow.up", enabled: canSend, action: send).help("Send")
            }
        }
        .padding(.leading, 14)
        .padding(.trailing, 6)
        .padding(.vertical, 6)
        .background(Color.primary.opacity(0.06), in: .rect(cornerRadius: 18))
        .padding(12)
        .onChange(of: store.focusRequests) { focused = true }
    }

    private var canSend: Bool {
        store.canSend && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var placeholder: String {
        if store.transport == nil { return "Aura is offline" }
        return store.phase == .awaitingApproval ? "Answer the approval request first" : "Ask \(store.persona.name)…"
    }

    private func send() {
        guard canSend else { return }
        store.send(draft)
        draft = ""
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
