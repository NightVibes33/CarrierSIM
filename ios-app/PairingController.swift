import Foundation
import AirliftFFI

@MainActor
final class PairingController: ObservableObject {
    static let shared = PairingController()

    private let hostName = "CarrierSIM"
    private let hostModel = "Mac17,7"
    private let bindAddress = "0.0.0.0"
    private let localNetwork = LocalNetworkAuthorization()
    private let keepAlive = KeepAlive()

    private var netService: NetService?
    private var pairContinuation: CheckedContinuation<String, Error>?

    @Published private(set) var running = false
    @Published var status = "Not paired"
    @Published var pin: String?

    private static let altIRKKey = "carriersimPairingHostAltIRK"
    nonisolated private static var storedAltIRK: String {
        get { UserDefaults.standard.string(forKey: altIRKKey) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: altIRKKey) }
    }

    enum PairingError: LocalizedError {
        case emptyFile
        case failed(String)

        var errorDescription: String? {
            switch self {
            case .emptyFile: return "Pairing produced an empty pairing file."
            case .failed(let message): return message
            }
        }
    }

    static func pairingFilePath() -> String {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return docs.appendingPathComponent("carriersim_pairing.plist").path
    }

    static var hasPairingFile: Bool {
        let path = pairingFilePath()
        guard FileManager.default.fileExists(atPath: path) else { return false }
        let size = (try? FileManager.default.attributesOfItem(atPath: path)[.size] as? NSNumber)?.intValue ?? 0
        return size > 0
    }

    func startAndWait() async throws -> String {
        if running {
            cancel()
            try? await Task.sleep(nanoseconds: 100_000_000)
        }

        return try await withCheckedThrowingContinuation { continuation in
            pairContinuation = continuation
            start()
        }
    }

    func cancel() {
        stopAdvertising()
        keepAlive.stop()
        running = false
        pin = nil
        status = "Cancelled"
        if let continuation = pairContinuation {
            pairContinuation = nil
            continuation.resume(throwing: CancellationError())
        }
    }

    func deletePairingFile() {
        try? FileManager.default.removeItem(atPath: Self.pairingFilePath())
        status = "Not paired"
        pin = nil
    }

    private func start() {
        stopAdvertising()
        running = true
        pin = nil
        status = "Starting local pairing host…"

        Task {
            _ = await localNetwork.request()
            guard running else { return }

            keepAlive.start()
            status = "Open Settings › Privacy & Security › Developer Mode"
            runHost()
        }
    }

    private func runHost() {
        let bind = bindAddress
        let name = hostName
        let model = hostModel
        let outPath = Self.pairingFilePath()
        let altIRK = Self.storedAltIRK
        nonisolated(unsafe) let context = UnsafeMutableRawPointer(
            Unmanaged.passRetained(self).toOpaque()
        )

        DispatchQueue.global(qos: .userInitiated).async {
            var result = ALPairResult()
            let rc = bind.withCString { bindC in
                name.withCString { nameC in
                    model.withCString { modelC in
                        outPath.withCString { outC in
                            altIRK.withCString { irkC in
                                al_pairing_run_host(
                                    bindC,
                                    0,
                                    nameC,
                                    modelC,
                                    outC,
                                    irkC,
                                    carrierPairReadyCallback,
                                    carrierPairPinCallback,
                                    context,
                                    &result
                                )
                            }
                        }
                    }
                }
            }

            let outcome: Result<String, Error>
            if rc == 0 {
                let issuedIRK = cString(result.host_alt_irk_hex)
                if !issuedIRK.isEmpty { Self.storedAltIRK = issuedIRK }

                let returnedPath = cString(result.pairing_file_path)
                let finalPath = returnedPath.isEmpty ? outPath : returnedPath
                let size = (try? FileManager.default.attributesOfItem(atPath: finalPath)[.size] as? NSNumber)?.intValue ?? 0
                outcome = size > 0 ? .success(finalPath) : .failure(PairingError.emptyFile)
            } else {
                let message = cString(result.error)
                outcome = .failure(PairingError.failed(message.isEmpty ? "Pairing failed (rc=\(rc))." : message))
            }
            al_pairing_result_free(&result)

            DispatchQueue.main.async {
                Unmanaged<PairingController>.fromOpaque(context).release()
                self.finish(outcome)
            }
        }
    }

    private func finish(_ outcome: Result<String, Error>) {
        stopAdvertising()
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            self?.keepAlive.stop()
        }
        running = false
        pin = nil

        switch outcome {
        case .success(let path):
            status = "Paired"
            pairContinuation?.resume(returning: path)
        case .failure(let error):
            status = "Pairing failed"
            pairContinuation?.resume(throwing: error)
        }
        pairContinuation = nil
    }

    fileprivate func startAdvertising(serviceID: String, port: Int32, txt: [String: Data]) {
        stopAdvertising()
        let service = NetService(
            domain: "",
            type: "_remotepairing-pairable-host._tcp.",
            name: serviceID,
            port: port
        )
        service.setTXTRecord(NetService.data(fromTXTRecord: txt))
        service.publish()
        netService = service
        status = "Approve CarrierSIM in Developer Mode"
    }

    fileprivate func presentPIN(_ value: String) {
        pin = value
        status = "Enter PIN \(value) in Developer Mode"
    }

    private func stopAdvertising() {
        netService?.stop()
        netService = nil
    }
}

private let carrierPairReadyCallback: ALPairReadyCb = { context, serviceID, port, keys, values, count in
    guard let context, let serviceID else { return }
    let controller = Unmanaged<PairingController>.fromOpaque(context).takeUnretainedValue()

    var txt: [String: Data] = [:]
    if let keys, let values {
        for index in 0..<Int(count) {
            guard let key = keys[index], let value = values[index] else { continue }
            txt[String(cString: key)] = Data(String(cString: value).utf8)
        }
    }

    let service = String(cString: serviceID)
    DispatchQueue.main.async {
        controller.startAdvertising(serviceID: service, port: Int32(port), txt: txt)
    }
}

private let carrierPairPinCallback: ALPairPinCb = { pin, context in
    guard let context, let pin else { return }
    let controller = Unmanaged<PairingController>.fromOpaque(context).takeUnretainedValue()
    let value = String(cString: pin)
    DispatchQueue.main.async {
        controller.presentPIN(value)
    }
}

private func cString(_ pointer: UnsafeMutablePointer<CChar>?) -> String {
    guard let pointer else { return "" }
    return String(cString: pointer)
}
