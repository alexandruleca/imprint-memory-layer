// imprint-setup-helper — native macOS progress window for first-run bootstrap.
//
// Invoked by imprint-launcher via exec on first launch:
//   imprint-setup-helper <bin> <writable_home> <profile>
//
// The helper replaces the launcher process (exec), so Imprint.app stays
// active in the Dock while setup runs. Closing the window exits the app.
// On success the sentinel (~/.local/share/imprint/.first-run.done) is
// touched and the helper continues the launch the way every later launch
// does, with `imprint ui open`, then exits: the user never relaunches.

import Cocoa
import Foundation

let cliArgs = CommandLine.arguments
guard cliArgs.count >= 4 else {
    fputs("Usage: imprint-setup-helper <bin> <writable_home> <profile>\n", stderr)
    exit(1)
}

let gBin          = cliArgs[1]
let gWritableHome = cliArgs[2]
let gProfile      = cliArgs[3]
let gSentinel     = gWritableHome + "/.first-run.done"

let gBootstrapArgs = ["bootstrap", "--profile", gProfile, "--non-interactive"]

// ---------------------------------------------------------------------------

class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var textView: NSTextView!
    var statusField: NSTextField!
    var closeBtn: NSButton!

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildWindow()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.global(qos: .userInitiated).async { self.runSetup() }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    // MARK: — UI

    func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 680, height: 440),
            styleMask: [.titled, .closable, .resizable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Imprint — First-run Setup"
        window.center()
        window.delegate = self

        let content = window.contentView!

        statusField = NSTextField(labelWithString: "Setting up Imprint (this may take a few minutes)…")
        statusField.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(statusField)

        let scrollView = NSScrollView()
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        scrollView.hasVerticalScroller = true
        scrollView.borderType = .bezelBorder
        content.addSubview(scrollView)

        textView = NSTextView(frame: .zero)
        textView.isEditable = false
        textView.isSelectable = true
        textView.font = NSFont(name: "Menlo", size: 11)
            ?? NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        textView.backgroundColor = NSColor(calibratedWhite: 0.1, alpha: 1)
        textView.textColor = NSColor(calibratedWhite: 0.9, alpha: 1)
        textView.textContainerInset = NSSize(width: 4, height: 4)
        scrollView.documentView = textView
        textView.autoresizingMask = [.width]

        closeBtn = NSButton(title: "Close", target: self, action: #selector(closeTapped))
        closeBtn.translatesAutoresizingMaskIntoConstraints = false
        closeBtn.isHidden = true
        content.addSubview(closeBtn)

        NSLayoutConstraint.activate([
            statusField.topAnchor.constraint(equalTo: content.topAnchor, constant: 16),
            statusField.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            statusField.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),

            scrollView.topAnchor.constraint(equalTo: statusField.bottomAnchor, constant: 8),
            scrollView.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 16),
            scrollView.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),
            scrollView.bottomAnchor.constraint(equalTo: closeBtn.topAnchor, constant: -12),

            closeBtn.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -16),
            closeBtn.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -16),
            closeBtn.widthAnchor.constraint(equalToConstant: 80),
        ])
    }

    func append(_ text: String) {
        DispatchQueue.main.async {
            let attrs: [NSAttributedString.Key: Any] = [
                .foregroundColor: NSColor(calibratedWhite: 0.9, alpha: 1),
                .font: NSFont(name: "Menlo", size: 11)
                    ?? NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
            ]
            self.textView.textStorage?.append(NSAttributedString(string: text, attributes: attrs))
            self.textView.scrollToEndOfDocument(nil)
        }
    }

    func fail(_ status: String, _ advice: String) {
        DispatchQueue.main.async {
            self.statusField.stringValue = status
            self.statusField.textColor = .systemRed
            self.append("\n[!] \(advice)\n")
            self.closeBtn.isHidden = false
        }
    }

    @objc func closeTapped() { NSApp.terminate(nil) }

    // MARK: — Bootstrap

    func runSetup() {
        guard runCmd(gBin, gBootstrapArgs), runCmd(gBin, ["setup", "--non-interactive"]) else {
            fail("Setup failed — see output above.", "Setup failed. Close this window and re-launch Imprint to retry.")
            return
        }
        try? FileManager.default.createDirectory(
            atPath: gWritableHome,
            withIntermediateDirectories: true,
            attributes: nil
        )
        FileManager.default.createFile(atPath: gSentinel, contents: nil, attributes: nil)

        DispatchQueue.main.async { self.statusField.stringValue = "Setup complete — opening Imprint…" }
        // `ui open` starts the desktop app detached and returns; its splash
        // takes over from this window.
        guard runCmd(gBin, ["ui", "open"]) else {
            fail("Setup complete, but Imprint did not open — see output above.",
                 "Setup is complete. Close this window and launch Imprint again.")
            return
        }
        DispatchQueue.main.async { NSApp.terminate(nil) }
    }

    func runCmd(_ exe: String, _ arguments: [String]) -> Bool {
        append("\n$ \(([exe] + arguments).joined(separator: " "))\n")

        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = arguments

        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe

        let sem = DispatchSemaphore(value: 0)

        // readabilityHandler fires on a private queue; EOF delivers empty Data.
        pipe.fileHandleForReading.readabilityHandler = { [weak self] fh in
            let data = fh.availableData
            if data.isEmpty { sem.signal(); return }
            if let str = String(data: data, encoding: .utf8) { self?.append(str) }
        }

        do {
            try p.run()
        } catch {
            pipe.fileHandleForReading.readabilityHandler = nil
            append("error: \(error)\n")
            return false
        }

        sem.wait() // wait for EOF on pipe
        pipe.fileHandleForReading.readabilityHandler = nil
        p.waitUntilExit()

        return p.terminationStatus == 0
    }
}

let delegate = AppDelegate()
let application = NSApplication.shared
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
