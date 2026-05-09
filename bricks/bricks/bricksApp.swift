import SwiftUI

@main
struct bricksApp: App {
    @StateObject private var hub = HubConnectionManager()
    @StateObject private var server = ServerConnectionManager()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView(hub: hub, server: server)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background {
                hub.releaseBLE()
            }
        }
    }
}
