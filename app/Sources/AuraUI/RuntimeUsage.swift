import Combine
import Darwin
import Foundation

/// Small, local-only telemetry for the Chat header: CPU time and the on-disk footprint of Aura
/// itself, and the Mac's memory in use (which includes any loaded model, Aura's biggest cost).
@MainActor
final class AuraRuntimeUsage: ObservableObject {
    @Published private(set) var cpuPercent = 0.0
    @Published private(set) var storageBytes: Int64 = 0
    @Published private(set) var memoryUsed: UInt64 = 0
    let memoryTotal = ProcessInfo.processInfo.physicalMemory

    private var timer: Timer?
    private var previousCPUSeconds: Double?
    private var previousSample = Date()
    private var lastStorageSample = Date.distantPast

    func start() {
        guard timer == nil else { return }
        sample()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.sample() }
        }
    }

    var cpuText: String { String(format: "%.1f%%", cpuPercent) }
    var storageText: String { ByteCountFormatter.string(fromByteCount: storageBytes, countStyle: .file) }
    var memoryText: String { String(format: "%.1f GB", Double(memoryUsed) / 1_073_741_824) }
    var memoryDetail: String {
        "Memory in use on this Mac (Activity Monitor's Memory Used), including any loaded model: "
            + "\(ByteCountFormatter.string(fromByteCount: Int64(memoryUsed), countStyle: .memory)) of "
            + "\(ByteCountFormatter.string(fromByteCount: Int64(memoryTotal), countStyle: .memory))."
    }

    private func sample() {
        let now = Date()
        if let cpu = cpuSeconds(), let previous = previousCPUSeconds {
            let elapsed = now.timeIntervalSince(previousSample)
            if elapsed > 0 { cpuPercent = max(0, (cpu - previous) / elapsed * 100) }
            previousCPUSeconds = cpu
        } else {
            previousCPUSeconds = cpuSeconds()
        }
        previousSample = now
        if let used = Self.memoryInUse() { memoryUsed = used }

        // This is deliberately infrequent: storage is useful context, not a reason to wake the
        // disk or repeatedly traverse Aura's data folders while someone is typing.
        if now.timeIntervalSince(lastStorageSample) >= 30 {
            storageBytes = AuraRuntimeUsage.storageBytes()
            lastStorageSample = now
        }
    }

    private func cpuSeconds() -> Double? {
        var usage = rusage()
        guard getrusage(RUSAGE_SELF, &usage) == 0 else { return nil }
        func seconds(_ value: timeval) -> Double {
            Double(value.tv_sec) + Double(value.tv_usec) / 1_000_000
        }
        return seconds(usage.ru_utime) + seconds(usage.ru_stime)
    }

    /// As Activity Monitor counts "Memory Used": app memory (anonymous pages that can't simply be
    /// purged) plus wired and compressed memory.
    static func memoryInUse() -> UInt64? {
        var stats = vm_statistics64()
        var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &stats) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                host_statistics64(mach_host_self(), HOST_VM_INFO64, $0, &count)
            }
        }
        guard result == KERN_SUCCESS else { return nil }
        let pages = UInt64(stats.internal_page_count) - min(UInt64(stats.internal_page_count), UInt64(stats.purgeable_count))
            + UInt64(stats.wire_count) + UInt64(stats.compressor_page_count)
        return pages * UInt64(getpagesize())
    }

    private static func storageBytes() -> Int64 {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let roots = [
            Bundle.main.bundleURL,
            home.appending(path: "Library/Application Support/Aura", directoryHint: .isDirectory),
            home.appending(path: "Library/Logs/Aura", directoryHint: .isDirectory),
        ]
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .fileAllocatedSizeKey, .totalFileAllocatedSizeKey]
        return roots.reduce(0) { total, root in
            guard let files = FileManager.default.enumerator(at: root, includingPropertiesForKeys: Array(keys),
                                                              options: [.skipsHiddenFiles])
            else { return total }
            var bytes = total
            for case let url as URL in files {
                guard let values = try? url.resourceValues(forKeys: keys), values.isRegularFile == true else { continue }
                bytes += Int64(values.totalFileAllocatedSize ?? values.fileAllocatedSize ?? 0)
            }
            return bytes
        }
    }
}
