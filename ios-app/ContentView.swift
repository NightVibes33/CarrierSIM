import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var model: CarrierSIMViewModel
    @ObservedObject private var pairing = PairingController.shared

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    header
                    pairingCard
                    tunnelCard

                    if model.isPaired {
                        simSection
                        recoveryCard
                        activityCard
                    }
                }
                .padding(16)
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationTitle("CarrierSIM")
            .navigationBarTitleDisplayMode(.inline)
            .task {
                model.refreshPairingState()
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "antenna.radiowaves.left.and.right")
                    .font(.system(size: 30, weight: .semibold))
                    .foregroundStyle(.blue)

                VStack(alignment: .leading, spacing: 2) {
                    Text("CarrierSIM")
                        .font(.title2.bold())
                    Text("On-device AirLift carrier profile switcher")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }

            Text(model.statusText)
                .font(.footnote)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .panel()
    }

    private var pairingCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(model.isPaired ? "This iPhone is paired" : "Pair this iPhone",
                  systemImage: model.isPaired ? "checkmark.shield.fill" : "link")
                .font(.headline)

            Text(pairing.status)
                .font(.caption)
                .foregroundStyle(.secondary)

            if let pin = pairing.pin {
                Text(pin)
                    .font(.system(size: 30, weight: .bold, design: .monospaced))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
            }

            HStack {
                if model.isPaired {
                    Button("Delete Pairing", role: .destructive) {
                        model.deletePairing()
                    }
                    .buttonStyle(.bordered)

                    Spacer()

                    Button("Scan SIMs") {
                        Task { await model.scanSIMs() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isBusy)
                } else {
                    Button(pairing.running ? "Pairing…" : "Pair This iPhone") {
                        Task { await model.pair() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(model.isBusy || pairing.running)
                }
            }
        }
        .panel()
    }

    private var tunnelCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Loopback tunnel", systemImage: "point.3.connected.trianglepath.dotted")
                .font(.headline)

            Text("CarrierSIM uses the same on-device RSD/AirLift transport as AirCard. LocalDevVPN or a compatible SideStore WireGuard tunnel must be active before Scan, Apply, Restore, or Reboot.")
                .font(.footnote)
                .foregroundStyle(.secondary)

            Button("Open LocalDevVPN") {
                model.openLocalDevVPN()
            }
            .buttonStyle(.bordered)
        }
        .panel()
    }

    @ViewBuilder
    private var simSection: some View {
        if model.sims.isEmpty {
            VStack(spacing: 10) {
                Image(systemName: "simcard")
                    .font(.system(size: 32))
                    .foregroundStyle(.secondary)
                Text("No SIMs loaded")
                    .font(.headline)
                Text("Turn on the loopback VPN, then tap Scan SIMs.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity)
            .panel()
        } else {
            ForEach(model.sims) { sim in
                simCard(sim)
            }
        }
    }

    private func simCard(_ sim: CarrierSIMLine) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label(sim.title, systemImage: "simcard.fill")
                    .font(.headline)
                Spacer()
                Text(sim.mcc + "-" + sim.mnc)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("IMSI  \(sim.imsi)")
                    .font(.caption.monospaced())
                Text("Current  \(sim.currentBundle.isEmpty ? "Unknown" : sim.currentBundle)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if !sim.iccidTail.isEmpty {
                    Text("ICCID  …\(sim.iccidTail)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Picker("Carrier bundle", selection: Binding(
                get: { model.selectedBundles[sim.id] ?? model.recommendedBundle(for: sim) },
                set: { model.selectedBundles[sim.id] = $0 }
            )) {
                ForEach(model.bundledChoices, id: \.self) { bundle in
                    Text(bundle).tag(bundle)
                }
                Text("Custom…").tag("Custom")
            }
            .pickerStyle(.menu)

            if (model.selectedBundles[sim.id] ?? "") == "Custom" {
                TextField("Bundle name, e.g. Vodafone_hu", text: $model.customBundle)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
            }

            HStack {
                Button("Restore Stock") {
                    Task { await model.restore(sim) }
                }
                .buttonStyle(.bordered)
                .disabled(model.isBusy)

                Spacer()

                Button("Apply") {
                    Task { await model.apply(sim) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.isBusy)
            }
        }
        .panel()
    }

    private var recoveryCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Finish", systemImage: "arrow.triangle.2.circlepath")
                .font(.headline)

            Text("After a successful catalog change, restart the iPhone so CommCenter re-evaluates the IMSI carrier-bundle link.")
                .font(.footnote)
                .foregroundStyle(.secondary)

            HStack {
                Button("Restore All IMSI Links", role: .destructive) {
                    Task { await model.restore(nil) }
                }
                .buttonStyle(.bordered)
                .disabled(model.isBusy)

                Spacer()

                Button("Reboot iPhone") {
                    Task { await model.reboot() }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.isBusy)
            }
        }
        .panel()
    }

    private var activityCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("AirLift activity", systemImage: "terminal")
                    .font(.headline)
                Spacer()
                if model.isBusy {
                    ProgressView()
                }
            }

            if model.logs.isEmpty {
                Text("No activity yet.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(model.logs.suffix(20).enumerated()), id: \.offset) { _, line in
                            Text(line)
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .panel()
    }
}

private extension View {
    func panel() -> some View {
        self
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(uiColor: .secondarySystemGroupedBackground),
                        in: RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}
