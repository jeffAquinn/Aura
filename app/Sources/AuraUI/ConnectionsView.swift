import AppKit
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
                        ConnectionRow(icon: ProviderGuide.of(c.id).icon, title: c.label, subtitle: c.summary,
                                      connected: c.isConnected)  // fmt: skip
                            .tag(Optional(c.id))
                    }
                    if !store.connections.contains(where: { $0.id == "discord" }) {
                        ConnectionRow(icon: ProviderGuide.of("discord").icon, title: "Discord", subtitle: "Coming next",
                                      connected: false)  // fmt: skip
                            .foregroundStyle(.secondary).selectionDisabled()
                    }
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 200, ideal: 220)
        } detail: {
            if let c = store.selected {
                ConnectionSetupView(store: store, connection: c).id(c.id)
            } else {
                ContentUnavailableView("Choose a connection", systemImage: "link")
            }
        }
        .task { await store.refresh() }
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

// MARK: - per-service setup guides

/// What differs between services: the guide, the credentials it needs, the sign-in button.
struct ProviderGuide {
    struct Field {
        let placeholder: String
        let secure: Bool
    }

    struct StepInfo {
        let text: String
        var links: [(String, String)] = []
        var copy: (label: String, value: String)?
    }

    let icon: String
    let subtitle: String
    let fields: [Field]  // client ID, then client secret when there is one
    let signIn: String
    let steps: (ConnectionInfo) -> [StepInfo]

    static func of(_ id: String) -> ProviderGuide {
        switch id {
        case "google": google
        case "slack": slack
        default: ProviderGuide(icon: "bubble.left.and.bubble.right", subtitle: "", fields: [], signIn: "Sign In…", steps: { _ in [] })
        }
    }

    static var google: ProviderGuide { ProviderGuide(
        icon: "envelope.badge",
        subtitle: "Gmail, Calendar and Drive, through your own Google Cloud client.",
        fields: [Field(placeholder: "Client ID (ends with .apps.googleusercontent.com)", secure: false),
                 Field(placeholder: "Client secret (starts with GOCSPX-)", secure: true)],
        signIn: "Sign In with Google…",
        steps: { _ in [
            StepInfo(text: "Create a Google Cloud project (name it “Aura”, Location: No organization).",
                     links: [("Create project", "https://console.cloud.google.com/projectcreate")]),
            StepInfo(text: "In that project, enable the APIs for the services you want.",
                     links: [("Gmail API", "https://console.cloud.google.com/apis/library/gmail.googleapis.com"),
                             ("Calendar API", "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"),
                             ("Drive API", "https://console.cloud.google.com/apis/library/drive.googleapis.com")]),
            StepInfo(text: "Google Auth Platform → Get started: app name “Aura”, your Gmail address as the support email. For Audience choose **Personal**: only your account can use it, with no verification and no weekly sign-in. (If Personal isn't offered, choose External, then add your Gmail under Audience → Test users; Google then asks you to sign in again every 7 days.)",
                     links: [("Google Auth Platform", "https://console.cloud.google.com/auth/overview")]),
            StepInfo(text: "Clients → Create client. Application type: **Desktop app** (not iOS or macOS: those ask for a Bundle ID and won't work with Aura's sign-in). Name it “Aura on this Mac”, then Create. Desktop app asks for nothing else: no Bundle ID, no redirect URI. Aura signs in through a one-time address on this Mac (http://127.0.0.1). Keep the dialog open: you'll copy the Client ID and Client secret below.",
                     links: [("Create client", "https://console.cloud.google.com/auth/clients/create")]),
        ] }) }

    static var slack: ProviderGuide { ProviderGuide(
        icon: "number.square",
        subtitle: "Your channels and messages, through your own Slack app, acting as you.",
        fields: [Field(placeholder: "Client ID (looks like 1234567890123.1234567890123)", secure: false)],
        signIn: "Sign In with Slack…",
        steps: { c in [
            StepInfo(text: "Open Slack's app page → **Create New App** → **From a manifest** → choose your workspace → replace everything in the box with Aura's manifest → Next → **Create**.",
                     links: [("Create a Slack app", "https://api.slack.com/apps?new_app=1")],
                     copy: c.setup?.manifest.map { ("Copy Aura's Manifest", $0) }),
            StepInfo(text: "The manifest sets everything: the name, the permissions Aura may ask for, **PKCE** (so no client secret is needed), no token rotation, and Aura's return address. You don't need to type the address anywhere; it's here in case Slack asks.",
                     copy: c.setup?.redirectUrl.map { ("Copy \($0)", $0) }),
            StepInfo(text: "Keep the app **internal** to your workspace: don't distribute it. (Slack's AI features accept only internal or Marketplace apps.) If your workspace requires admins to approve apps, approve it as the owner."),
            StepInfo(text: "On the app's **Basic Information** page, under App Credentials, copy the **Client ID** into the field below. Aura doesn't need the Client Secret."),
        ] }) }
}

// MARK: - the setup page

