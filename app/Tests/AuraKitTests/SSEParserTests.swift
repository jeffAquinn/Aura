import Testing
@testable import AuraKit

struct SSEParserTests {
    private func parse(_ lines: [String]) -> [AuraEvent] {
        var parser = SSEParser()
        return lines.compactMap { parser.feed($0) }
    }

    @Test func parsesEveryEventType() {
        let events = parse([
            "event: thinking", "data: {}", "",
            "event: waking", "data: {}", "",
            "event: status", #"data: {"text": "[Tool: File_System_Tool] Reading 'notes.txt'..."}"#, "",
            "event: text", #"data: {"text": "Hello"}"#, "",
            "event: approval",
            #"data: {"actions": [{"tool": "create_folder", "args": {"path": "X"}, "description": "create folder 'X/'"}]}"#, "",
            "event: error", #"data: {"message": "Can't reach the model."}"#, "",
            "event: done", "data: {}", "",
        ])
        #expect(events == [
            .thinking, .waking,
            .status("[Tool: File_System_Tool] Reading 'notes.txt'..."),
            .text("Hello"),
            .approval([PendingAction(tool: "create_folder", description: "create folder 'X/'")]),
            .error("Can't reach the model."),
            .done,
        ])
    }

    @Test func worksWithoutBlankLines() {
        // Some line readers drop empty lines; events complete at their data line anyway.
        #expect(parse(["event: text", #"data: {"text": "a"}"#, "event: done", "data: {}"]) == [.text("a"), .done])
    }

    @Test func keepsUnicodeAndEscapedNewlines() {
        #expect(parse(["event: text", #"data: {"text": "5.3°C ✨ — naïve\nnext line"}"#]) == [.text("5.3°C ✨ — naïve\nnext line")])
    }

    @Test func ignoresUnknownEventsMalformedDataAndComments() {
        let events = parse([
            ": a comment",
            "event: future_feature", #"data: {"x": 1}"#,
            "event: text", "data: {not json",
            "event: text", #"data: {"text": "ok"}"#,
        ])
        #expect(events == [.text("ok")])
    }

    @Test func eventNameDoesNotLeakIntoTheNextEvent() {
        // A data line without its own event line is a plain "message", which Aura ignores.
        #expect(parse(["event: text", #"data: {"text": "a"}"#, #"data: {"text": "b"}"#]) == [.text("a")])
    }
}
