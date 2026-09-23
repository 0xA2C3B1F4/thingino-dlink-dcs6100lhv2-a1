import CoreWLAN
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(2)
}

guard let ssid = readLine(strippingNewline: true),
      let psk = readLine(strippingNewline: true),
      ssid.range(of: #"^DCS6100-[0-9a-f]{8}$"#, options: .regularExpression) != nil,
      psk.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil else {
    fail("invalid private recovery Wi-Fi input")
}

guard let interface = CWWiFiClient.shared().interface() else {
    fail("no macOS Wi-Fi interface")
}

do {
    // A broad scan can omit the currently associated BSS.  This helper is
    // explicitly a transition to the recovery AP, so disconnect first and
    // make repeated calls perform the same bounded association transaction.
    interface.disassociate()
    // A standalone helper may receive a redacted nil CWNetwork.ssid even when
    // the exact targeted scan succeeded.  The scan itself is already bound to
    // the private session SSID, so do not discard that result by re-reading the
    // permission-redacted property.
    let matches = try interface.scanForNetworks(withSSID: Data(ssid.utf8))
        .sorted { $0.rssiValue > $1.rssiValue }
    guard let network = matches.first else {
        fail("private recovery AP was not found")
    }
    try interface.associate(to: network, password: psk)
    print("joined \(ssid)")
} catch {
    fail("private recovery AP association failed: \(error)")
}
