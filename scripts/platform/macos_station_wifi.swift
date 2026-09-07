import CoreWLAN
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(2)
}

func decodeHex(_ value: String) -> Data? {
    guard value.count >= 2, value.count <= 64, value.count % 2 == 0,
          value.range(of: #"^[0-9a-f]+$"#, options: .regularExpression) != nil else {
        return nil
    }
    var result = Data()
    var index = value.startIndex
    while index < value.endIndex {
        let next = value.index(index, offsetBy: 2)
        guard let byte = UInt8(value[index..<next], radix: 16) else { return nil }
        result.append(byte)
        index = next
    }
    return result
}

guard let ssidHex = readLine(strippingNewline: true),
      let psk = readLine(strippingNewline: true),
      let ssid = decodeHex(ssidHex),
      psk.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil else {
    fail("invalid private station Wi-Fi input")
}

guard let interface = CWWiFiClient.shared().interface() else {
    fail("no macOS Wi-Fi interface")
}

do {
    interface.disassociate()
    let matches = try interface.scanForNetworks(withSSID: ssid)
        .sorted { $0.rssiValue > $1.rssiValue }
    guard let network = matches.first else {
        fail("private station Wi-Fi was not found")
    }
    try interface.associate(to: network, password: psk)
    print("associated")
} catch {
    fail("private station Wi-Fi association failed: \(error)")
}
