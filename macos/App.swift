import AppKit
import Foundation

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var statusLabel: NSTextField!
    var openButton: NSButton!
    var stopButton: NSButton!
    var loginButton: NSButton!
    var loginMenu: NSMenuItem!
    var project: URL!
    var working = false
    var ending = false
    var loginEnabled = false
    let address = URL(string: "http://localhost:8000")!

    func validProject(_ url: URL) -> Bool {
        return FileManager.default.fileExists(atPath: url.appendingPathComponent("scripts/app-control.sh").path)
            && FileManager.default.fileExists(atPath: url.appendingPathComponent("app/main.py").path)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let parent = Bundle.main.bundleURL.deletingLastPathComponent().resolvingSymlinksInPath()
        if validProject(parent) { project = parent }
        else if let saved = UserDefaults.standard.string(forKey: "ProjectFolder"), validProject(URL(fileURLWithPath: saved)) {
            project = URL(fileURLWithPath: saved)
        } else {
            let picker = NSOpenPanel()
            picker.title = "选择现有课程视频备份项目文件夹"
            picker.message = "请选择包含 app、data 和 start.command 的项目目录。授权和历史仍使用这里的数据。"
            picker.canChooseFiles = false; picker.canChooseDirectories = true; picker.allowsMultipleSelection = false
            guard picker.runModal() == .OK, let folder = picker.url, validProject(folder) else {
                NSApp.terminate(nil); return
            }
            project = folder
        }
        UserDefaults.standard.set(project.path, forKey: "ProjectFolder")
        makeMenu(); makeWindow(); beginStart()
    }

    func makeMenu() {
        let menu = NSMenu(); let item = NSMenuItem(); let appMenu = NSMenu()
        appMenu.addItem(withTitle: "打开课程视频备份页面", action: #selector(beginStart), keyEquivalent: "o")
        appMenu.addItem(withTitle: "停止本地服务", action: #selector(stopService), keyEquivalent: "")
        loginMenu = NSMenuItem(title: "登录 Mac 后启动后台服务", action: #selector(toggleLogin), keyEquivalent: "")
        appMenu.addItem(loginMenu); appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出课程视频备份", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu; menu.addItem(item); NSApp.mainMenu = menu
    }

    func makeWindow() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 450, height: 225),
            styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "课程视频备份"; window.delegate = self; window.isReleasedWhenClosed = false
        let view = window.contentView!
        let heading = NSTextField(labelWithString: "课程视频备份 · 本地服务")
        heading.font = .boldSystemFont(ofSize: 18); heading.frame = NSRect(x: 25, y: 170, width: 400, height: 26); view.addSubview(heading)
        statusLabel = NSTextField(wrappingLabelWithString: "正在启动；首次运行可能需要安装依赖……")
        statusLabel.frame = NSRect(x: 25, y: 110, width: 400, height: 49); view.addSubview(statusLabel)
        openButton = NSButton(title: "打开页面", target: self, action: #selector(beginStart))
        openButton.bezelStyle = .rounded; openButton.frame = NSRect(x: 20, y: 66, width: 125, height: 34); view.addSubview(openButton)
        stopButton = NSButton(title: "停止服务", target: self, action: #selector(stopService))
        stopButton.bezelStyle = .rounded; stopButton.frame = NSRect(x: 150, y: 66, width: 125, height: 34); view.addSubview(stopButton)
        loginButton = NSButton(checkboxWithTitle: "登录 Mac 后自动启动后台服务（可选）", target: self, action: #selector(toggleLogin))
        loginButton.frame = NSRect(x: 25, y: 25, width: 400, height: 26); view.addSubview(loginButton)
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }

    func controls(_ enabled: Bool) {
        openButton.isEnabled = enabled; stopButton.isEnabled = enabled; loginButton.isEnabled = enabled
    }

    func call(_ action: String, completion: @escaping ([String: Any]) -> Void) {
        working = true; controls(false)
        let root = project!
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process(); let output = Pipe()
            process.executableURL = URL(fileURLWithPath: "/bin/zsh")
            process.arguments = ["-f", root.appendingPathComponent("scripts/app-control.sh").path, action]
            process.currentDirectoryURL = root
            // Data is always from the selected existing project, never a new default location.
            var env = ProcessInfo.processInfo.environment
            env["COURSE_BACKUP_DATA"] = root.appendingPathComponent("data").path
            process.environment = env
            process.standardInput = FileHandle.nullDevice; process.standardOutput = output; process.standardError = FileHandle.nullDevice
            var result: [String: Any] = ["ok": false, "message": "服务操作未完成，请检查项目目录和运行环境。"]
            do {
                try process.run()
                let data = output.fileHandleForReading.readDataToEndOfFile(); process.waitUntilExit()
                if let parsed = try JSONSerialization.jsonObject(with: data) as? [String: Any] { result = parsed }
            } catch { }
            DispatchQueue.main.async {
                self.working = false; self.controls(true)
                if let enabled = result["login_enabled"] as? Bool { self.loginEnabled = enabled }
                self.loginButton.state = self.loginEnabled ? .on : .off
                self.loginMenu.state = self.loginButton.state
                self.statusLabel.stringValue = result["message"] as? String ?? "服务状态待确认。"
                completion(result)
            }
        }
    }

    func error(_ result: [String: Any]) {
        let alert = NSAlert(); alert.messageText = "本地服务操作未完成"
        alert.informativeText = result["message"] as? String ?? "请检查项目 README。"
        alert.addButton(withTitle: "确定"); alert.runModal()
    }

    @objc func beginStart() {
        guard project != nil, !working, !ending else { return }
        statusLabel.stringValue = "正在检查并启动本地服务；首次运行可能需要安装依赖……"
        call("start") { result in
            if result["ok"] as? Bool == true, result["running"] as? Bool == true { NSWorkspace.shared.open(self.address) }
            else { self.error(result) }
        }
    }

    @objc func stopService() {
        guard !working, !ending else { return }
        statusLabel.stringValue = "正在安全停止本地服务……"
        call("stop") { result in if result["ok"] as? Bool != true { self.error(result) } }
    }

    @objc func toggleLogin() {
        guard !working, !ending else { return }
        loginButton.state = loginEnabled ? .on : .off
        let alert = NSAlert()
        alert.messageText = loginEnabled ? "关闭登录启动？" : "开启登录启动？"
        alert.informativeText = loginEnabled
            ? "关闭后不会在登录时启动。由 LaunchAgent 启动的服务会安全停止；以后仍可双击 App。"
            : "macOS 将在你登录后启动本项目的后台服务，不打开浏览器，也不会自动公开视频。默认关闭，只有确认后才安装 LaunchAgent。"
        alert.addButton(withTitle: "取消"); alert.addButton(withTitle: loginEnabled ? "关闭" : "开启")
        if alert.runModal() != .alertSecondButtonReturn { return }
        call(loginEnabled ? "login-disable" : "login-enable") { result in
            if result["ok"] as? Bool != true { self.error(result) }
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window?.makeKeyAndOrderFront(nil); beginStart(); return true
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool { NSApp.terminate(nil); return false }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if project == nil || window == nil { return .terminateNow }
        if working || ending { return .terminateCancel }
        ending = true; statusLabel.stringValue = "正在安全停止服务并退出……"
        call("stop") { result in
            let ok = result["ok"] as? Bool == true
            if !ok { self.error(result); self.ending = false }
            NSApp.reply(toApplicationShouldTerminate: ok)
        }
        return .terminateLater
    }
}

let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
