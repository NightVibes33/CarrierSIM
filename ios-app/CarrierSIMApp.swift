import SwiftUI

@main
struct CarrierSIMApp: App {
    @StateObject private var model = CarrierSIMViewModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
        }
    }
}
