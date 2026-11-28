import AppKit
import AuraKit
import Charts
import SwiftUI

/// Renders a (possibly still-streaming) Markdown reply natively: no raw `**`, `#` or ``` left.
struct MarkdownView: View {
    private let blocks: [MarkdownBlock]
    private let localImages: [LocalImageReference]
    private let externalLinks: [ExternalLinkReference]

    init(_ text: String, vaultRoot: String? = nil) {
        blocks = Markdown.blocks(text)
        localImages = RichMedia.localImages(in: text, vaultRoot: vaultRoot)
        externalLinks = RichMedia.externalLinks(in: text)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
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
            ForEach(localImages) { LocalImageCard(reference: $0) }
            ForEach(externalLinks) { ExternalLinkCard(reference: $0) }
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
            LongText(styled(block.text, chat))
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
                LongText(styled(block.text, chat))
            }
            .padding(.leading, CGFloat(depth) * 18)
        case let .code(language):
            if let chart = InlineChart(block.plainText, language: language) {
                ChartBlockView(chart: chart)
            } else {
                CodeBlockView(code: block.plainText, language: language)
            }
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
            // Opening external content is an explicit choice in ExternalLinkCard below, never a
            // side effect of selecting/reading a message bubble.
            out[run.range].link = nil
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
                // In pieces of 60 lines: one Text taller than a layer can be draws nothing (TextChunks).
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(TextChunks.splitLines(code).enumerated()), id: \.offset) { _, piece in
                        Text(piece).font(chat.code()).fixedSize().textSelection(.enabled)
                    }
                }
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
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
                GridRow {
                    ForEach(header.indices, id: \.self) {
                        Text(header[$0]).fontWeight(.semibold).tableCell(header: true)
                    }
                }
                ForEach(rows.indices, id: \.self) { r in
                    GridRow {
                        ForEach(rows[r].indices, id: \.self) {
                            Text(rows[r][$0]).tableCell(shaded: r.isMultiple(of: 2))
                        }
                    }
                }
            }
            .font(chat.font(0.92))
            .fixedSize(horizontal: true, vertical: false)
            .padding(6)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.black.opacity(0.12), in: .rect(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.primary.opacity(0.12), lineWidth: 0.75))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Table with \(header.count) columns and \(rows.count) rows")
    }
}

private extension View {
    func tableCell(header: Bool = false, shaded: Bool = false) -> some View {
        self
            .lineLimit(nil)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 9)
            .padding(.vertical, 7)
            .background(header ? Color.primary.opacity(0.16) : shaded ? Color.primary.opacity(0.06) : .clear)
            .overlay(alignment: .trailing) { Divider().opacity(0.45) }
            .overlay(alignment: .bottom) { Divider().opacity(0.45) }
    }
}

/// A deliberately small, local chart grammar. A fenced `chart`, `graph`, or `bar` block carries
/// a two-column CSV/TSV payload: a label header and a numeric value header, followed by rows.
/// It has no script execution, URL fetching, or arbitrary chart specification language.
private struct InlineChart {
    struct Point: Identifiable {
        let label: String
        let value: Double
        var id: String { label }
    }

    let labelTitle: String
    let valueTitle: String
    let points: [Point]

    init?(_ source: String, language: String?) {
        guard ["chart", "graph", "bar"].contains(language?.lowercased() ?? ""), source.utf8.count <= 16_384 else { return nil }
        let lines = source.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }
        guard let header = lines.first, lines.count >= 2, lines.count <= 41 else { return nil }
        let separator: Character = header.contains("\t") ? "\t" : ","
        let titles = header.split(separator: separator, omittingEmptySubsequences: false).map(String.init)
        guard titles.count == 2, !titles[0].isEmpty, !titles[1].isEmpty else { return nil }
        let parsed = lines.dropFirst().compactMap { row -> Point? in
            let cells = row.split(separator: separator, omittingEmptySubsequences: false).map { $0.trimmingCharacters(in: .whitespaces) }
            guard cells.count == 2, !cells[0].isEmpty, cells[0].count <= 80,
                  let value = Double(cells[1]), value.isFinite else { return nil }
            return Point(label: cells[0], value: value)
        }
        guard parsed.count == lines.count - 1, Set(parsed.map(\.label)).count == parsed.count else { return nil }
        labelTitle = titles[0]
        valueTitle = titles[1]
        points = parsed
    }
}

