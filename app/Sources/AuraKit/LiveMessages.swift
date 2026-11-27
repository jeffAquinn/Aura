import Foundation

// MARK: - live messages: new Slack and Discord messages, as the server hears them

public struct IncomingMessage: Codable, Sendable, Equatable, Identifiable {
    public let id: Int
    public let source: String  // Slack | Discord
    public let place: String  // "#aura-chat", "#general in Aura HQ", "a direct message"
    public let author: String
    public let text: String
    public let kind: String  // dm | mention | channel; reply: a Slack answer Aura couldn't post
    public let at: Double

    public init(id: Int, source: String, place: String, author: String, text: String, kind: String = "channel",
                at: Double = 0) {
        self.id = id
        self.source = source
        self.place = place
        self.author = author
        self.text = text
        self.kind = kind
        self.at = at
    }

    /// Written to Aura (a DM or an @mention), not just said in a channel it's in.
    public var isToAura: Bool { kind == "dm" || kind == "mention" }

    /// "Slack · #aura-chat", or "Discord · direct message".
    public var heading: String { "\(source) · \(kind == "dm" ? "direct message" : place)" }

    /// One line for the clipboard and the conversation's text.
    public var plainText: String { "\(author) in \(heading): \(text)" }
}

public struct MessagesPage: Codable, Sendable, Equatable {
    public let messages: [IncomingMessage]
    public let latest: Int

    public init(messages: [IncomingMessage], latest: Int) {
        self.messages = messages
        self.latest = latest
    }
}

/// Follows the server's live messages with a long poll, handing each batch to `onMessages`.
/// Restarted with every new server (its message ids start again).
@MainActor
public final class MessageFeed {
    public var onMessages: ([IncomingMessage]) -> Void = { _ in }
    public private(set) var after = 0
    private var task: Task<Void, Never>?
    private let wait: Int
    private let pause: Duration

    /// `wait`: how long the server may hold each request; `pause`: the rest after an empty
    /// answer that came back at once (so a server that doesn't wait isn't asked in a tight loop).
    public init(wait: Int = 25, pause: Duration = .seconds(1)) {
        self.wait = wait
        self.pause = pause
    }

    public var isRunning: Bool { task != nil }

    public func start(_ transport: any AuraTransport) {
        task?.cancel()
        after = 0
        task = Task { [weak self, wait, pause] in
            var backoff = 1.0
            while !Task.isCancelled {
                let began = ContinuousClock.now
                do {
                    let page = try await transport.messages(after: self?.after ?? 0, wait: wait)
                    guard let self, !Task.isCancelled else { return }
                    self.after = page.latest
                    if !page.messages.isEmpty { self.onMessages(page.messages) }
                    backoff = 1
                    if page.messages.isEmpty, ContinuousClock.now - began < .seconds(1) {
                        try? await Task.sleep(for: pause)  // a server that didn't wait: don't spin
                    }
                } catch {
                    if Task.isCancelled { return }
                    try? await Task.sleep(for: .seconds(backoff))  // the server is restarting, or busy
                    backoff = min(backoff * 2, 30)
                }
            }
        }
    }

    public func stop() {
        task?.cancel()
        task = nil
    }
}
