import Foundation

/// Long text split into pieces that can each be drawn. macOS draws each SwiftUI `Text` as one
/// layer, and a layer can't be taller than about 16,000 pixels (8,000 points on a Retina screen):
/// a long message drawn as one `Text` showed nothing at all, only the panel's background. Pieces
/// of at most `limit` characters (or `lines` lines of code) stay far below that.
public enum TextChunks {
    public static let limit = 3_000
    public static let lines = 60

    /// Pieces of at most `limit` characters, broken at a line end where possible, else at a space.
    /// The line end or space a piece is broken at is dropped (the next piece starts a new line).
    public static func split(_ text: String, limit: Int = limit) -> [String] {
        guard text.count > limit else { return [text] }
        var pieces: [String] = []
        var rest = Substring(text)
        while rest.count > limit {
            let window = rest.prefix(limit)
            if let cut = window.lastIndex(of: "\n") ?? window.lastIndex(of: " "), cut > rest.startIndex {
                pieces.append(String(rest[..<cut]))
                rest = rest[rest.index(after: cut)...]
            } else {
                pieces.append(String(window))
                rest = rest[window.endIndex...]
            }
        }
        pieces.append(String(rest))
        return pieces
    }

    /// The same for styled text (Aura's Markdown), keeping each piece's styling.
    public static func split(_ text: AttributedString, limit: Int = limit) -> [AttributedString] {
        let plain = String(text.characters)
        guard plain.count > limit else { return [text] }
        var pieces: [AttributedString] = []
        var start = 0
        for piece in split(plain, limit: limit) {
            let from = text.characters.index(text.startIndex, offsetBy: start)
            let to = text.characters.index(from, offsetBy: piece.count)
            pieces.append(AttributedString(text[from..<to]))
            start += piece.count + 1  // the dropped line end or space
        }
        return pieces
    }

    /// Code in pieces of at most `lines` lines (code isn't wrapped, so it is split only at line ends).
    public static func splitLines(_ code: String, lines: Int = lines) -> [String] {
        let all = code.split(separator: "\n", omittingEmptySubsequences: false)
        guard all.count > lines else { return [code] }
        return stride(from: 0, to: all.count, by: lines).map { all[$0..<min($0 + lines, all.count)].joined(separator: "\n") }
    }
}
