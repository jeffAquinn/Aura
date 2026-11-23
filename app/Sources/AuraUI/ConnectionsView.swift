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
    let fields: [Field]  // client ID, then client secret when there is one (Discord: the bot token)
    let signIn: String?  // nil: no browser sign-in (Discord: the token is verified as it's saved)
    var save = "Save to Keychain"
    let steps: (ConnectionInfo) -> [StepInfo]

    static func of(_ id: String) -> ProviderGuide {
        switch id {
        case "google": google
        case "slack": slack
        default: discord
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

extension ProviderGuide {
    static var discord: ProviderGuide { ProviderGuide(
        icon: "bubble.left.and.bubble.right",
        subtitle: "Your servers, through your own private Discord bot.",
        fields: [Field(placeholder: "Bot token (three parts joined by dots)", secure: true)],
        signIn: nil,
        save: "Verify & Save to Keychain",
        steps: { c in [
            StepInfo(text: "Open the Discord Developer Portal → **New Application** → name it “Aura” → Create.",
                     links: [("Developer Portal", "https://discord.com/developers/applications")]),
            StepInfo(text: "Open the **Installation** page and set **Install Link** to **None**, then Save Changes. (A private bot can't keep Discord's default install link; Aura gives you its own invite link in step 3 below.)"),
            StepInfo(text: "Open the **Bot** page. Turn **off** “Public Bot” (so only you can add it to a server). Under Privileged Gateway Intents, turn **on** “Message Content Intent” (without it Discord hides message text from bots). Save Changes.",
                     links: c.setup?.portalUrl.map { [("This bot's page", $0)] } ?? []),
            StepInfo(text: "On the same page, under Token, click **Reset Token** and copy it. Discord shows it only once. Paste it below: Aura checks it with Discord before saving, and refuses a personal account's token (Discord forbids automating user accounts)."),
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
    @State private var liveValues = ["", "", "", ""]  // bot token, app-level token, refresh token, Client Secret
    @State private var replacingLive = false
    @State private var confirmRemoveLive = false

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
                if guide.signIn != nil { signIn } else { invite }
                if let live = connection.live, connection.isConnected {
                    LiveMessagesCard(store: store, connection: connection, live: live, values: $liveValues,
                                     replacing: $replacingLive, confirmRemove: $confirmRemoveLive)
                }
                SecurityNote(service: connection.label, revoke: guide.signIn != nil)
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
                        // A check's own explanation wins: it says why, and how to fix it.
                        let ok = check?.ok ?? s.granted
                        Image(systemName: check == nil && !s.granted ? "minus.circle" : ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(check == nil && !s.granted ? Color.secondary : ok ? .green : .red)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(s.label).font(.callout.weight(.medium))
                            Text(check?.detail ?? (s.granted ? "Connected" : "Not connected"))
                                .font(.caption).foregroundStyle(check?.ok == false ? .red : .secondary)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                            if let fix = check?.fix {
                                Link("\(check?.fixLabel ?? "Fix it") ↗", destination: fix).font(.caption.weight(.semibold))
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
            Label(connection.account.map { "Connected as \($0)" } ?? "Connected", systemImage: "person.crop.circle.badge.checkmark")
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
                        Button(guide.save) { save() }
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
                    Button(connection.isConnected ? "Sign In Again…" : guide.signIn ?? "Sign In…") {
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

    /// Discord: add the bot to a server with the invite link Aura builds (the least permissions it needs).
    private var invite: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                Text("The invite asks only for View Channels, Send Messages and Read Message History. Pick your server and Authorize. Aura can read only the servers and channels you invite the bot to, and sending will always ask you first.")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
                HStack {
                    if let link = ConnectionCheck.trusted(connection.setup?.inviteUrl) {
                        Link("Invite the bot to your server ↗", destination: link)
                    } else {
                        Text("Save the bot token first.").foregroundStyle(.secondary)
                    }
                    Spacer()
                    if connection.isConnected {
                        Button("Check Connection") { Task { await store.check(connection.id) } }.disabled(store.busy)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label("3. Invite the bot to your server", systemImage: "person.badge.plus").font(.headline)
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

// MARK: - live messages

/// Whether Aura hears new messages as they arrive. Slack needs the app's bot (two tokens);
/// Discord's bot already has everything.
struct LiveMessagesCard: View {
    let store: ConnectionsStore
    let connection: ConnectionInfo
    let live: LiveInfo
    @Binding var values: [String]
    @Binding var replacing: Bool
    @Binding var confirmRemove: Bool
    /// Why Slack refused the tokens: shown here, by the button (the page's top is out of sight).
    @State var refusal: String?
    @State private var saving = false

    private var isSlack: Bool { connection.id == "slack" }

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                Text(isSlack
                     ? "So Aura notices new messages as they arrive (DMs to Aura, @Aura mentions, and the channels you invite it to), your Slack app gets an **Aura** bot that only listens. Each message appears in Aura's chat and as a Mac notification. Aura won't answer in Slack by itself, and nobody there can make it do anything."
                     : "Aura hears DMs to your bot, @mentions, and every channel the bot can see. Each message appears in Aura's chat and as a Mac notification. Aura won't answer in Discord by itself, and nobody there can make it do anything.")
                    .font(.callout).fixedSize(horizontal: false, vertical: true)
                if live.configured || !isSlack { statusRow }
                if isSlack && (!live.configured || replacing) { setup } else if isSlack { saved }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        } title: {
            Label(isSlack ? "4. Live messages (optional)" : "4. Live messages", systemImage: "bell.badge").font(.headline)
        }
        .confirmationDialog("Stop live messages and remove the bot's tokens from the Keychain?", isPresented: $confirmRemove) {
            Button("Remove", role: .destructive) { Task { await store.removeLive(connection.id) } }
        }
    }

    private var statusRow: some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: live.isListening ? "dot.radiowaves.left.and.right" : live.state == "problem" ? "xmark.circle.fill" : "pause.circle")
                .foregroundStyle(live.isListening ? Color.green : live.state == "problem" ? .red : .secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(live.summary).font(.callout.weight(.medium))
                if !live.detail.isEmpty {
                    Text(live.detail).font(.caption).foregroundStyle(live.state == "problem" ? .red : .secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
            if live.state == "problem" || live.state == "off" {
                Button("Try Again") { Task { await store.check(connection.id) } }.controlSize(.small).disabled(store.busy)
            }
        }
    }

    private var steps: [ProviderGuide.StepInfo] {
        [
            ProviderGuide.StepInfo(text: "Open the Slack app whose Client ID you saved above, and use that same app for every step here (if you made more than one “Aura” app, the tokens must all come from this one). Open **App Manifest**, replace everything with Aura's manifest, and **Save Changes**. It adds the Aura bot, **Socket Mode** (no web address needed) and the message events. Nothing you've set up changes.",
                                   links: [("Your Slack apps", "https://api.slack.com/apps")],
                                   copy: connection.setup?.manifest.map { ("Copy Aura's Manifest", $0) }),
            ProviderGuide.StepInfo(text: "Open **Install App** → **Reinstall to Workspace** → Allow. Copy the **Bot User OAuth Token** (it starts with xoxb-)."),
            ProviderGuide.StepInfo(text: "Open **Basic Information** → scroll to **App-Level Tokens** → **Generate Token and Scopes**. Name it “Aura live”, click **Add Scope** → **connections:write**, then **Generate**. Slack shows an **Access Token** (xoxe.xapp-1-…) and, because token rotation is on in your app, a **Refresh Token** (xoxe-1-…). Copy both now: Slack shows them only once. (If your app shows a single token starting with xapp-, that's all you need.)"),
            ProviderGuide.StepInfo(text: "On the same Basic Information page, under **App Credentials**, click **Show** next to **Client Secret** and copy it. Aura uses it to renew the Access Token every 12 hours."),
            ProviderGuide.StepInfo(text: "Paste them below. Aura checks them with Slack before it saves them to the Keychain."),
            ProviderGuide.StepInfo(text: "In Slack, invite the bot to each channel you want Aura to hear: type **/invite @Aura** in #aura-chat. Direct messages to Aura need no invite."),
        ]
    }

    @ViewBuilder private var setup: some View {
        ForEach(Array(steps.enumerated()), id: \.offset) { i, step in
            Step(n: i + 1, text: step.text, links: step.links, copy: step.copy)
        }
        SecureField("Bot token (starts with xoxb-)", text: $values[0]).textFieldStyle(.roundedBorder)
        SecureField("Access Token from App-Level Tokens (xoxe.xapp-1-… or xapp-1-…)", text: $values[1]).textFieldStyle(.roundedBorder)
        if rotates {
            Text("This Access Token rotates: it lasts 12 hours, so Aura renews it with the Refresh Token shown with it and your app's Client Secret.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            SecureField("Refresh Token (xoxe-1-…), shown with the Access Token", text: $values[2]).textFieldStyle(.roundedBorder)
            SecureField("Client Secret (Basic Information → App Credentials)", text: $values[3]).textFieldStyle(.roundedBorder)
        }
        if let refusal {
            Label(refusal, systemImage: "xmark.octagon.fill")
                .foregroundStyle(.red).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
        HStack {
            if saving {
                ProgressView().controlSize(.small)
                Text("Checking with Slack…").foregroundStyle(.secondary)
            }
            Spacer()
            if replacing { Button("Cancel") { reset() } }
            Button("Verify & Save to Keychain") {
                saving = true
                refusal = nil
                Task {
                    refusal = await store.saveLive(connection.id, botToken: values[0], appToken: values[1],
                                                   refreshToken: values[2], clientSecret: values[3])
                    saving = false
                    if refusal == nil { reset() }
                }
            }
            .disabled(values.prefix(rotates ? 4 : 2).contains(where: \.isEmpty) || store.busy || saving)
        }
    }

    private var rotates: Bool { LiveTokens.rotates(values[1]) }

    @ViewBuilder private var saved: some View {
        ForEach(live.credentials.filter(\.set)) { c in
            LabeledContent(c.label) { Text(c.hint ?? "Not set").font(.callout.monospaced()) }
        }
        HStack {
            Text("Invite the bot with **/invite @Aura** in each channel Aura should hear.").font(.caption).foregroundStyle(.secondary)
            Spacer()
            Button("Remove…", role: .destructive) { confirmRemove = true }
            Button("Replace…") { replacing = true }
        }
        .controlSize(.small)
    }

    private func reset() {
        values = ["", "", "", ""]  // nothing lingers in the view
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
    var revoke = true

    var body: some View {
        Label {
            Text("Your app's details and your sign-in are kept in this Mac's Keychain: never in the project folder, a file, a log or the chat, so they can't reach git. Aura's AI and its tools can't read them. "
                 + (revoke ? "Disconnecting revokes Aura's access at \(service)."
                    : "Disconnecting forgets the token; Reset Token in the Developer Portal makes it useless to anyone."))
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
        }
        .task { connections = (try? await store.transport?.connections()) ?? [] }
    }
}
