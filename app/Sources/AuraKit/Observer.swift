import Foundation
import Observation

// MARK: - the Observer (docs/observer.md): a live, read-only log of Aura's own activity

public struct ObserverEvent: Identifiable, Equatable, Sendable {
    public enum Category: String, CaseIterable, Sendable {
        case turns = "Turns", actions = "Actions", vault = "Vault", containers = "Containers"
        case egress = "Egress", connections = "Connections", alerts = "Alerts", other = "Other"
    }

    public let id: Int
    public let time: String  // UTC, ISO 8601: as the server recorded it, or when the frame arrived
    public let clock: String  // "23:12:09", the Mac's time zone, 24-hour: worked out once, not per render
    public let category: Category
    public let title: String
    public let details: [ActivityEntry.Field]
    /// From the audit log, so a history reload brings it back; turn and approval rows exist only live.
    public let audited: Bool

    public init(id: Int, time: String, category: Category, title: String, details: [ActivityEntry.Field] = [],
                audited: Bool = false) {
        self.id = id
        self.time = time
        self.clock = Self.clock(time)
        self.category = category
        self.title = title
        self.details = details
        self.audited = audited
    }

    // Formatters are costly to make, and a history load makes hundreds of events at once.
    nonisolated(unsafe) static let iso = ISO8601DateFormatter()  // UTC, internet date-time by default
    nonisolated(unsafe) private static let hms: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    static func clock(_ iso: String) -> String {
        guard let date = Self.iso.date(from: iso) else { return "" }
        return hms.string(from: date)
    }

    /// The server names each entry's kind (aura.audit.category); anything unknown is Other.
    public static func category(named name: String) -> Category {
        Category.allCases.first { $0.rawValue.lowercased() == name.lowercased() } ?? .other
    }

    /// An audit entry as one row: "Container started · sandbox · network none · 512m".
    public static func from(_ entry: ActivityEntry, id: Int) -> ObserverEvent {
        let name = entry.event.replacingOccurrences(of: "_", with: " ")
        let summary = ["kind", "tool", "decision", "engine", "skill", "path", "network", "memory", "exit_code", "seconds",
                       "query", "reason", "connection", "account"]
            .compactMap { key in entry.field(key).map { key == "seconds" ? "\($0) s" : key == "exit_code" ? "exit \($0)" : $0 } }
            .prefix(4)
        return ObserverEvent(id: id, time: entry.ts, category: category(named: entry.category),
                             title: ([name.prefix(1).uppercased() + name.dropFirst()] + summary).joined(separator: " · "),
                             details: entry.fields, audited: true)
    }
}

@MainActor
@Observable
public final class ObserverStore {
    /// What the window shows, oldest first (bounded).
    public private(set) var events: [ObserverEvent] = []
    public private(set) var paused = false
    /// Events that arrived while paused: shown on Resume, in order.
    public private(set) var held: [ObserverEvent] = []
    /// The categories shown (all, until the user filters).
    public var shown: Set<ObserverEvent.Category> = Set(ObserverEvent.Category.allCases) {
        didSet { visible = events.filter { shown.contains($0.category) } }
    }
    /// `events` in the shown categories: kept up to date as events arrive, not filtered per render.
    public private(set) var visible: [ObserverEvent] = []
    public static let keep = 1000

    public var transport: (any AuraTransport)? {
        didSet { if transport == nil { stop() } }
    }

    @ObservationIgnored private var feed: Task<Void, Never>?
    @ObservationIgnored private(set) var seq = -1
    @ObservationIgnored private var nextID = 0
    /// Bumped by every start and stop: a start that awaited its history past either gives up.
    @ObservationIgnored private var generation = 0
    @ObservationIgnored var retry = 0.5  // the first wait before reconnecting; tests shorten it

    public init() {}

    /// Load the recent history, then follow the live feed from exactly there.
    public func start() async {
        guard let transport else { return }
        stop()
        let mine = generation
        let page = (try? await transport.activity(limit: 200)) ?? ActivityPage(entries: [], latest: -1)
        guard mine == generation else { return }  // stopped (window closed) or restarted meanwhile
        events = []
        visible = []
        // Paused through a reload: keep what only the live feed had; the history brings back the rest.
        held = paused ? held.filter { !$0.audited } : []
        page.entries.forEach { show(ObserverEvent.from($0, id: id())) }  // the past: shown even while paused
        seq = page.latest
        feed = followFeed(from: { [weak self] in self?.seq ?? -1 }, retry: retry,
                          transport: { [weak self] in self?.transport },
                          apply: { [weak self] in self?.apply($0) })
    }

    public func stop() {
        generation += 1
        feed?.cancel()
        feed = nil
    }

    public func pause() { paused = true }

    public func resume() {
        paused = false
        held.forEach(show)
        held = []
    }

    func apply(_ frame: MirrorFrame) {
        seq = frame.seq
        if frame.kind == "reset" {
            Task { await start() }  // the feed lost track: reload the history
            return
        }
        if let event = event(for: frame) { append(event) }
    }

    /// Turns and approvals come from the turn frames; everything else from the audit entries.
    /// (Reply text, thinking and model frames are the chat's, not the Observer's.)
    private func event(for frame: MirrorFrame) -> ObserverEvent? {
        if let entry = frame.activity { return ObserverEvent.from(entry, id: id()) }
        let source = frame.origin == "slack" ? "from Slack" : "on the Mac"
        switch frame.kind {
        case "turn":
            return ObserverEvent(id: id(), time: Self.now(), category: .turns,
                                 title: frame.said == nil ? "Turn started (answering a card) · \(source)" : "Turn started · \(source)",
                                 details: [ActivityEntry.Field(key: "turn", value: frame.turnID ?? "")])
        case "done":
            return ObserverEvent(id: id(), time: Self.now(), category: .turns, title: "Turn ended · \(source)",
                                 details: [ActivityEntry.Field(key: "turn", value: frame.turnID ?? "")])
        case "stopped":
            return ObserverEvent(id: id(), time: Self.now(), category: .turns, title: "Turn stopped · \(source)")
        default:
            break
        }
        switch frame.event {
        case let .status(text)?:
            return ObserverEvent(id: id(), time: Self.now(), category: .actions,
                                 title: StatusLine.readable(text))
        case let .approval(actions)?:
            return ObserverEvent(id: id(), time: Self.now(), category: .actions,
                                 title: "Approval asked · " + actions.map { $0.tool }.joined(separator: ", "),
                                 details: actions.map { ActivityEntry.Field(key: $0.tool, value: $0.description) })
        case let .error(message)?:
            return ObserverEvent(id: id(), time: Self.now(), category: .alerts, title: "Turn failed · \(message)")
        default:
            return nil
        }
    }

    private func append(_ event: ObserverEvent) {
        guard paused else { return show(event) }
        held.append(event)
        if held.count > Self.keep { held.removeFirst(held.count - Self.keep) }
    }

    private func show(_ event: ObserverEvent) {
        events.append(event)
        if shown.contains(event.category) { visible.append(event) }
        guard events.count > Self.keep else { return }
        events.removeFirst(events.count - Self.keep)
        // ids rise with arrival, so whatever fell out of `events` is a prefix of `visible`
        let oldest = events[0].id
        visible.removeFirst(visible.firstIndex { $0.id >= oldest } ?? visible.count)
    }

    /// When a frame without its own time arrived (turns, approvals), as the audit log writes times.
    static func now() -> String {
        ObserverEvent.iso.string(from: Date())  // UTC, internet date-time: the audit log's form
    }

    private func id() -> Int {
        nextID += 1
        return nextID
    }
}
