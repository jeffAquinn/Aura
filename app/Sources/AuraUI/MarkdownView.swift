import AppKit
import AuraKit
import SwiftUI

/// Renders a (possibly still-streaming) Markdown reply natively: no raw `**`, `#` or ``` left.
struct MarkdownView: View {
    private let blocks: [MarkdownBlock]

    init(_ text: String) {
        blocks = Markdown.blocks(text)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(blocks) { block in
                BlockView(block: block)
                    .padding(.leading, CGFloat(block.quoteDepth) * 12)
                    .overlay(alignment: .leading) {
                        if block.quoteDepth > 0 {
                            Capsule().fill(.tertiary).frame(width: 3)
                        }
                    }
                    .foregroundStyle(block.quoteDepth > 0 ? .secondary : .primary)
            }
        }
        .textSelection(.enabled)
    }
}

private struct BlockView: View {
    let block: MarkdownBlock
    @Environment(\.chatText) private var chat

    var body: some View {
        content.font(chat.font())
    }

    @ViewBuilder private var content: some View {
        switch block.kind {
        case .paragraph:
            Text(styled(block.text, chat)).lineSpacing(3).fixedSize(horizontal: false, vertical: true)
        case let .heading(level):
            Text(styled(block.text, chat))
                .font(level <= 1 ? chat.font(1.2, weight: .semibold) : level == 2 ? chat.font(1.05, weight: .bold)
                    : chat.font(1, weight: .semibold))
                .padding(.top, 4)
        case let .listItem(marker, depth):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                    .frame(minWidth: 14, alignment: .trailing)
                Text(styled(block.text, chat)).lineSpacing(3).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 18)
        case let .code(language):
            CodeBlockView(code: block.plainText, language: language)
        case .rule:
            Divider().padding(.vertical, 2)
        case let .table(header, rows):
            TableBlockView(header: header.map { styled($0, chat) }, rows: rows.map { $0.map { styled($0, chat) } })
        }
    }
}

/// Inline `code` gets an explicit monospaced face and a subtle background; links are underlined
/// (they keep the bubble's text color, so they stay readable on any bubble).
private func styled(_ text: AttributedString, _ chat: ChatTextStyle) -> AttributedString {
    var out = text
    for run in text.runs {
        if run.inlinePresentationIntent?.contains(.code) == true {
            out[run.range].font = chat.code()
            out[run.range].backgroundColor = Color.black.opacity(0.14)
        }
        if run.link != nil {
            out[run.range].underlineStyle = .single
        }
    }
    return out
}

private struct CodeBlockView: View {
    let code: String
    let language: String?
    @State private var copied = false
    @Environment(\.chatText) private var chat

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(language ?? "code")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(code, forType: .string)
                    copied = true
                } label: {
                    Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                        .font(.caption2)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("Copy code")
            }
            FitsOrScrolls {
                Text(code)
                    .font(chat.code())
                    .fixedSize()
                    .textSelection(.enabled)
            }
        }
        .padding(10)
        .background(Color.black.opacity(0.16), in: .rect(cornerRadius: 10))  // reads on any bubble color
    }
}

private struct TableBlockView: View {
    let header: [AttributedString]
    let rows: [[AttributedString]]
    @Environment(\.chatText) private var chat

    var body: some View {
        FitsOrScrolls {
            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                GridRow {
                    ForEach(header.indices, id: \.self) { Text(header[$0]).fontWeight(.semibold) }
                }
                Divider().gridCellUnsizedAxes(.horizontal)
                ForEach(rows.indices, id: \.self) { r in
                    GridRow {
                        ForEach(rows[r].indices, id: \.self) { Text(rows[r][$0]) }
                    }
                }
            }
            .font(chat.font(0.92))
            .padding(10)
        }
        .background(Color.black.opacity(0.10), in: .rect(cornerRadius: 10))
    }
}

/// Lays content out at full width; scrolls horizontally only when a line is too wide.
private struct FitsOrScrolls<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        ViewThatFits(in: .horizontal) {
            content
            ScrollView(.horizontal, showsIndicators: false) { content }
        }
    }
}
