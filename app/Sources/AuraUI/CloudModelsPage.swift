import AuraKit
import SwiftUI

/// The Models window's Cloud page (step 4 of docs/models-window.md): the Anthropic connection. Its
/// API key row and Sync cloud models moved here from Settings unchanged; the key is the same
/// Keychain item as before, so a saved key keeps working. (Other cloud providers: not planned for
/// now, the user's decision on 2026-11-27.)
struct CloudModelsPage: View {
    let store: ChatStore
    let catalog: ModelCatalog
    @State private var syncing = false
    @State private var error: String?

    /// The keys shown here rather than in Settings → Keys.
    static let keys: Set<String> = ["anthropic_api_key"]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ModelsCard("Anthropic", symbol: "cloud") {
                Text("Choosing a Claude model for a role (on the Roles page) sends that role's prompts to Anthropic, and may incur API charges. Local models stay the default; nothing is preloaded at launch.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                ForEach(store.secrets.filter { Self.keys.contains($0.name) }) { KeyRow(secret: $0, store: store) }
                if store.secrets.isEmpty {
                    Text(store.transport == nil ? "Aura is offline." : "Loading…").foregroundStyle(.secondary)
                }
                Text("Stored in this Mac's Keychain, never in a file, a log or the chat.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ModelsCard("Claude models", symbol: "list.bullet") {
                HStack {
                    Button(syncing ? "Syncing cloud models…" : "Sync cloud models") { sync() }
                        .disabled(syncing || !catalog.anthropicKeySet)
                    if syncing { ProgressView().controlSize(.small) }
                    Spacer()
                    Text("\(catalog.anthropicModels.count) available").font(.caption).foregroundStyle(.secondary)
                }
                .controlSize(.small)
                if !catalog.anthropicKeySet {
                    Label("Add the Anthropic API key above before choosing Claude.", systemImage: "key.fill")
                        .font(.caption).foregroundStyle(.orange)
                }
                Text("Sync checks Anthropic with your saved API key and lists the cloud models available to that account. It never exposes the key to Aura's interface.")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if !catalog.anthropicModels.isEmpty {
                    Text(catalog.anthropicModels.joined(separator: " · "))
                        .font(.caption.monospaced()).foregroundStyle(.secondary)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                }
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange)
                }
            }
        }
        .task { await store.loadSecrets() }
    }

    private func sync() {
        syncing = true
        error = nil
        Task {
            error = await store.syncCloudModels()
            syncing = false
        }
    }
}
