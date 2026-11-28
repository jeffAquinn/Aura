import AuraKit
import SwiftUI

/// The Models window (docs/models-window.md): every model setting, local and cloud, in one place.
///
/// Step 1 of the plan: the window and its pages, opened from the menu-bar menu and the panel's ⋯
/// menu. Nothing has moved yet: each page says what will move to it, and the controls stay in
/// Settings → Models until their step. Roles and Cloud show the current setup, read-only, so it's
/// plain that nothing was lost (the Anthropic connection, its key and its models included).
public enum ModelsPage: String, CaseIterable, Identifiable, Sendable {
    case roles, local, cloud, settings

    public var id: String { rawValue }

    var title: String {
        switch self {
        case .roles: "Roles"
        case .local: "Local"
        case .cloud: "Cloud"
        case .settings: "Model settings"
        }
    }

    var symbol: String {
        switch self {
        case .roles: "arrow.triangle.branch"
        case .local: "desktopcomputer"
        case .cloud: "cloud"
        case .settings: "slider.horizontal.3"
        }
    }

    var summary: String {
        switch self {
        case .roles: "The model each kind of request uses (Startup, Chat, Web Search, Deep Research) and the Developer for coding."
        case .local: "The models on this Mac (Ollama and llama.cpp), what is loaded, memory in use, Sleep Mode, and where to find more."
        case .cloud: "Cloud providers: the Anthropic key and the Claude models your account can use."
        case .settings: "Each model's own settings: sampling, thinking, context budget and reply length, with recommended values."
        }
    }

    /// The plan's step that moves this page's controls here (§12).
    var step: Int {
        switch self {
        case .roles: 2
        case .local: 3
        case .cloud: 4
        case .settings: 6
        }
    }
}

/// Which page the Models window shows; the app changes it to open the window at a page.
@MainActor @Observable
public final class ModelsNavigation {
    public var page: ModelsPage = .roles
    public init() {}
}

public struct ModelsView: View {
    let store: ChatStore
    @Bindable var navigation: ModelsNavigation

    public init(store: ChatStore, navigation: ModelsNavigation) {
        self.store = store
        self.navigation = navigation
    }

    public var body: some View {
        AuraThemedPage {
            NavigationSplitView {
                List(ModelsPage.allCases, selection: Binding(get: { navigation.page }, set: { navigation.page = $0 ?? .roles })) { page in
                    Label(page.title, systemImage: page.symbol).tag(page)
                }
                .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 240)
            } detail: {
                ModelsPageView(page: navigation.page, catalog: store.modelCatalog)
            }
        }
        .task { await store.loadSettings() }  // the catalog: roles and the cloud connection, read-only here
    }
}

/// One page, scrolling.
struct ModelsPageView: View {
    let page: ModelsPage
    let catalog: ModelCatalog?

    var body: some View {
        ScrollView { ModelsPageContent(page: page, catalog: catalog) }
    }
}

/// A page's content. In step 1 it explains what moves here and, for Roles and Cloud, shows
/// today's setup. (Separate from the scroll view so snapshot tests can draw it.)
struct ModelsPageContent: View {
    let page: ModelsPage
    let catalog: ModelCatalog?

    var body: some View {
            VStack(alignment: .leading, spacing: 14) {
                Label(page.title, systemImage: page.symbol).font(.title2.weight(.semibold))
                Text(page.summary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                current
                Label("These controls move here in step \(page.step) of the plan. Until then, change them in Settings → Models"
                      + (page == .cloud ? " and Settings → Keys." : "."), systemImage: "info.circle")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .padding(10)
                    .background(.quaternary.opacity(0.5), in: .rect(cornerRadius: 10))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(24)
            .frame(maxWidth: 640, alignment: .leading)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private var current: some View {
        if let catalog {
            switch page {
            case .roles:
                NowCard {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(["startup", "chat", "web_search", "deep_research"], id: \.self) { role in
                            if let choice = catalog.roles[role] {
                                LabeledContent(catalog.roleLabels[role] ?? role) {
                                    Text("\(choice.model) · \(Self.provider(choice.provider))").textSelection(.enabled)
                                }
                            }
                        }
                        LabeledContent("Coding") { Text(catalog.codingEngine == "codex" ? "Codex" : "Claude Code") }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            case .cloud:
                NowCard {
                    VStack(alignment: .leading, spacing: 6) {
                        LabeledContent("Anthropic") {
                            Label(catalog.anthropicKeySet ? "Connected: API key set" : "Not connected: no API key",
                                  systemImage: catalog.anthropicKeySet ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(catalog.anthropicKeySet ? Color.green : .secondary)
                        }
                        LabeledContent("Claude models") { Text("\(catalog.anthropicModels.count) available") }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            case .local, .settings:
                EmptyView()
            }
        }
    }

    static func provider(_ id: String) -> String {
        switch id {
        case "anthropic": "Anthropic cloud"
        case "llamacpp": "llama.cpp"
        default: "Ollama"
        }
    }
}

/// Today's setup on a page, in a quiet card (drawn by SwiftUI, so it renders everywhere).
struct NowCard<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Now").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            content
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: .rect(cornerRadius: 10))
    }
}

/// Opens the Models window, at a page or where it was. Provided by the app.
public struct OpenModelsAction: Sendable {
    private let handler: @MainActor @Sendable (ModelsPage?) -> Void

    public init(_ handler: @escaping @MainActor @Sendable (ModelsPage?) -> Void) {
        self.handler = handler
    }

    @MainActor public func callAsFunction(_ page: ModelsPage? = nil) { handler(page) }
}

public extension EnvironmentValues {
    /// Provided by the app.
    @Entry var openModels = OpenModelsAction { _ in }
}
