import Foundation

/// One renderable block of a Markdown reply. Inline styling (bold, italics, `code`, links)
/// lives in `text` as AttributedString attributes; block structure lives in `kind`.
public struct MarkdownBlock: Identifiable, Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case paragraph
        case heading(level: Int)
        /// A list item's paragraph. `marker` is "•", "◦" or "3."; empty for a later paragraph of
        /// the same item. `depth` 0 is the outermost list.
        case listItem(marker: String, depth: Int)
        case code(language: String?)
        case rule
        case table(header: [AttributedString], rows: [[AttributedString]])
    }

    public let id: Int
    public var kind: Kind
    public var text: AttributedString
    /// How many block quotes contain this block.
    public var quoteDepth: Int

    public var plainText: String { String(text.characters) }
}

public enum Markdown {
    /// Parse a (possibly still-streaming) reply into blocks. Never throws: unparseable input
    /// comes back as one plain paragraph.
    public static func blocks(_ source: String) -> [MarkdownBlock] {
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .full, failurePolicy: .returnPartiallyParsedIfPossible)
        guard !source.isEmpty,
              let parsed = try? AttributedString(markdown: chatLineBreaks(source), options: options)
        else {
            return source.isEmpty ? [] : [MarkdownBlock(id: 0, kind: .paragraph, text: AttributedString(source), quoteDepth: 0)]
        }

        var blocks: [MarkdownBlock] = []
        var index: [Int: Int] = [:]  // block identity -> position in `blocks`
        var firstParagraphOfItem: [Int: Int] = [:]  // list item identity -> its first paragraph
        var tables: [Int: TableBuilder] = [:]

        for run in parsed.runs {
            var piece = AttributedString(parsed[run.range])
            piece.presentationIntent = nil
            let components = run.presentationIntent?.components ?? []  // innermost first

            if let table = components.first(where: { if case .table = $0.kind { true } else { false } }) {
                tables[table.identity, default: TableBuilder()].add(piece, components)
                if index[table.identity] == nil {
                    index[table.identity] = blocks.count
                    blocks.append(MarkdownBlock(id: table.identity, kind: .table(header: [], rows: []), text: "", quoteDepth: quoteDepth(components)))
                }
                continue
            }

            let leaf = components.first { component in
                switch component.kind {
                case .paragraph, .header, .codeBlock, .thematicBreak: true
                default: false
                }
            }
            let id = leaf?.identity ?? -(blocks.count + 1)
            if let i = index[id] {
                blocks[i].text.append(piece)
                continue
            }
            index[id] = blocks.count
            blocks.append(MarkdownBlock(
                id: id, kind: kind(leaf, components, &firstParagraphOfItem), text: piece,
                quoteDepth: quoteDepth(components)))
        }

        for (id, table) in tables {
            if let i = index[id] { blocks[i].kind = .table(header: table.header, rows: table.rows) }
        }
        for i in blocks.indices {
            if case .code = blocks[i].kind {  // drop the fence's trailing newline
                var text = blocks[i].text
                while text.characters.last == "\n" { text.removeSubrange(text.index(beforeCharacter: text.endIndex)..<text.endIndex) }
                blocks[i].text = text
            }
        }
        // Never render non-empty text as nothing (e.g. a lone "1." mid-stream).
        if blocks.isEmpty, !source.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return [MarkdownBlock(id: 0, kind: .paragraph, text: AttributedString(source), quoteDepth: 0)]
        }
        return blocks
    }

    private static func kind(
        _ leaf: PresentationIntent.IntentType?, _ components: [PresentationIntent.IntentType],
        _ firstParagraphOfItem: inout [Int: Int]
    ) -> MarkdownBlock.Kind {
        switch leaf?.kind {
        case let .header(level): return .heading(level: level)
        case let .codeBlock(language): return .code(language: language.flatMap { $0.isEmpty ? nil : $0 })
        case .thematicBreak: return .rule
        default: break
        }
        // A paragraph: inside a list item?
        guard let itemIndex = components.firstIndex(where: { if case .listItem = $0.kind { true } else { false } }),
              case let .listItem(ordinal) = components[itemIndex].kind
        else { return .paragraph }
        let item = components[itemIndex].identity
        let depth = components.filter {
            switch $0.kind { case .orderedList, .unorderedList: true; default: false }
        }.count - 1
        if firstParagraphOfItem[item] != nil { return .listItem(marker: "", depth: depth) }
        firstParagraphOfItem[item] = leaf?.identity ?? 0
        let ordered = components[(itemIndex + 1)...].first {
            switch $0.kind { case .orderedList, .unorderedList: true; default: false }
        }.map { if case .orderedList = $0.kind { true } else { false } } ?? false
        return .listItem(marker: ordered ? "\(ordinal)." : (depth == 0 ? "•" : "◦"), depth: max(depth, 0))
    }

    private static func quoteDepth(_ components: [PresentationIntent.IntentType]) -> Int {
        components.filter { if case .blockQuote = $0.kind { true } else { false } }.count
    }

    private struct TableBuilder {
        var header: [AttributedString] = []
        var rowsByIndex: [Int: [Int: AttributedString]] = [:]

        mutating func add(_ piece: AttributedString, _ components: [PresentationIntent.IntentType]) {
            var column = 0, row: Int?  // nil = header row
            for c in components {
                switch c.kind {
                case let .tableCell(columnIndex): column = columnIndex
                case let .tableRow(rowIndex): row = rowIndex
                default: break
                }
            }
            if let row {
                rowsByIndex[row, default: [:]][column, default: ""].append(piece)
            } else {
                while header.count <= column { header.append("") }
                header[column].append(piece)
            }
        }

        var rows: [[AttributedString]] {
            rowsByIndex.keys.sorted().map { r in
                let cells = rowsByIndex[r]!
                return (0...(cells.keys.max() ?? 0)).map { cells[$0] ?? "" }
            }
        }
    }

    /// Chat replies use single newlines as line breaks; CommonMark would join those lines with a
    /// space. Turn them into hard breaks, except inside code fences and before block syntax.
    static func chatLineBreaks(_ source: String) -> String {
        let lines = source.components(separatedBy: "\n")
        var out: [String] = []
        var inFence = false
        for (i, line) in lines.enumerated() {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") { inFence.toggle() }
            let next = i + 1 < lines.count ? lines[i + 1].trimmingCharacters(in: .whitespaces) : ""
            let breakHere = !inFence && !trimmed.isEmpty && !next.isEmpty
                && !startsBlock(next) && !startsBlock(trimmed, structuralOnly: true)
            out.append(breakHere ? line + "  " : line)
        }
        return out.joined(separator: "\n")
    }

    private static func startsBlock(_ line: String, structuralOnly: Bool = false) -> Bool {
        if line.hasPrefix("|") || line.hasPrefix("```") || line.hasPrefix("~~~") { return true }
        if structuralOnly { return line.hasPrefix("#") }
        if line.hasPrefix("#") || line.hasPrefix(">") { return true }
        if line.allSatisfy({ $0 == "=" }) || line.allSatisfy({ $0 == "-" }) { return true }  // setext / rule
        if ["- ", "* ", "+ "].contains(where: line.hasPrefix) { return true }
        if let dot = line.firstIndex(where: { $0 == "." || $0 == ")" }),
           !line[..<dot].isEmpty, line[..<dot].allSatisfy(\.isNumber) { return true }
        return false
    }
}
