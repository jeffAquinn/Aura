import Foundation
import Testing
@testable import AuraKit

struct MarkdownTests {
    private func kinds(_ md: String) -> [MarkdownBlock.Kind] { Markdown.blocks(md).map(\.kind) }
    private func texts(_ md: String) -> [String] { Markdown.blocks(md).map(\.plainText) }

    @Test func noRawMarkdownSymbolsSurvive() {
        let md = "## Plan\nHere is **bold**, *italic*, `code` and a [link](https://example.com)."
        let blocks = Markdown.blocks(md)
        #expect(blocks.map(\.kind) == [.heading(level: 2), .paragraph])
        let body = blocks[1].plainText
        #expect(body == "Here is bold, italic, code and a link.")
        #expect(!body.contains("**") && !body.contains("`") && !body.contains("]("))
    }

    @Test func inlineStylesAreKeptAsAttributes() throws {
        let text = Markdown.blocks("A **strong** and `mono` [site](https://example.com)")[0].text
        let strong = try #require(text.range(of: "strong"))
        #expect(text[strong].inlinePresentationIntent == .stronglyEmphasized)
        let mono = try #require(text.range(of: "mono"))
        #expect(text[mono].inlinePresentationIntent == .code)
        let site = try #require(text.range(of: "site"))
        #expect(text[site].link == URL(string: "https://example.com"))
    }

    @Test func orderedUnorderedAndNestedLists() {
        let md = """
        3. Third
        4. Fourth
           - nested
             - deeper
        - top bullet
        """
        #expect(kinds(md) == [
            .listItem(marker: "3.", depth: 0),
            .listItem(marker: "4.", depth: 0),
            .listItem(marker: "◦", depth: 1),
            .listItem(marker: "◦", depth: 2),
            .listItem(marker: "•", depth: 0),
        ])
        #expect(texts(md) == ["Third", "Fourth", "nested", "deeper", "top bullet"])
    }

    @Test func aListItemWithTwoParagraphsShowsOneMarker() {
        let md = "- First paragraph\n\n  Second paragraph\n- Next"
        #expect(kinds(md) == [
            .listItem(marker: "•", depth: 0), .listItem(marker: "", depth: 0), .listItem(marker: "•", depth: 0),
        ])
    }

    @Test func codeBlocksKeepExactContentAndLanguage() {
        let md = "```python\nfor i in range(3):\n    print(i)  # **not bold**\n```"
        let block = Markdown.blocks(md)[0]
        #expect(block.kind == .code(language: "python"))
        #expect(block.plainText == "for i in range(3):\n    print(i)  # **not bold**")
        #expect(Markdown.blocks("```\nx\n```")[0].kind == .code(language: nil))
    }

    @Test func quotesTablesAndRules() {
        let md = """
        > Quoted **text**

        | City | Temp |
        |------|------|
        | London | 5.3°C |
        | Paris | 8°C |

        ---
        Done
        """
        let blocks = Markdown.blocks(md)
        #expect(blocks[0].kind == .paragraph && blocks[0].quoteDepth == 1)
        guard case let .table(header, rows) = blocks[1].kind else {
            Issue.record("expected a table, got \(blocks[1].kind)")
            return
        }
        #expect(header.map { String($0.characters) } == ["City", "Temp"])
        #expect(rows.map { $0.map { String($0.characters) } } == [["London", "5.3°C"], ["Paris", "8°C"]])
        #expect(blocks[2].kind == .rule)
        #expect(blocks[3].plainText == "Done")
    }

    @Test func singleNewlinesStayLineBreaks() {
        #expect(texts("Roses are red\nViolets are blue") == ["Roses are red\nViolets are blue"])
        #expect(texts("Para one\n\nPara two") == ["Para one", "Para two"])
    }

    @Test func lineBreaksNeverLeakIntoCodeOrSplitLists() {
        #expect(texts("```\na\nb\n```") == ["a\nb"])  // no trailing hard-break spaces in code
        #expect(kinds("Intro line\n- item") == [.paragraph, .listItem(marker: "•", depth: 0)])
    }

    @Test func partialStreamingInputDegradesGracefully() {
        // An unclosed fence is already code; an unclosed ** stays literal until it closes.
        #expect(kinds("Text\n```py\nprint(1") == [.paragraph, .code(language: "py")])
        #expect(texts("Some **bol") == ["Some **bol"])
        #expect(texts("Some **bold**") == ["Some bold"])
        #expect(Markdown.blocks("").isEmpty)
        #expect(texts("1.") == ["1."])  // an empty list marker must not make the text vanish
    }
}

struct FriendlyMessageTests {
    @Test func offlineAndAuthErrorsAreNonTechnical() {
        #expect(FriendlyMessage.describe(URLError(.cannotConnectToHost)) == "Aura is offline. Please check your connection.")
        #expect(FriendlyMessage.describe(URLError(.networkConnectionLost)) == FriendlyMessage.offline)
        let auth = FriendlyMessage.describe(AuraClientError.http(status: 401, message: "Missing or invalid token."))
        #expect(auth.contains("Restart Aura") && !auth.contains("401") && !auth.lowercased().contains("token"))
    }

    @Test func aFailedTurnDoesNotRepeatTheOfflineBanner() {
        let message = FriendlyMessage.turnFailed(URLError(.networkConnectionLost))
        #expect(message != FriendlyMessage.offline && message.contains("offline"))
    }

    @Test func busyAndPendingApprovalAreExplained() {
        #expect(FriendlyMessage.describe(AuraClientError.http(status: 409, message: "An approval is pending; …"))
            == "Please answer Aura's approval request first.")
        #expect(FriendlyMessage.describe(AuraClientError.http(status: 409, message: "busy"))
            == "Aura is still working on your last request.")
    }

    @Test func nothingTechnicalEverLeaks() {
        let errors: [Error] = [
            AuraClientError.http(status: 500, message: "Traceback (most recent call last)…"),
            AuraClientError.badResponse,
            ServerManager.ServerError.exitedEarly("/Users/x/Library/Logs/Aura/server-stderr.log"),
            ServerManager.ServerError.uvNotFound,
            CocoaError(.fileReadCorruptFile),
        ]
        for error in errors {
            let message = FriendlyMessage.describe(error)
            #expect(!message.contains("Traceback") && !message.contains("/Users/") && !message.contains("Error Domain"))
            #expect(!message.isEmpty)
        }
    }

    @Test func copiedRepliesArePlainText() {
        let reply = "## Plan\nIt's **5.3°C** and *overcast*.\n\n1. Take a `coat`\n   - umbrella\n\n> quoted\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nprint(1)\n```"
        let text = Markdown.plainText(reply)
        #expect(text == "Plan\n\nIt's 5.3°C and overcast.\n\n1. Take a coat\n\n    ◦ umbrella\n\n> quoted\n\nA\tB\n1\t2\n\nprint(1)")
        #expect(!text.contains("**") && !text.contains("```") && !text.contains("##"))
    }
}
