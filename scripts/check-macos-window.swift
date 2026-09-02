// CI-only observer: inspect the real packaged app without importing Python code.
// The input bundle must be generic; this check never connects Google or exports mail.
import AppKit
import CoreGraphics
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("FAIL: \(message)\n".utf8))
    exit(1)
}

guard CommandLine.arguments.count == 3 else {
    fail("Expected app bundle path and screenshot output path")
}
let bundleURL = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
let screenshot = CommandLine.arguments[2]
let deadline = Date().addingTimeInterval(30)
var observedApp: NSRunningApplication?
var observedWindow: CGWindowID?

while Date() < deadline {
    if let app = NSRunningApplication.runningApplications(
        withBundleIdentifier: "com.dhar174.mailbox-rescue"
    ).first(where: { $0.bundleURL?.standardizedFileURL == bundleURL }) {
        observedApp = app
        app.activate(options: [.activateIgnoringOtherApps])
        let windows = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID
        ) as? [[String: Any]] ?? []
        for window in windows {
            guard let pid = window[kCGWindowOwnerPID as String] as? Int32,
                  pid == app.processIdentifier,
                  let bounds = window[kCGWindowBounds as String] as? [String: Double],
                  (bounds["Width"] ?? 0) >= 400,
                  (bounds["Height"] ?? 0) >= 300,
                  let number = window[kCGWindowNumber as String] as? UInt32 else { continue }
            observedWindow = number
            break
        }
    }
    if observedWindow != nil { break }
    Thread.sleep(forTimeInterval: 0.5)
}

guard let app = observedApp, let windowID = observedWindow else {
    observedApp?.terminate()
    fail("Packaged app did not produce an onscreen window within 30 seconds")
}
// Give Qt time to paint, then capture only the app window for human inspection.
Thread.sleep(forTimeInterval: 2)
let capture = Process()
capture.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
capture.arguments = ["-x", "-l", String(windowID), screenshot]
do {
    try capture.run()
    capture.waitUntilExit()
    guard capture.terminationStatus == 0,
          let attributes = try? FileManager.default.attributesOfItem(atPath: screenshot),
          let size = attributes[.size] as? NSNumber, size.intValue > 0 else {
        app.terminate()
        fail("Could not capture the packaged app window")
    }
} catch {
    app.terminate()
    fail("Could not start window capture")
}
guard app.terminate() else { fail("Packaged app rejected normal quit") }
let quitDeadline = Date().addingTimeInterval(10)
while !app.isTerminated && Date() < quitDeadline {
    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
}
guard app.isTerminated else { fail("Packaged app did not quit within 10 seconds") }
print("PASS: extracted packaged app opened an onscreen window and quit normally")
print("Screenshot captured for UI inspection; live OAuth/export/resume NOT TESTED")
