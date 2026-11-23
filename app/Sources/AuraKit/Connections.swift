import Foundation
import Observation

// MARK: - Settings → Connections (Sprint 7): models mirroring /v1/connections

public struct ConnectionService: Codable, Sendable, Equatable, Identifiable {
    public let id: String  // gmail | calendar | drive
    public let label: String
    public let purpose: String
    public let granted: Bool

    public init(id: String, label: String, purpose: String, granted: Bool = false) {
        self.id = id
        self.label = label
        self.purpose = purpose
        self.granted = granted
    }
}

public struct ConnectionCheck: Codable, Sendable, Equatable {
    public let service: String
    public let ok: Bool
    public let detail: String
    public let fixUrl: String?  // the page that fixes it (enable an API, turn on an intent, invite a bot)
    public let fixLabel: String?

    public init(service: String, ok: Bool, detail: String, fixUrl: String? = nil, fixLabel: String? = nil) {
        self.service = service
        self.ok = ok
        self.detail = detail
        self.fixUrl = fixUrl
        self.fixLabel = fixLabel
    }

    /// Only ever the service's own console (Google Cloud, Discord's Developer Portal).
    public var fix: URL? { ConnectionCheck.trusted(fixUrl) }

    public static let trustedHosts: Set<String> = ["console.developers.google.com", "console.cloud.google.com", "discord.com"]

    public static func trusted(_ link: String?) -> URL? {
        guard let link, let url = URL(string: link), url.scheme == "https", trustedHosts.contains(url.host() ?? "") else {
            return nil
        }
        return url
    }
}

/// What a service's setup needs to show (Slack: Aura's app manifest and its return address).
public struct ConnectionSetup: Codable, Sendable, Equatable {
    public let redirectUrl: String?
    public let manifest: String?
    public let inviteUrl: String?  // Discord: add the bot to a server
    public let portalUrl: String?  // Discord: the bot's page in the Developer Portal

    public init(redirectUrl: String? = nil, manifest: String? = nil, inviteUrl: String? = nil, portalUrl: String? = nil) {
        self.redirectUrl = redirectUrl
        self.manifest = manifest
        self.inviteUrl = inviteUrl
        self.portalUrl = portalUrl
    }
}

/// Live messages: whether Aura hears new Slack or Discord messages as they arrive.
public struct LiveInfo: Codable, Sendable, Equatable {
    public let state: String  // off | connecting | listening | problem
    public let detail: String
    public let configured: Bool  // Slack: both tokens saved (Discord: connected)
    public let credentials: [SecretInfo]  // Slack's bot and app-level tokens, as masked hints

    public init(state: String = "off", detail: String = "", configured: Bool = false, credentials: [SecretInfo] = []) {
        self.state = state
        self.detail = detail
        self.configured = configured
        self.credentials = credentials
    }

    public var isListening: Bool { state == "listening" }

    public var summary: String {
        switch state {
        case "listening": "Listening"
        case "connecting": "Connecting…"
        case "problem": "Stopped"
        default: configured ? "Off" : "Not set up"
        }
    }
}

/// Slack's live-message tokens, sent once to the local server, which checks them with Slack and
/// keeps them in the Keychain.
public struct LiveTokens: Encodable, Sendable {
    public let botToken: String
    public let appToken: String
    public let refreshToken: String  // only for a rotating app-level token (xoxe.xapp-…)
    public let clientSecret: String

    public init(botToken: String, appToken: String, refreshToken: String = "", clientSecret: String = "") {
        self.botToken = botToken
        self.appToken = appToken
        self.refreshToken = refreshToken
        self.clientSecret = clientSecret
    }

    /// Token rotation is on for the app: its app-level token lasts 12 hours and Aura renews it.
    public static func rotates(_ appToken: String) -> Bool {
        appToken.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("xoxe.")
    }

    enum CodingKeys: String, CodingKey {
        case botToken = "bot_token"
        case appToken = "app_token"
        case refreshToken = "refresh_token"
        case clientSecret = "client_secret"
    }
}

public struct ConnectionInfo: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let label: String
    public let state: String  // not_set_up | ready | signing_in | connected
    public let configured: Bool
    public let account: String?
    public let services: [ConnectionService]
    public let credentials: [SecretInfo]  // masked hints only
    public let problem: String?
    public let checks: [ConnectionCheck]
    public let setup: ConnectionSetup?
    public let live: LiveInfo?  // Slack and Discord

    public init(id: String, label: String, state: String, configured: Bool = false, account: String? = nil,
                services: [ConnectionService] = [], credentials: [SecretInfo] = [], problem: String? = nil,
                checks: [ConnectionCheck] = [], setup: ConnectionSetup? = nil, live: LiveInfo? = nil) {
        self.id = id
        self.label = label
        self.state = state
        self.configured = configured
        self.account = account
        self.services = services
        self.credentials = credentials
        self.problem = problem
        self.checks = checks
        self.setup = setup
        self.live = live
    }

    public var isConnected: Bool { state == "connected" }
    public var isSigningIn: Bool { state == "signing_in" }

    /// One line for Settings and the sidebar.
    public var summary: String {
        switch state {
        case "connected": account.map { "Connected as \($0)" } ?? "Connected"
        case "signing_in": "Waiting for you in your browser…"
        case "ready": "Ready to sign in"
        default: "Not set up"
        }
    }
}

/// The user's OAuth client, sent once to the local server, which keeps it in the Keychain.
public struct ConnectionCredentials: Encodable, Sendable {
    public let clientId: String
    public let clientSecret: String