/// One service: the guide, the Keychain fields, the services and the sign-in, then the status.
struct ConnectionSetupView: View {
    let store: ConnectionsStore
    let connection: ConnectionInfo
    @State private var values = ["", ""]
    @State private var replacing = false
    @State private var chosen: Set<String>?
    @State private var error: String?
    @State private var confirmRemove = false

    private var guide: ProviderGuide { .of(connection.id) }
    private var selected: Set<String> { chosen ?? Set(connection.services.map(\.id)) }

    var body: some View {
        Scrolling {
            VStack(alignment: .leading, spacing: 16) {
                header
                if let problem = connection.problem {
                    Label(problem, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let e = error ?? store.lastError {
                    Label(e, systemImage: "xmark.octagon.fill").foregroundStyle(.red).fixedSize(horizontal: false, vertical: true)
                }
                if connection.isConnected { status }
                steps
                credentials
                signIn
                SecurityNote(service: connection.label)
            }
            .padding(24)
            .frame(maxWidth: 720, alignment: .leading)
        }
        .confirmationDialog("Remove your \(connection.label) app's details from the Keychain? Aura is also signed out of \(connection.label).",
                            isPresented: $confirmRemove) {  // fmt: skip
            Button("Remove", role: .destructive) { Task { await store.removeCredentials(connection.id) } }
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: guide.icon).font(.largeTitle).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(connection.label).font(.title2.weight(.semibold))
                Text(guide.subtitle).foregroundStyle(.secondary)
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

    private var steps: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(Array(guide.steps(connection).enumerated()), id: \.offset) { i, step in
                    Step(n: i + 1, text: step.text, links: step.links, copy: step.copy)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("1. Create your \(connection.label) app (once, a few minutes)", systemImage: "list.number").font(.headline)
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
                    ForEach(Array(guide.fields.enumerated()), id: \.offset) { i, field in
                        if field.secure {
                            SecureField(field.placeholder, text: $values[i]).textFieldStyle(.roundedBorder)
                        } else {
                            TextField(field.placeholder, text: $values[i]).textFieldStyle(.roundedBorder)
                        }
                    }
                    HStack {
                        Spacer()
                        if replacing { Button("Cancel") { reset() } }
                        Button("Save to Keychain") { save() }
                            .disabled(values.prefix(guide.fields.count).contains(where: \.isEmpty) || store.busy)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("2. Save it to the Keychain", systemImage: "key").font(.headline)
        }
    }

    private var signIn: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(connection.services) { s in
                    Toggle(isOn: Binding(get: { selected.contains(s.id) },
                                         set: { on in var set = selected; if on { set.insert(s.id) } else { set.remove(s.id) }; chosen = set })) {  // fmt: skip
                        VStack(alignment: .leading, spacing: 1) {
                            Text(s.label)
                            Text(s.purpose).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Text("Aura asks \(connection.label) only for these. Nothing is used until the Permissions screen lets you decide what Aura may do, and anything that writes or sends will always ask you first.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack {
                    if connection.isSigningIn {
                        ProgressView().controlSize(.small)
                        Text("Finish signing in in your browser…").foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(connection.isConnected ? "Sign In Again…" : guide.signIn) {
                        Task {
                            error = await store.signIn(connection.id,
                                                       services: connection.services.map(\.id).filter(selected.contains))  // fmt: skip
                        }
                    }
                    .disabled(!connection.configured || selected.isEmpty || connection.isSigningIn)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("3. Choose what to connect and sign in", systemImage: "person.badge.key").font(.headline)
        }
    }

    private func save() {
        Task {
            error = await store.saveCredentials(connection.id, clientId: values[0], clientSecret: values[1])
            if error == nil { reset() }
        }
    }

    private func reset() {
        values = ["", ""]  // nothing lingers in the view
        replacing = false
    }
}

/// One numbered setup step, with links that open in the browser and an optional copy button.
struct Step: View {
    let n: Int
    let text: String
    let links: [(String, String)]
    var copy: (label: String, value: String)?
    @State private var copied = false

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
                    if let copy {
                        Button(copied ? "Copied" : copy.label) {
                            Clipboard.copy(copy.value)
                            copied = true
                        }
                        .controlSize(.small)
                    }
                }
            }
        }
    }
}

struct SecurityNote: View {
    var service = "Google"

    var body: some View {
        Label {
            Text("Your app's details and your sign-in are kept in this Mac's Keychain: never in the project folder, a file, a log or the chat, so they can't reach git. Aura's AI and its tools can't read them. Disconnecting revokes Aura's access at \(service).")
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
                    ConnectionRow(icon: ProviderGuide.of(c.id).icon, title: c.label, subtitle: c.summary, connected: c.isConnected)
                    Button(c.state == "not_set_up" ? "Set Up…" : "Manage…") { openConnections(c.id) }.controlSize(.small)
                }
            }
            if connections.isEmpty {
                Text(store.transport == nil ? "Aura is offline." : "Loading…").foregroundStyle(.secondary)
            }
            if !connections.contains(where: { $0.id == "discord" }) {
                Text("Discord comes next.").font(.caption).foregroundStyle(.secondary)
            }
        }
        .task { connections = (try? await store.transport?.connections()) ?? [] }
    }
}
