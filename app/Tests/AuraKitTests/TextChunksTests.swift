import AuraKit
import Foundation
import Testing

@Suite struct TextChunksTests {
    @Test func shortTextIsOnePiece() {
        #expect(TextChunks.split("hello") == ["hello"])
        #expect(TextChunks.splitLines("a\nb") == ["a\nb"])
    }

    @Test func longTextBreaksAtLineEndsThenSpacesAndLosesNothing() {
        let line = String(repeating: "word ", count: 100) + "end"  // 503 characters
        let text = Array(repeating: line, count: 40).joined(separator: "\n")  // ~20,000 characters
        let pieces = TextChunks.split(text)
        #expect(pieces.count > 1)
        #expect(pieces.allSatisfy { $0.count <= TextChunks.limit })
        #expect(pieces.joined(separator: "\n") == text)  // every piece but the last broke at a line end
        let oneLine = String(repeating: "word ", count: 2_000)
        let words = TextChunks.split(oneLine)
        #expect(words.allSatisfy { $0.count <= TextChunks.limit } && words.joined(separator: " ") == oneLine)
        let noBreaks = String(repeating: "x", count: 7_000)
        #expect(TextChunks.split(noBreaks).map(\.count) == [3_000, 3_000, 1_000])
    }

    @Test func styledTextKeepsItsStylingInEachPiece() {
        var bold = AttributedString(String(repeating: "bold ", count: 1_000))
        bold.inlinePresentationIntent = .stronglyEmphasized
        let pieces = TextChunks.split(bold)
        #expect(pieces.count == 2)
        #expect(pieces.allSatisfy { piece in piece.runs.allSatisfy { $0.inlinePresentationIntent == .stronglyEmphasized } })
        #expect(pieces.map { String($0.characters) }.joined(separator: " ") == String(bold.characters))
    }

    @Test func codeSplitsOnlyAtLineEnds() {
        let code = (1...130).map { "let x\($0) = \($0)" }.joined(separator: "\n")
        let pieces = TextChunks.splitLines(code)
        #expect(pieces.count == 3)
        #expect(pieces.joined(separator: "\n") == code)
    }
}
