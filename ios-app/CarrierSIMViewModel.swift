import Foundation
import UIKit
import AirliftFFI

private struct CarrierSIMError: Error {
    let message: String
}

struct CarrierSIMLine: Identifiable, Hashable {
    let slot: String
    let mcc: String
    let mnc: String
    let imsi: String
    let currentBundle: String
    let iccidTail: String

    var id: String { imsi.isEmpty ? slot : imsi }
    var plmn: String { mcc + mnc }
    var title: String {
        switch slot {
        case "kOne": return "SIM 1"
        case "kTwo": return "SIM 2"
        default: return slot.isEmpty ? "SIM" : slot
        }
    }
}

@MainActor
final class CarrierSIMViewModel: ObservableObject {
    @Published var isPaired = PairingController.hasPairingFile
    @Published var sims: [CarrierSIMLine] = []
    @Published var selectedBundles: [String: String] = [:]
    @Published var customBundle = ""
    @Published var isBusy = false
    @Published var statusText = "Pair CarrierSIM with this iPhone to begin."
    @Published var logs: [String] = []
    @Published var lastResult = ""

    let bundledChoices = [
        "Vodafone_hu",
        "Vodafone_ro",
        "MTS_ua",
        "Telia_az",
        "Nova_is"
    ]

    func refreshPairingState() {
        isPaired = PairingController.hasPairingFile
        if isPaired && sims.isEmpty {
            statusText = "Paired. Turn on LocalDevVPN, then scan SIMs."
        }
    }

    func pair() async {
        guard !isBusy else { return }
        isBusy = true
        statusText = "Pairing…"
        defer { isBusy = false }

        do {
            _ = try await PairingController.shared.startAndWait()
            isPaired = true
            statusText = "Paired. Turn on LocalDevVPN, then scan SIMs."
        } catch {
            statusText = error.localizedDescription
        }
    }

    func deletePairing() {
        PairingController.shared.deletePairingFile()
        isPaired = false
        sims = []
        selectedBundles = [:]
        statusText = "Pairing file deleted."
    }

    func openLocalDevVPN() {
        guard let url = URL(string: "localdevvpn://") else { return }
        UIApplication.shared.open(url)
    }

    func scanSIMs() async {
        guard !isBusy, isPaired else { return }
        isBusy = true
        statusText = "Reading SIM information…"
        logs.removeAll(keepingCapacity: true)
        defer { isBusy = false }

        let result = await runStatus()
        switch result {
        case .success(let json):
            parseStatus(json)
            statusText = sims.isEmpty ? "No active SIM/eSIM with an IMSI was reported." : "Ready."
        case .failure(let error):
            statusText = error.message
        }
    }

    func apply(_ sim: CarrierSIMLine) async {
        guard !isBusy else { return }
        let selected = selectedBundles[sim.id] ?? recommendedBundle(for: sim)
        let bundle = selected == "Custom" ? customBundle.trimmingCharacters(in: .whitespacesAndNewlines) : selected
        guard !bundle.isEmpty else {
            statusText = "Enter a carrier bundle name."
            return
        }

        saveOriginalBundleIfNeeded(sim)
        isBusy = true
        statusText = "Applying \(bundle) to \(sim.title)…"
        logs.removeAll(keepingCapacity: true)
        defer { isBusy = false }

        switch await runApply(imsi: sim.imsi, bundle: bundle) {
        case .success(let json):
            lastResult = json
            statusText = "Carrier catalog updated. Reboot the iPhone to make CommCenter reload it."
            await scanSIMsAfterOperation()
        case .failure(let error):
            statusText = error.message
        }
    }

    func restore(_ sim: CarrierSIMLine?) async {
        guard !isBusy else { return }
        let targets = sim.map { [$0] } ?? sims
        guard !targets.isEmpty else {
            statusText = "No SIMs are loaded."
            return
        }

        isBusy = true
        statusText = sim == nil ? "Restoring original carrier selections…" : "Restoring \(sim!.title)…"
        logs.removeAll(keepingCapacity: true)
        defer { isBusy = false }

        for target in targets {
            guard let original = originalBundle(for: target) else {
                statusText = "No pre-change carrier bundle backup exists for \(target.title)."
                continue
            }
            switch await runApply(imsi: target.imsi, bundle: original) {
            case .success(let json):
                lastResult = json
                clearOriginalBundle(for: target)
            case .failure(let message):
                statusText = message
                return
            }
        }

        statusText = "Original carrier selection restored. Reboot the iPhone to reload CommCenter."
        await scanSIMsAfterOperation()
    }

    func reboot() async {
        guard !isBusy, isPaired else { return }
        isBusy = true
        statusText = "Sending restart request…"
        defer { isBusy = false }

        let path = PairingController.pairingFilePath()
        let outcome: Result<Void, CarrierSIMError> = await withCheckedContinuation { continuation in
            let context = Unmanaged.passUnretained(self).toOpaque()
            DispatchQueue.global(qos: .userInitiated).async {
                var errorPointer: UnsafeMutablePointer<CChar>?
                let rc = path.withCString {
                    al_device_respring($0, carrierSIMLogCallback, context, &errorPointer)
                }
                if rc == 0 {
                    continuation.resume(returning: .success(()))
                } else {
                    let message = takeCString(errorPointer)
                    continuation.resume(returning: .failure(CarrierSIMError(message: message.isEmpty ? "Restart request failed." : message)))
                }
            }
        }

        switch outcome {
        case .success:
            statusText = "Restart request sent."
        case .failure(let error):
            statusText = error.message
        }
    }