private struct ChartBlockView: View {
    let chart: InlineChart
    @Environment(\.chatText) private var chat

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Chart", systemImage: "chart.bar.fill")
                .font(chat.font(0.82, weight: .semibold))
                .foregroundStyle(.secondary)
            Chart(chart.points) { point in
                BarMark(x: .value(chart.labelTitle, point.label), y: .value(chart.valueTitle, point.value))
                    .foregroundStyle(Color.accentColor.gradient)
                    .annotation(position: .top) { Text(point.value, format: .number.precision(.fractionLength(0...2))).font(.caption2) }
            }
            .chartXAxisLabel(chart.labelTitle)
            .chartYAxisLabel(chart.valueTitle)
            .frame(height: 190)
        }
        .padding(10)
        .background(Color.black.opacity(0.12), in: .rect(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.primary.opacity(0.12), lineWidth: 0.75))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Bar chart of \(chart.valueTitle) by \(chart.labelTitle), \(chart.points.count) values")
    }
}

private struct LocalImageReference: Identifiable {
    let url: URL
    let alt: String
    var id: String { url.path }
}

private struct ExternalLinkReference: Identifiable {
    let url: URL
    let label: String
    var id: String { url.absoluteString }

    var isVideo: Bool {
        let videoHosts = ["youtube.com", "www.youtube.com", "youtu.be", "vimeo.com"]
        let videoExtensions = ["mp4", "mov", "m4v", "webm"]
        return videoHosts.contains(url.host?.lowercased() ?? "") || videoExtensions.contains(url.pathExtension.lowercased())
    }

    var isSecure: Bool { url.scheme?.lowercased() == "https" }
}

