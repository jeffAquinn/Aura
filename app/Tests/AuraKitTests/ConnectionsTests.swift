import Foundation
import Testing
@testable import AuraKit

/// A fake server for Settings → Connections: signing in finishes after a couple of polls.
final class FakeConnections: AuraTransport, @unchecked Sendable {
    var state = "not_set_up"
    var polls = 0
    var saved: [ConnectionCredentials] = []
    var checks = 0
    var authURL = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x"

    func chat(_ text: String) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func approve(_ approvals: [Bool]) -> AsyncThrowingStream<AuraEvent, Error> { AsyncThrowingStream { $0.finish() } }
    func newConversation() async throws {}
    func warmup() async throws {}
    func health() async throws -> Health { throw URLError(.badServerResponse) }

    private var info: ConnectionInfo {
        ConnectionInfo(id: "google", label: "Google", state: state, configured: state != "not_set_up",
                       account: state == "connected" ? "me@gmail.com" : nil,
                       services: [ConnectionService(id: "gmail", label: "Gmail", purpose: "", granted: state == "connected")])
    }

    func connections() async throws -> [ConnectionInfo] {
        if state == "signing_in" { polls += 1; if polls >= 2 { state = "connected" } }
        return [info]
    }
    func saveConnectionCredentials(_ id: String, _ credentials: ConnectionCredentials) async throws -> ConnectionInfo {
        guard credentials.clientSecret.hasPrefix("GOCSPX-") else {
            throw AuraClientError.http(status: 422, message: "That doesn't look like a Google OAuth client secret (it should start with 'GOCSPX-').")
        }
        saved.append(credentials)
        state = "ready"
        return info
    }
    func connect(_ id: String, services: [String]) async throws -> SignInStart {
        state = "signing_in"
        return try JSONDecoder().decode(SignInStart.self, from: Data(#"{"authUrl": "\#(authURL)"}"#.utf8))
    }
    func checkConnection(_ id: String) async throws -> ConnectionInfo { checks += 1; return info }
    func disconnect(_ id: String) async throws -> ConnectionInfo { state = "ready"; return info }
}

@MainActor
struct ConnectionsTests {
    @Test func decodesTheServersStatusWithoutSecrets() throws {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        let list = try d.decode([ConnectionInfo].self, from: Data("""
        [{"id": "google", "label": "Google", "state": "connected", "configured": true, "account": "me@gmail.com",
          "services": [{"id": "gmail", "label": "Gmail", "purpose": "Read and search your mail...", "granted": true},
                       {"id": "drive", "label": "Drive", "purpose": "...", "granted": false}],
          "credentials": [{"name": "google_client_id", "label": "Google OAuth client ID", "set": true,
                           "hint": "123456…apps.googleusercontent.com"},
                          {"name": "google_client_secret", "label": "Google OAuth client secret", "set": true, "hint": "GOCSPX-…uvwx"}],
          "problem": null, "checks": [{"service": "gmail", "ok": true, "detail": "Working.", "at": 1795000000}]}]
        """.utf8))
        let google = try #require(list.first)
        #expect(google.isConnected && google.summary == "Connected as me@gmail.com")
        #expect(google.services.map(\.granted) == [true, false] && google.checks.first?.ok == true)
        #expect(google.credentials.map(\.hint) == ["123456…apps.googleusercontent.com", "GOCSPX-…uvwx"])
        let body = try JSONSerialization.jsonObject(with: JSONEncoder().encode(ConnectionCredentials(clientId: "a", clientSecret: "b")))
        #expect((body as? [String: String]) == ["client_id": "a", "client_secret": "b"])  // the server's field names
    }

    @Test func signingInOpensTheBrowserFollowsItAndChecks() async throws {
        let fake = FakeConnections()
        var opened: [URL] = []
        let store = ConnectionsStore(transport: { fake }, openURL: { opened.append($0) })
        await store.refresh()
        #expect(store.selected?.state == "not_set_up")

        let refused = await store.saveCredentials("google", clientId: "id.apps.googleusercontent.com", clientSecret: "tvly-oops")
        #expect(refused?.contains("GOCSPX-") == true && fake.saved.isEmpty)
        #expect(await store.saveCredentials("google", clientId: " id.apps.googleusercontent.com\n", clientSecret: "GOCSPX-abc") == nil)
        #expect(fake.saved.first?.clientId == "id.apps.googleusercontent.com")  // trimmed
        #expect(store.selected?.state == "ready")

        #expect(await store.signIn("google", services: ["gmail"]) == nil)
        #expect(opened.map(\.host) == ["accounts.google.com"])
        for _ in 0..<50 where fake.checks == 0 { try await Task.sleep(for: .milliseconds(100)) }
        #expect(store.selected?.isConnected == true && fake.checks == 1)  // checked as soon as it connected
    }

    @Test func onlyAnHttpsSignInPageIsEverOpened() async {
        let fake = FakeConnections()
        fake.state = "ready"
        fake.authURL = "file:///etc/passwd"
        var opened: [URL] = []
        let store = ConnectionsStore(transport: { fake }, openURL: { opened.append($0) })
        #expect(await store.signIn("google", services: ["gmail"]) == "The sign-in address was invalid.")
        #expect(opened.isEmpty)
    }
}
