import AuraKit
import SwiftUI

/// The Models window (docs/models-window.md): every model setting, local and cloud, in one place,
/// opened from the menu-bar menu and the panel's ⋯ menu. Roles, Local, Cloud and Model settings
/// each hold their own controls; Settings → Models only opens this window.
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
}

/// Which page the Models window shows; the app changes it to open the window at a page.
@MainActor @Observable
public final class ModelsNavigation {
    public var page: ModelsPage = .roles
    /// The model the Model settings page should show next (the chat box's "Model settings…"). The
    /// page takes it and clears it, so asking for the same model again still works.
    public var settingsModel: ModelChoice?
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
                ScrollView {
                    ModelsPageContent(page: navigation.page, catalog: store.modelCatalog, store: store, navigation: navigation)
                }
            }
        }
        .task { await store.loadModelCatalog() }
    }
}

/// A page: its title and summary, then its controls. (Outside the scroll view, so snapshot tests can draw it.)
struct ModelsPageContent: View {
    let page: ModelsPage
    let catalog: ModelCatalog?
    let store: ChatStore
    var navigation = ModelsNavigation()

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(page.title, systemImage: page.symbol).font(.title2.weight(.semibold))
            Text(page.summary).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let catalog {
                switch page {
                case .roles: RoleChoices(store: store, catalog: catalog)
                case .local: LocalModelsPage(store: store, catalog: catalog)
                case .cloud: CloudModelsPage(store: store, catalog: catalog)
                case .settings: ModelSettingsPage(store: store, catalog: catalog, navigation: navigation)
                }
            } else {
                Text(store.transport == nil ? "Aura is offline." : "Loading model settings…").foregroundStyle(.secondary)
            }
        }
        .padding(24)
        .frame(maxWidth: 640, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A warning under a control: the same small orange line everywhere in the Models window.
struct WarningLabel: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Label(text, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
    }
}

/// Opens the Models window, at a page or where it was. Provided by the app.
public struct OpenModelsAction: Sendable {
    private let handler: @MainActor @Sendable (ModelsPage?, ModelChoice?) -> Void

    public init(_ handler: @escaping @MainActor @Sendable (ModelsPage?, ModelChoice?) -> Void) {
        self.handler = handler
    }

    /// `model`: the model the Model settings page should show.
    @MainActor public func callAsFunction(_ page: ModelsPage? = nil, model: ModelChoice? = nil) { handler(page, model) }
}

public extension EnvironmentValues {
    /// Provided by the app.
    @Entry var openModels = OpenModelsAction { _, _ in }
}
