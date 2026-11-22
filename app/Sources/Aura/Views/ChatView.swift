import AuraKit
import SwiftUI

/// The panel's content. Functional first; the glass and visual polish come after Gemma's review.
struct ChatView: View {
    let store: ChatStore
    let avatar: Image?

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(store: store, avatar: avatar)
            Divider().opacity(0.4)
            TranscriptView(store: store, avatar: avatar)
            InputBar(store: store)
        }
        .frame(minWidth: 360, minHeight: 420)
        .background(.regularMaterial)
    }
}

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
        .overlay(Circle().strokeBorder(.white.opacity(0.55), lineWidth: 1))
        .shadow(color: .cyan.opacity(pulsing ? (bright ? 0.95 : 0.15) : 0), radius: pulsing ? size / 3 : 0)
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

    var body: some View {
        HStack(spacing: 10) {
            AvatarView(image: avatar, size: 32, pulsing: store.phase == .waking || store.connection == .starting)
            VStack(alignment: .leading, spacing: 1) {
                Text("Aura").font(.headline)
                Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            Button {
                Task { await store.newConversation() }
            } label: {
                Image(systemName: "square.and.pencil")
            }
            .buttonStyle(.borderless)
            .help("New conversation")
            .disabled(store.isBusy || store.transport == nil)
        }
        .padding(.horizontal, 14)
        .padding(.top, 14)
        .padding(.bottom, 10)
    }

    private var subtitle: String {
        switch store.connection {
        case .starting: "Starting…"
        case let .offline(reason): "Offline: \(reason)"
        case .connected:
            switch store.phase {
            case .idle: "Ready"
            case .thinking: "Thinking…"
            case .waking: "Waking up…"
            case .streaming: "Typing…"
            case .awaitingApproval: "Waiting for your approval"
            }
        }
    }
}

struct TranscriptView: View {
    let store: ChatStore
    let avatar: Image?

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 10) {
                    ForEach(store.items) { item in
                        ItemView(item: item, store: store, avatar: avatar).id(item.id)
                    }
                    if store.phase == .thinking || store.phase == .waking {
                        TypingIndicator(waking: store.phase == .waking, avatar: avatar)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(14)
            }
            .onChange(of: store.items.last?.text) { proxy.scrollTo("bottom", anchor: .bottom) }
            .onChange(of: store.items.count) { proxy.scrollTo("bottom", anchor: .bottom) }
            .onChange(of: store.phase) { proxy.scrollTo("bottom", anchor: .bottom) }
        }
    }
}

struct ItemView: View {
    let item: ChatStore.Item
    let store: ChatStore
    let avatar: Image?

    var body: some View {
        switch item.role {
        case .user:
            HStack {
                Spacer(minLength: 48)
                Text(item.text)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .foregroundStyle(.white)
                    .background(Color.accentColor.opacity(0.9), in: .rect(cornerRadius: 14))
            }
        case .aura:
            HStack(alignment: .top, spacing: 8) {
                AvatarView(image: avatar, size: 24)
                Text(Self.inlineMarkdown(item.text))
                    .textSelection(.enabled)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(.thinMaterial, in: .rect(cornerRadius: 14))
                Spacer(minLength: 24)
            }
        case .status:
            ToolChip(text: item.text)
        case .approval:
            ApprovalCard(item: item, store: store)
        case .error:
            Label(item.text, systemImage: "exclamationmark.triangle.fill")
                .font(.callout)
                .foregroundStyle(.orange)
                .padding(.leading, 32)
        }
    }

    /// Bold, italics, `code` and links; block Markdown (lists, tables, code blocks) is Sprint 4.
    static func inlineMarkdown(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
    }
}

struct ToolChip: View {
    let text: String

    var body: some View {
        Label(text, systemImage: icon)
            .font(.caption)
            .foregroundStyle(.secondary)
            .lineLimit(2)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(.quaternary.opacity(0.6), in: .capsule)
            .padding(.leading, 32)
    }

    private var icon: String {
        if text.contains("Web_Search_Tool") { return "globe" }
        if text.contains("Code_Interpreter_Tool") { return "terminal" }
        return "folder"
    }
}

/// Safety first: Deny is the default button (Return); allowing takes a deliberate click.
struct ApprovalCard: View {
    let item: ChatStore.Item
    let store: ChatStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Aura wants to:", systemImage: "hand.raised.fill")
                .font(.subheadline.weight(.semibold))
            ForEach(Array(item.actions.enumerated()), id: \.offset) { _, action in
                Text(action.description)
                    .font(.system(.callout, design: .monospaced))
                    .textSelection(.enabled)
            }
            if let decisions = item.decisions {
                Text(Self.outcome(decisions)).font(.caption).foregroundStyle(.secondary)
            } else {
                HStack {
                    Spacer()
                    Button("Allow") { store.decide(Array(repeating: true, count: item.actions.count)) }
                    Button("Deny") { store.decide(Array(repeating: false, count: item.actions.count)) }
                        .keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(12)
        .background(.orange.opacity(0.10), in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.orange.opacity(0.45)))
        .padding(.leading, 32)
    }

    static func outcome(_ decisions: [Bool]) -> String {
        if decisions.isEmpty { return "Expired: Aura restarted before you answered." }
        if decisions.allSatisfy({ $0 }) { return "Allowed" }
        return decisions.contains(true) ? "Partly allowed" : "Denied"
    }
}

struct TypingIndicator: View {
    let waking: Bool
    let avatar: Image?
    @State private var animating = false

    var body: some View {
        HStack(spacing: 8) {
            AvatarView(image: avatar, size: 24, pulsing: waking)
            if waking {
                Text("Aura is waking up…").font(.callout).foregroundStyle(.secondary)
            }
            HStack(spacing: 4) {
                ForEach(0..<3, id: \.self) { i in
                    Circle()
                        .frame(width: 6, height: 6)
                        .opacity(animating ? 1 : 0.25)
                        .animation(.easeInOut(duration: 0.6).repeatForever().delay(Double(i) * 0.2), value: animating)
                }
            }
            .foregroundStyle(.secondary)
        }
        .onAppear { animating = true }
    }
}

struct InputBar: View {
    let store: ChatStore
    @State private var draft = ""
    @FocusState private var focused: Bool

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(placeholder, text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...6)
                .focused($focused)
                .onSubmit(send)
                .disabled(store.transport == nil || store.phase == .awaitingApproval)
            if store.isBusy {
                Button(action: store.stop) { Image(systemName: "stop.circle.fill").font(.title2) }
                    .buttonStyle(.borderless)
                    .help("Stop")
            } else {
                Button(action: send) { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                    .buttonStyle(.borderless)
                    .disabled(!store.canSend || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(10)
        .background(.background.opacity(0.55), in: .rect(cornerRadius: 12))
        .padding(10)
        .onChange(of: store.focusRequests) { focused = true }
    }

    private var placeholder: String {
        store.phase == .awaitingApproval ? "Answer the approval card first" : "Ask Aura…"
    }

    private func send() {
        guard store.canSend, !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        store.send(draft)
        draft = ""
    }
}
