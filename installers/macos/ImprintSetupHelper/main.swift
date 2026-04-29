// imprint-setup-helper — native macOS progress window for first-run bootstrap.
//
// Invoked by imprint-launcher via exec on first launch:
//   imprint-setup-helper <bin> <writable_home> <profile> <with_llm:0|1>
//
// The helper replaces the launcher process (exec), so Imprint.app stays
// active in the Dock while setup runs. Closing the window exits the app.
// On success the sentinel (~/.local/share/imprint/.first-run.done) is
// touched; the next launch goes straight to `imprint ui open`.

import Cocoa
import Foundation

let cliArgs = CommandLine.arguments
guard cliArgs.count >= 5 else {
    fputs("Usage: imprint-setup-helper <bin> <writable_home> <profile> <with_llm:0|1>\n", stderr)
    exit(1)
}

let gBin          = cliArgs[1]
let gWritableHome = cliArgs[2]
let gProfile      = cliArgs[3]
let gWithLlm      = cliArgs[4] == "1"
let gSentinel     = gWritableHome + "/.first-run.done"

var gBootstrapArgs = ["bootstrap", "--profile", gProfile, "--non-interactive"]
gBootstrapArgs += gWithLlm ? ["--with-llm"] : ["--no-llm"]

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

    func finish(ok: Bool) {
        DispatchQueue.main.async {
            if ok {
                self.statusField.stringValue = "Setup complete — Imprint is ready."
                self.append("\n[+] Setup complete. Close this window, then launch Imprint from the Dock.\n")
            } else {
                self.statusField.stringValue = "Setup failed — see output above."
                self.statusField.textColor = .systemRed
                self.append("\n[!] Setup failed. Close this window and re-launch Imprint to retry.\n")
            }
            self.closeBtn.isHidden = false
        }
    }

    @objc func closeTapped() { NSApp.terminate(nil) }

    // MARK: — Bootstrap

    func runSetup() {
        let ok1 = runCmd(gBin, gBootstrapArgs)
        if !ok1 { finish(ok: false); return }

        let ok2 = runCmd(gBin, ["setup"])
        if ok2 {
            try? FileManager.default.createDirectory(
                atPath: gWritableHome,
                withIntermediateDirectories: true,
                attributes: nil
            )
            FileManager.default.createFile(atPath: gSentinel, contents: nil, attributes: nil)
        }
        finish(ok: ok2)
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
NSApp.delegate = delegate
NSApp.setActivationPolicy(.regular)
NSApp.run()