    public init(clientId: String, clientSecret: String) {
        self.clientId = clientId
        self.clientSecret = clientSecret
    }

    enum CodingKeys: String, CodingKey {
        case clientId = "client_id"
        case clientSecret = "client_secret"
    }
}

/// A started sign-in: the page to open in the user's browser.
public struct SignInStart: Decodable, Sendable {
    public let authUrl: String
}

// MARK: - state

@MainActor
@Observable
public final class ConnectionsStore {
    public private(set) var connections: [ConnectionInfo] = []
    public var selectedID: String? = "google"
    public private(set) var lastError: String?
    public private(set) var busy = false

    @ObservationIgnored private let transport: () -> (any AuraTransport)?
    @ObservationIgnored private let openURL: (URL) -> Void
    @ObservationIgnored private var watching: Task<Void, Never>?

    public init(transport: @escaping () -> (any AuraTransport)?, openURL: @escaping (URL) -> Void) {
        self.transport = transport
        self.openURL = openURL
    }

    /// For previews and snapshot tests.
    public init(connections: [ConnectionInfo], selectedID: String? = "google") {
        transport = { nil }
        openURL = { _ in }
        self.connections = connections
        self.selectedID = selectedID
    }

    public var selected: ConnectionInfo? { connections.first { $0.id == selectedID } }

    public func refresh() async {
        guard let t = transport() else { return }
        do {
            connections = try await t.connections()
            lastError = nil
        } catch {
            lastError = FriendlyMessage.describe(error)
        }
    }

    /// Returns why the server refused them (nil: saved to the Keychain).
    public func saveCredentials(_ id: String, clientId: String, clientSecret: String) async -> String? {
        let refused = await perform { try await $0.saveConnectionCredentials(id, ConnectionCredentials(
            clientId: clientId.trimmingCharacters(in: .whitespacesAndNewlines),
            clientSecret: clientSecret.trimmingCharacters(in: .whitespacesAndNewlines))) }
        if refused == nil, connections.first(where: { $0.id == id })?.isConnected == true {
            await check(id)  // connected on saving (Discord's bot token): show what works straight away
        }
        return refused
    }

    public func removeCredentials(_ id: String) async {
        lastError = await perform { try await $0.removeConnectionCredentials(id) }
    }

    /// Opens the service's sign-in page in the user's browser, then follows the sign-in until it
    /// finishes there (or times out on the server).
    public func signIn(_ id: String, services: [String]) async -> String? {
        guard let t = transport() else { return FriendlyMessage.offline }
        do {
            let start = try await t.connect(id, services: services)
            guard let url = URL(string: start.authUrl), url.scheme == "https" else { return "The sign-in address was invalid." }
            openURL(url)
            await refresh()
            follow(id)
            return nil
        } catch let AuraClientError.http(_, message) {
            return message
        } catch {
            return FriendlyMessage.describe(error)
        }
    }

    public func check(_ id: String) async {
        lastError = await perform { try await $0.checkConnection(id) }
    }

    /// Slack's live messages: returns why the tokens were refused (nil: checked and saved).
    public func saveLive(_ id: String, botToken: String, appToken: String, refreshToken: String = "",
                         clientSecret: String = "") async -> String? {
        let trim = { (s: String) in s.trimmingCharacters(in: .whitespacesAndNewlines) }
        let rotates = LiveTokens.rotates(appToken)  // a plain xapp- token sends nothing more
        let refused = await perform { try await $0.saveLiveTokens(id, LiveTokens(
            botToken: trim(botToken), appToken: trim(appToken),
            refreshToken: rotates ? trim(refreshToken) : "", clientSecret: rotates ? trim(clientSecret) : "")) }
        if refused == nil { followLive(id) }
        return refused
    }

    public func removeLive(_ id: String) async {
        lastError = await perform { try await $0.removeLiveTokens(id) }
    }

    /// Connecting takes a moment: refresh until the listener settles.
    private func followLive(_ id: String) {
        Task { [weak self] in
            for _ in 0..<15 {
                try? await Task.sleep(for: .seconds(1))
                guard let self else { return }
                await self.refresh()
                if self.connections.first(where: { $0.id == id })?.live?.state != "connecting" { return }
            }
        }
    }

    public func disconnect(_ id: String) async {
        lastError = await perform { try await $0.disconnect(id) }
    }

    private func follow(_ id: String) {
        watching?.cancel()
        watching = Task { [weak self] in
            for _ in 0..<400 {  // the server gives up after 5 minutes
                try? await Task.sleep(for: .seconds(1))
                guard let self, !Task.isCancelled else { return }
                await self.refresh()
                guard self.connections.first(where: { $0.id == id })?.isSigningIn == true else { break }
            }
            if let self, self.connections.first(where: { $0.id == id })?.isConnected == true {
                await self.check(id)  // prove it works straight away
            }
        }
    }

    private func perform(_ action: (any AuraTransport) async throws -> ConnectionInfo) async -> String? {
        guard let t = transport() else { return FriendlyMessage.offline }
        busy = true
        defer { busy = false }
        do {
            let updated = try await action(t)
            if let i = connections.firstIndex(where: { $0.id == updated.id }) { connections[i] = updated }
            else { connections.append(updated) }
            lastError = nil
            return nil
        } catch let AuraClientError.http(_, message) {
            return message
        } catch {
            return FriendlyMessage.describe(error)
        }
    }
}
