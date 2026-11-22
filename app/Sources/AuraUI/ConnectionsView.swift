import AuraKit
import SwiftUI

/// Settings → Connections: Aura's links to the user's accounts, set up one at a time.
public struct ConnectionsView: View {
    let store: ConnectionsStore

    public init(store: ConnectionsStore) {
        self.store = store
    }

    public var body: some View {
        NavigationSplitView {
            List(selection: Binding(get: { store.selectedID }, set: { store.selectedID = $0 })) {
                Section("Connections") {
                    ForEach(store.connections) { c in
                        ConnectionRow(icon: ConnectionsView.icon(c.id), title: c.label, subtitle: c.summary,
                                      connected: c.isConnected)  // fmt: skip
                            .tag(Optional(c.id))
                    }
                    ConnectionRow(icon: "number.square", title: "Slack", subtitle: "Coming next", connected: false)
                        .foregroundStyle(.secondary).selectionDisabled()
                    ConnectionRow(icon: "bubble.left.and.bubble.right", title: "Discord", subtitle: "Coming next",
                                  connected: false)  // fmt: skip
                        .foregroundStyle(.secondary).selectionDisabled()
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 200, ideal: 220)
        } detail: {
            if let c = store.selected, c.id == "google" {
                GoogleSetupView(store: store, connection: c)
            } else {
                ContentUnavailableView("Choose a connection", systemImage: "link")
            }
        }
        .task { await store.refresh() }
    }

    static func icon(_ id: String) -> String {
        switch id {
        case "google": "envelope.badge"
        case "slack": "number.square"
        default: "bubble.left.and.bubble.right"
        }
    }
}

struct ConnectionRow: View {
    let icon: String
    let title: String
    let subtitle: String
    let connected: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: icon).frame(width: 20)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if connected { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green) }
        }
    }
}

/// Google: create the OAuth client (guided), save it to the Keychain, choose services, sign in.
struct GoogleSetupView: View {
    let store: ConnectionsStore
    let connection: ConnectionInfo
    @State private var clientId = ""
    @State private var clientSecret = ""
    @State private var replacing = false
    @State private var chosen: Set<String> = ["gmail", "calendar", "drive"]
    @State private var error: String?
    @State private var confirmRemove = false