    func recommendedBundle(for sim: CarrierSIMLine) -> String {
        switch sim.plmn {
        case "25001": return "Vodafone_ro"
        case "25701", "25702", "25704", "25705", "25706": return "MTS_ua"
        default:
            if sim.mcc == "250" || sim.mcc == "257" { return "Vodafone_hu" }
            return "Vodafone_hu"
        }
    }

    private func backupKey(for sim: CarrierSIMLine) -> String {
        "carriersim.original.\(sim.imsi)"
    }

    private func normalizedBundleName(_ value: String) -> String? {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let noPrefix = trimmed.hasPrefix("com.apple.") ? String(trimmed.dropFirst("com.apple.".count)) : trimmed
        return noPrefix.hasSuffix(".bundle") ? String(noPrefix.dropLast(".bundle".count)) : noPrefix
    }

    private func saveOriginalBundleIfNeeded(_ sim: CarrierSIMLine) {
        let key = backupKey(for: sim)
        guard UserDefaults.standard.string(forKey: key) == nil,
              let original = normalizedBundleName(sim.currentBundle),
              !original.isEmpty
        else { return }
        UserDefaults.standard.set(original, forKey: key)
    }

    private func originalBundle(for sim: CarrierSIMLine) -> String? {
        UserDefaults.standard.string(forKey: backupKey(for: sim))
    }

    private func clearOriginalBundle(for sim: CarrierSIMLine) {
        UserDefaults.standard.removeObject(forKey: backupKey(for: sim))
    }

    private func scanSIMsAfterOperation() async {
        let result = await runStatus()
        if case .success(let json) = result {
            parseStatus(json)
        }
    }

    private func parseStatus(_ json: String) {
        lastResult = json
        guard let data = json.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = object["sims"] as? [[String: Any]]
        else {
            sims = []
            return
        }

        sims = rows.compactMap { row in
            let imsi = row["imsi"] as? String ?? ""
            guard imsi.count == 15 else { return nil }
            let iccid = row["iccid"] as? String ?? ""
            return CarrierSIMLine(
                slot: row["slot"] as? String ?? "",
                mcc: row["mcc"] as? String ?? "",
                mnc: row["mnc"] as? String ?? "",
                imsi: imsi,
                currentBundle: row["bundle"] as? String ?? "",
                iccidTail: String(iccid.suffix(4))
            )
        }

        for sim in sims where selectedBundles[sim.id] == nil {
            selectedBundles[sim.id] = recommendedBundle(for: sim)
        }
    }

    private func runStatus() async -> Result<String, CarrierSIMError> {
        let path = PairingController.pairingFilePath()
        return await withCheckedContinuation { continuation in
            let context = Unmanaged.passUnretained(self).toOpaque()
            DispatchQueue.global(qos: .userInitiated).async {
                var jsonPointer: UnsafeMutablePointer<CChar>?
                var errorPointer: UnsafeMutablePointer<CChar>?
                let rc = path.withCString {
                    al_carriersim_status($0, carrierSIMLogCallback, context, &jsonPointer, &errorPointer)
                }
                if rc == 0 {
                    continuation.resume(returning: .success(takeCString(jsonPointer)))
                } else {
                    let message = takeCString(errorPointer)
                    continuation.resume(returning: .failure(CarrierSIMError(message: message.isEmpty ? "Unable to read SIM information." : message)))
                }
            }
        }
    }

    private func runApply(imsi: String, bundle: String) async -> Result<String, CarrierSIMError> {
        let path = PairingController.pairingFilePath()
        return await withCheckedContinuation { continuation in
            let context = Unmanaged.passUnretained(self).toOpaque()
            DispatchQueue.global(qos: .userInitiated).async {
                var jsonPointer: UnsafeMutablePointer<CChar>?
                var errorPointer: UnsafeMutablePointer<CChar>?
                let rc = path.withCString { pathC in
                    imsi.withCString { imsiC in
                        bundle.withCString { bundleC in
                            al_carriersim_apply(
                                pathC,
                                imsiC,
                                bundleC,
                                carrierSIMLogCallback,
                                context,
                                &jsonPointer,
                                &errorPointer
                            )
                        }
                    }
                }
                if rc == 0 {
                    continuation.resume(returning: .success(takeCString(jsonPointer)))
                } else {
                    let message = takeCString(errorPointer)
                    continuation.resume(returning: .failure(CarrierSIMError(message: message.isEmpty ? "Carrier update failed." : message)))
                }
            }
        }
    }

    fileprivate func appendLog(_ line: String) {
        logs.append(line)
        if logs.count > 250 {
            logs.removeFirst(logs.count - 250)
        }
    }
}

private let carrierSIMLogCallback: ALLogCallback = { context, message in
    guard let context, let message else { return }
    let model = Unmanaged<CarrierSIMViewModel>.fromOpaque(context).takeUnretainedValue()
    let line = String(cString: message)
    DispatchQueue.main.async {
        model.appendLog(line)
    }
}

private func takeCString(_ pointer: UnsafeMutablePointer<CChar>?) -> String {
    guard let pointer else { return "" }
    let value = String(cString: pointer)
    al_string_free(pointer)
    return value
}
