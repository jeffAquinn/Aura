import AppKit
import AuraKit
import SwiftUI

/// The Models window's Local page (step 3 of docs/models-window.md): the models on this Mac and
/// what is loaded, memory in use, Sleep Mode and installing an Ollama model (both moved here from
/// Settings unchanged), and where to find more models. It refreshes every 5 seconds while shown,
/// since models load and unload on their own.
struct LocalModelsPage: View {
    let store: ChatStore
    let catalog: ModelCatalog
    @StateObject private var usage = AuraRuntimeUsage()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            ModelsCard("Ollama", symbol: "shippingbox") {
                if let state = store.localModels {
                    if !state.ollama.answering {
                        Label("Ollama isn't answering. Start the Ollama app to use its models.", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                    } else if state.ollama.models.isEmpty {
                        Text("No Ollama models installed yet.").foregroundStyle(.secondary)
                    } else {
                        ForEach(state.ollama.models) { LocalModelRow(model: $0) }
                    }
                } else {
                    Text("Checking…").foregroundStyle(.secondary)
                }
                Divider()
                InstallOllamaModel(store: store)
            }
            ModelsCard("llama.cpp", symbol: "cpu") {
                if let llama = store.localModels?.llamacpp {
                    if !llama.installed {
                        Label("llama.cpp isn't installed. Install it with brew install llama.cpp.", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                    }
                    if llama.models.isEmpty {
                        Text("No .gguf model files in the models folder yet.").foregroundStyle(.secondary)
                    } else {
                        ForEach(llama.models) { LocalModelRow(model: $0) }
                    }
                    HStack {
                        Text(llama.folder).font(.caption.monospaced()).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Button("Show in Finder") { NSWorkspace.shared.open(URL(fileURLWithPath: llama.folder)) }
                            .controlSize(.small)
                    }
                    Text(llama.server.running ? "Aura's llama-server is running \(llama.server.model ?? "a model")."
                         : "llama-server isn't running: Aura starts it when a llama.cpp model is picked.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            ModelsCard("Memory", symbol: "memorychip") {
                LabeledContent("In use on this Mac") {
                    Text("\(usage.memoryText) of \(ByteCountFormatter.string(fromByteCount: Int64(usage.memoryTotal), countStyle: .memory))")
                        .monospacedDigit()
                }
                Text("Loaded models take most of it. An idle model unloads after \(store.localModels?.idleUnload ?? "10m").")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .task { usage.start() }
            ModelsCard("Sleep Mode", symbol: "moon") {
                SleepModeControls(store: store, catalog: catalog)
            }
            ModelsCard("Find models", symbol: "magnifyingglass") {
                FindModelLink(title: "llama.cpp models (GGUF) on Hugging Face", url: "https://huggingface.co/models?library=gguf",
                              note: "Put the .gguf file in the models folder above; Aura lists it and starts llama-server for it when you pick it.")
                FindModelLink(title: "MLX models (MLX Community)", url: "https://huggingface.co/mlx-community",
                              note: "MLX runs models on Apple silicon's GPU. Ollama already uses MLX for many models; Aura doesn't run MLX files directly yet.")
                FindModelLink(title: "Ollama's most-downloaded models", url: "https://ollama.com/library?sort=popular",
                              note: "Install one by name with Install a model, above.")
            }
        }
        .task {
            while !Task.isCancelled {
                await store.loadLocalModels()
                try? await Task.sleep(for: .seconds(5))
            }
        }
    }
}

/// A section of a Models page: a titled card.
struct ModelsCard<Content: View>: View {
    let title: String
    let symbol: String
    @ViewBuilder let content: Content

    init(_ title: String, symbol: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.symbol = symbol
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(title, systemImage: symbol).font(.headline)
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: .rect(cornerRadius: 12))
    }
}

/// One local model: its name, size, and whether it is loaded now.
struct LocalModelRow: View {
    let model: LocalModels.Model

    var body: some View {
        HStack {
            Text(model.name).textSelection(.enabled)
            Spacer()
            if model.loaded {
                Label("Loaded", systemImage: "circle.fill").labelStyle(.titleAndIcon)
                    .font(.caption.weight(.medium)).foregroundStyle(.green)
            }
            if let gb = model.gb {
                Text(String(format: "%.1f GB", gb)).font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
        }
    }
}

struct FindModelLink: View {
    let title: String
    let url: String
    let note: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if let link = URL(string: url) { Link(title, destination: link) }
            Text(note).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// Sleep Mode, moved from Settings unchanged: after 10 idle minutes every model unloads; with Sleep
/// Mode on, this one Ollama model then stays loaded.
struct SleepModeControls: View {
    let store: ChatStore
    let catalog: ModelCatalog
    @State private var sleepMode = SleepMode(model: "")
    @State private var saving = false
    @State private var confirm = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Toggle("Sleep Mode", isOn: enabled)
                .disabled(saving || catalog.ollamaModels.isEmpty)
            if !catalog.ollamaModels.isEmpty {
                Picker("Keep this local model loaded", selection: $sleepMode.model) {
                    ForEach(catalog.ollamaModels, id: \.self) { Text($0).tag($0) }
                }
                .disabled(saving)
                .onChange(of: sleepMode.model) { _, _ in
                    if sleepMode.enabled { save() }
                }
            }
            Text("After 10 minutes with nothing happening, Aura unloads every model to free your Mac's memory. With Sleep Mode on, it then loads this one local model and keeps it ready, so your next message answers quickly; it can use several GB of memory and stays loaded after Aura closes. With Sleep Mode off, nothing stays loaded.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let error {
                WarningLabel(error)
            }
        }
        .onAppear { sleepMode = catalog.sleepMode }
        .onChange(of: catalog.sleepMode) { _, current in sleepMode = current }
        .confirmationDialog("Enable Sleep Mode?", isPresented: $confirm, titleVisibility: .visible) {
            Button("Keep \(sleepMode.model) Loaded") {
                sleepMode.enabled = true
                save()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("After 10 idle minutes, Aura unloads every other model and keeps this one loaded in Ollama until you use Aura again. It may make a smaller Mac feel less responsive.")
        }
    }

    private var enabled: Binding<Bool> {
        Binding(
            get: { sleepMode.enabled },
            set: { on in
                if on { confirm = true } else {
                    sleepMode.enabled = false
                    save()
                }
            }
        )
    }

    private func save() {
        saving = true
        error = nil
        Task {
            error = await store.updateSleepMode(sleepMode)
            saving = false
        }
    }
}

/// Installing an Ollama model by name, moved from Settings unchanged (with its confirmation).
struct InstallOllamaModel: View {
    let store: ChatStore
    @State private var name = ""
    @State private var confirm = false
    @State private var downloading = false
    @State private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Install a model").font(.callout.weight(.medium))
            HStack {
                TextField("Model name, e.g. qwen3:8b", text: $name)
                    .textFieldStyle(.roundedBorder)
                    .disabled(downloading)
                Button(downloading ? "Downloading…" : "Download") { confirm = true }
                    .disabled(downloading || trimmed.isEmpty)
                if downloading { ProgressView().controlSize(.small) }
            }
            Text("Downloads use your local Ollama daemon and its configured model folder, not Aura_Vault. Models can use several GB and may download from the internet. Only download a model name and source you trust.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let error {
                WarningLabel(error)
            }
        }
        .confirmationDialog("Download local model?", isPresented: $confirm, titleVisibility: .visible) {
            Button("Download \(trimmed)") { download() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Aura will ask Ollama to download this named model. The model is stored by Ollama outside Aura_Vault and can consume several GB of disk space.")
        }
    }

    private var trimmed: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    private func download() {
        downloading = true
        error = nil
        let requested = trimmed
        Task {
            error = await store.pullLocalModel(requested)
            if error == nil { name = "" }
            downloading = false
            await store.loadLocalModels()
        }
    }
}