/// References are parsed from Markdown source only to decide how it is presented. Local images
/// must resolve inside the current Vault; HTTP(S) URLs are shown as non-loading approval cards.
private enum RichMedia {
    private static let markdownLink = try! NSRegularExpression(pattern: #"!?\[([^\]]*)\]\((https?://[^\s)]+)\)"#)
    private static let markdownImage = try! NSRegularExpression(pattern: #"!\[([^\]]*)\]\(([^\s)]+)\)"#)
    private static let rawURL = try! NSRegularExpression(pattern: #"https?://[^\s<>\]\[()]+"#)
    private static let imageExtensions = Set(["png", "jpg", "jpeg", "gif", "heic", "tiff", "webp"])

    static func localImages(in source: String, vaultRoot: String?) -> [LocalImageReference] {
        guard let vaultRoot, !vaultRoot.isEmpty else { return [] }
        let root = URL(fileURLWithPath: vaultRoot).resolvingSymlinksInPath().standardizedFileURL
        let range = NSRange(source.startIndex..., in: source)
        var seen = Set<String>()
        return markdownImage.matches(in: source, range: range).compactMap { match in
            guard let altRange = Range(match.range(at: 1), in: source), let sourceRange = Range(match.range(at: 2), in: source),
                  let url = localURL(String(source[sourceRange]), root: root), imageExtensions.contains(url.pathExtension.lowercased()),
                  seen.insert(url.path).inserted else { return nil }
            return LocalImageReference(url: url, alt: String(source[altRange]))
        }
    }

    static func externalLinks(in source: String) -> [ExternalLinkReference] {
        let range = NSRange(source.startIndex..., in: source)
        var labels: [String: String] = [:]
        for match in markdownLink.matches(in: source, range: range) {
            guard let labelRange = Range(match.range(at: 1), in: source), let urlRange = Range(match.range(at: 2), in: source) else { continue }
            let raw = String(source[urlRange])
            labels[raw] = String(source[labelRange])
        }
        var seen = Set<String>()
        return rawURL.matches(in: source, range: range).compactMap { match in
            guard let range = Range(match.range, in: source), let url = URL(string: String(source[range])),
                  ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil,
                  seen.insert(url.absoluteString).inserted else { return nil }
            return ExternalLinkReference(url: url, label: labels[url.absoluteString] ?? url.host ?? url.absoluteString)
        }
    }

    private static func localURL(_ raw: String, root: URL) -> URL? {
        let candidate: URL
        if raw.hasPrefix("file://") {
            guard let url = URL(string: raw), url.isFileURL else { return nil }
            candidate = url
        } else if raw.hasPrefix("/") {
            candidate = URL(fileURLWithPath: raw)
        } else if !raw.contains("://") {
            candidate = root.appending(path: raw)
        } else {
            return nil
        }
        let resolved = candidate.resolvingSymlinksInPath().standardizedFileURL
        guard resolved.path.hasPrefix(root.path + "/") else { return nil }
        return resolved
    }
}

private struct LocalImageCard: View {
    let reference: LocalImageReference

    var body: some View {
        if let image = NSImage(contentsOf: reference.url) {
            VStack(alignment: .leading, spacing: 6) {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: 460, maxHeight: 300, alignment: .leading)
                    .clipShape(.rect(cornerRadius: 9))
                Label(reference.alt.isEmpty ? reference.url.lastPathComponent : reference.alt, systemImage: "photo")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(8)
            .background(Color.black.opacity(0.12), in: .rect(cornerRadius: 10))
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Local Vault image: \(reference.alt.isEmpty ? reference.url.lastPathComponent : reference.alt)")
        }
    }
}

private struct ExternalLinkCard: View {
    enum Decision { case pending, allowed, denied }

    let reference: ExternalLinkReference
    @State private var decision: Decision = .pending

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: reference.isVideo ? "play.rectangle.fill" : "link")
                .foregroundStyle(.secondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 4) {
                Text(reference.isVideo ? "External video link" : reference.isSecure ? "External link" : "Unsecured external link")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Text(reference.label).font(.callout.weight(.medium)).lineLimit(2)
                Text(reference.url.host ?? reference.url.absoluteString).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                if !reference.isSecure {
                    Label("This link does not use HTTPS.", systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
                controls
            }
        }
        .padding(10)
        .background(Color.black.opacity(0.12), in: .rect(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.primary.opacity(0.12), lineWidth: 0.75))
        .accessibilityElement(children: .contain)
        .accessibilityLabel("External \(reference.isVideo ? "video " : "")link to \(reference.url.host ?? "unknown host")")
    }

    @ViewBuilder private var controls: some View {
        switch decision {
        case .pending:
            HStack(spacing: 8) {
                Button("Deny") { decision = .denied }.buttonStyle(.borderless)
                Button("Allow") { decision = .allowed }.buttonStyle(.borderedProminent)
            }
            .controlSize(.small)
        case .allowed:
            Link("Open approved link", destination: reference.url)
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
        case .denied:
            Label("Link denied", systemImage: "xmark.shield")
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
        }
    }
}

/// Lays content out at full width; scrolls horizontally only when a line is too wide.
/// Wrapped text that may be very long, drawn in pieces (TextChunks): macOS draws a whole `Text` as
/// one layer, and a message taller than a layer can be showed nothing but the panel's background.
struct LongText: View {
    let pieces: [AttributedString]
    init(_ text: AttributedString) { pieces = TextChunks.split(text) }
    init(_ text: String) { pieces = TextChunks.split(text).map { AttributedString($0) } }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {  // 4: the line spacing, so pieces read as one text
            ForEach(Array(pieces.enumerated()), id: \.offset) { _, piece in
                Text(piece).lineSpacing(4).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct FitsOrScrolls<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        ViewThatFits(in: .horizontal) {
            content
            ScrollView(.horizontal, showsIndicators: false) { content }
        }
    }
}