    var body: some View {
        Scrolling {
            VStack(alignment: .leading, spacing: 16) {
                header
                if let problem = connection.problem {
                    Label(problem, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                }
                if let e = error ?? store.lastError {
                    Label(e, systemImage: "xmark.octagon.fill").foregroundStyle(.red)
                }
                if connection.isConnected { status }
                guide
                credentials
                signIn
                SecurityNote()
            }
            .padding(24)
            .frame(maxWidth: 720, alignment: .leading)
        }
        .confirmationDialog("Remove your Google client from the Keychain? Aura is also signed out of Google.",
                            isPresented: $confirmRemove) {  // fmt: skip
            Button("Remove", role: .destructive) { Task { await store.removeCredentials(connection.id) } }
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "envelope.badge").font(.largeTitle).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text("Google").font(.title2.weight(.semibold))
                Text("Gmail, Calendar and Drive, through your own Google Cloud client.").foregroundStyle(.secondary)
            }
            Spacer()
            Text(connection.summary)
                .font(.callout.weight(.medium))
                .padding(.horizontal, 10).padding(.vertical, 4)
                .background((connection.isConnected ? Color.green : Color.secondary).opacity(0.15), in: .capsule)
        }
    }

    private var status: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(connection.services) { s in
                    let check = connection.checks.first { $0.service == s.id }
                    HStack(alignment: .firstTextBaseline) {
                        Image(systemName: !s.granted ? "minus.circle" : check?.ok == false ? "xmark.circle.fill" : "checkmark.circle.fill")
                            .foregroundStyle(!s.granted ? Color.secondary : check?.ok == false ? .red : .green)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(s.label).font(.callout.weight(.medium))
                            Text(!s.granted ? "Not connected" : check?.detail ?? "Connected")
                                .font(.caption).foregroundStyle(check?.ok == false ? .red : .secondary)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                            if let fix = check?.fix {
                                Link("Enable it in Google Cloud ↗", destination: fix).font(.caption.weight(.semibold))
                            }
                        }
                    }
                }
                HStack {
                    Button("Check Connection") { Task { await store.check(connection.id) } }
                    Spacer()
                    Button("Disconnect", role: .destructive) { Task { await store.disconnect(connection.id) } }
                }
                .disabled(store.busy)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label(connection.account.map { "Signed in as \($0)" } ?? "Signed in", systemImage: "person.crop.circle.badge.checkmark")
                .font(.headline)
        }
    }

    private var guide: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                Step(n: 1, text: "Create a Google Cloud project (name it “Aura”).",
                     links: [("Create project", "https://console.cloud.google.com/projectcreate")])
                Step(n: 2, text: "In that project, enable the APIs for the services you want.",
                     links: [("Gmail API", "https://console.cloud.google.com/apis/library/gmail.googleapis.com"),
                             ("Calendar API", "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"),
                             ("Drive API", "https://console.cloud.google.com/apis/library/drive.googleapis.com")])
                Step(n: 3, text: "Google Auth Platform → Get started: app name “Aura”, your Gmail address as the support email. For Audience choose **Personal**: only your account can use it, with no verification and no weekly sign-in. (If Personal isn't offered, choose External, then add your Gmail under Audience → Test users; Google then asks you to sign in again every 7 days.)",
                     links: [("Google Auth Platform", "https://console.cloud.google.com/auth/overview")])
                Step(n: 4, text: "Clients → Create client. Application type: **Desktop app** (not iOS or macOS: those ask for a Bundle ID and won't work with Aura's sign-in). Name it “Aura on this Mac”, then Create. Desktop app asks for nothing else: no Bundle ID, no redirect URI. Aura signs in through a one-time address on this Mac (http://127.0.0.1). Keep the dialog open: you'll copy the Client ID and Client secret below.",
                     links: [("Create client", "https://console.cloud.google.com/auth/clients/create")])
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("1. Create your Google client (once, about 5 minutes)", systemImage: "list.number").font(.headline)
        }
    }

    @ViewBuilder private var credentials: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                if connection.configured && !replacing {
                    ForEach(connection.credentials) { c in
                        LabeledContent(c.label) { Text(c.hint ?? "Not set").font(.callout.monospaced()) }
                    }
                    HStack {
                        Spacer()
                        Button("Remove…", role: .destructive) { confirmRemove = true }
                        Button("Replace…") { replacing = true }
                    }
                    .controlSize(.small)
                } else {
                    TextField("Client ID (ends with .apps.googleusercontent.com)", text: $clientId)
                        .textFieldStyle(.roundedBorder)
                    SecureField("Client secret (starts with GOCSPX-)", text: $clientSecret)
                        .textFieldStyle(.roundedBorder)
                    HStack {
                        Spacer()
                        if replacing { Button("Cancel") { reset() } }
                        Button("Save to Keychain") { save() }
                            .disabled(clientId.isEmpty || clientSecret.isEmpty || store.busy)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("2. Save your client to the Keychain", systemImage: "key").font(.headline)
        }
    }

    private var signIn: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(connection.services) { s in
                    Toggle(isOn: Binding(get: { chosen.contains(s.id) },
                                         set: { on in if on { chosen.insert(s.id) } else { chosen.remove(s.id) } })) {  // fmt: skip
                        VStack(alignment: .leading, spacing: 1) {
                            Text(s.label)
                            Text(s.purpose).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Text("Aura asks Google only for these. Nothing is used until the Permissions screen lets you decide what Aura may do, and anything that writes (a draft, an event, a file) will always ask you first.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack {
                    if connection.isSigningIn {
                        ProgressView().controlSize(.small)
                        Text("Finish signing in in your browser…").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(connection.isConnected ? "Sign In Again…" : "Sign In with Google…") {
                        Task {
                            error = await store.signIn(connection.id,
                                                       services: connection.services.map(\.id).filter(chosen.contains))  // fmt: skip
                        }
                    }
                    .disabled(!connection.configured || chosen.isEmpty || connection.isSigningIn)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("3. Choose services and sign in", systemImage: "person.badge.key").font(.headline)
        }
    }

    private func save() {
        Task {
            error = await store.saveCredentials(connection.id, clientId: clientId, clientSecret: clientSecret)
            if error == nil { reset() }
        }
    }

    private func reset() {
        clientId = ""
        clientSecret = ""  // the secret doesn't linger in the view
        replacing = false
    }
}

/// One numbered setup step, with links that open in the browser.
struct Step: View {
    let n: Int
    let text: String
    let links: [(String, String)]

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("\(n)").font(.callout.weight(.bold)).frame(width: 20, height: 20)
                .background(Color.accentColor.opacity(0.15), in: .circle)
            VStack(alignment: .leading, spacing: 4) {
                Text(.init(text)).font(.callout).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 12) {
                    ForEach(links, id: \.1) { title, url in
                        if let u = URL(string: url) { Link("\(title) ↗", destination: u).font(.callout) }
                    }
                }
            }
        }
    }
}

struct SecurityNote: View {
    var body: some View {
        Label {
            Text("Your client secret and your sign-in are kept in this Mac's Keychain: never in the project folder, a file, a log or the chat, so they can't reach git. Aura's AI and its tools can't read them. Disconnecting revokes Aura's access at Google.")
                .fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: "lock.shield")
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }
}

// MARK: - opening the window from Settings

public struct OpenConnectionsAction: Sendable {
    private let handler: @MainActor @Sendable (String?) -> Void

    public init(_ handler: @escaping @MainActor @Sendable (String?) -> Void) {
        self.handler = handler
    }

    @MainActor public func callAsFunction(_ id: String?) { handler(id) }
}

public extension EnvironmentValues {
    /// Provided by the app.
    @Entry var openConnections = OpenConnectionsAction { _ in }
}

/// Settings → Connections: a line per account, and the window where each is set up.
struct ConnectionsSection: View {
    let store: ChatStore
    @State private var connections: [ConnectionInfo] = []
    @Environment(\.openConnections) private var openConnections

    var body: some View {
        Section("Connections") {
            ForEach(connections) { c in
                HStack {
                    ConnectionRow(icon: ConnectionsView.icon(c.id), title: c.label, subtitle: c.summary, connected: c.isConnected)
                    Button(c.state == "not_set_up" ? "Set Up…" : "Manage…") { openConnections(c.id) }.controlSize(.small)
                }
            }
            if connections.isEmpty {
                Text(store.transport == nil ? "Aura is offline." : "Loading…").foregroundStyle(.secondary)
            }
            Text("Slack and Discord come next.").font(.caption).foregroundStyle(.secondary)
        }
        .task { connections = (try? await store.transport?.connections()) ?? [] }
    }
}
