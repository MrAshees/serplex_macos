use serde::Deserialize;
use std::{
    env,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{BufRead, BufReader, Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, Url};

#[derive(Default)]
struct BackendState {
    child: Mutex<Option<Child>>,
    stderr_log: Mutex<Option<PathBuf>>,
    stdout_log: Mutex<Option<PathBuf>>,
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppConfig {
    app_port: Option<u16>,
    app_host: Option<String>,
    endpoint: Option<String>,
    wireguard_endpoint: Option<String>,
    use_wireguard: Option<bool>,
    no_tunnel: Option<bool>,
    skip_pull: Option<bool>,
    vision_model: Option<String>,
    update_manifest_url: Option<String>,
    web_search_api_key: Option<String>,
    api_key: Option<String>,
    model_api_key: Option<String>,
}

struct UpdateTarget {
    style: String,
    target: PathBuf,
}

impl AppConfig {
    fn effective_endpoint(&self) -> String {
        if self.use_wireguard.unwrap_or(false) {
            self.wireguard_endpoint
                .clone()
                .unwrap_or_else(|| "http://10.253.77.2:11434".to_string())
        } else {
            self.endpoint
                .clone()
                .unwrap_or_else(|| "https://serplex.ashees.dev".to_string())
        }
    }

    fn effective_api_key(&self) -> String {
        self.model_api_key
            .clone()
            .or_else(|| self.api_key.clone())
            .unwrap_or_default()
    }
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState::default())
        .setup(|app| {
            if let Err(error) = start_serplex(app) {
                show_start_error(app, &error);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                stop_backend(window.app_handle());
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run Serplex");
}

fn start_serplex(app: &mut tauri::App) -> Result<(), String> {
    let app_root = find_app_root(app)?;
    let config = load_config(&app_root);
    let host = config
        .app_host
        .clone()
        .unwrap_or_else(|| "127.0.0.1".to_string());
    let listen_host = if host == "*" {
        "0.0.0.0".to_string()
    } else {
        host
    };
    let preferred_port = config.app_port.unwrap_or(18787);
    let port = find_available_port(&listen_host, preferred_port)?;
    let browser_host = if listen_host == "0.0.0.0" {
        "127.0.0.1"
    } else {
        listen_host.as_str()
    };
    let app_url = format!("http://{}:{}", browser_host, port);
    let user_data = serplex_user_data_dir();
    let log_dir = user_data.join("logs");
    fs::create_dir_all(&log_dir).map_err(|err| format!("Cannot create log directory: {err}"))?;

    let child = spawn_backend(&app_root, &config, &listen_host, port, &user_data, &log_dir)?;
    {
        let state = app.state::<BackendState>();
        *state
            .child
            .lock()
            .map_err(|_| "Backend lock poisoned".to_string())? = Some(child);
        *state
            .stdout_log
            .lock()
            .map_err(|_| "Log lock poisoned".to_string())? =
            Some(log_dir.join("tauri-backend.out.log"));
        *state
            .stderr_log
            .lock()
            .map_err(|_| "Log lock poisoned".to_string())? =
            Some(log_dir.join("tauri-backend.err.log"));
    }

    wait_for_server(
        app,
        &format!("{}/api/config", app_url),
        Duration::from_secs(120),
    )?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "Main window was not found".to_string())?;
    let url = Url::parse(&app_url).map_err(|err| format!("Invalid UI URL: {err}"))?;
    window
        .navigate(url)
        .map_err(|err| format!("Cannot open UI: {err}"))?;
    Ok(())
}

fn find_app_root(app: &tauri::App) -> Result<PathBuf, String> {
    let mut candidates = Vec::new();
    if let Ok(resource_dir) = app.path().resource_dir() {
        candidates.push(resource_dir.join("app"));
        candidates.push(resource_dir);
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            candidates.push(parent.to_path_buf());
            candidates.push(parent.join("app"));
        }
    }
    if let Ok(current) = env::current_dir() {
        candidates.push(current.clone());
        candidates.push(current.join("src-tauri").join("resources").join("app"));
    }

    for candidate in candidates {
        if candidate
            .join("codex_local")
            .join("codex_lite_server.py")
            .is_file()
        {
            return Ok(candidate);
        }
    }
    Err("Bundled Serplex backend was not found.".to_string())
}

fn load_config(app_root: &Path) -> AppConfig {
    for name in ["serplex.desktop.json", "local-codex.desktop.json"] {
        let path = app_root.join(name);
        if let Ok(text) = fs::read_to_string(path) {
            if let Ok(config) = serde_json::from_str::<AppConfig>(&text) {
                return config;
            }
        }
    }
    AppConfig::default()
}

fn find_available_port(host: &str, preferred: u16) -> Result<u16, String> {
    for offset in 0..80u16 {
        let port = preferred.saturating_add(offset);
        if TcpListener::bind((host, port)).is_ok() {
            return Ok(port);
        }
    }
    Err(format!("No free local port was found near {preferred}."))
}

fn spawn_backend(
    app_root: &Path,
    config: &AppConfig,
    listen_host: &str,
    port: u16,
    user_data: &Path,
    log_dir: &Path,
) -> Result<Child, String> {
    let backend = backend_command(app_root)?;

    let stdout_path = log_dir.join("tauri-backend.out.log");
    let stderr_path = log_dir.join("tauri-backend.err.log");
    rotate_log(&stdout_path);
    rotate_log(&stderr_path);

    let mut command = Command::new(&backend.program);
    command
        .args(&backend.args)
        .current_dir(app_root)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1")
        .env("CODEX_LITE_HOST", listen_host)
        .env("CODEX_LITE_PORT", port.to_string())
        .env("CODEX_LITE_USER_DATA_DIR", user_data)
        .env("CODEX_LITE_STATE_DIR", user_data.join("runtime"))
        .env("CODEX_LITE_VERSION_FILE", app_root.join("app-version.json"))
        .env(
            "CODEX_LITE_WORKSPACE",
            env::var("CODEX_LITE_WORKSPACE").unwrap_or_else(|_| home_dir().display().to_string()),
        )
        .env(
            "CODEX_LITE_MODEL",
            env::var("CODEX_LITE_MODEL")
                .unwrap_or_else(|_| "qwen3-coder:30b-a3b-q4_K_M".to_string()),
        )
        .env(
            "CODEX_LITE_VISION_MODEL",
            config
                .vision_model
                .clone()
                .unwrap_or_else(|| "llama3.2-vision:11b".to_string()),
        )
        .env(
            "CODEX_LITE_UPDATE_MANIFEST_URL",
            config.update_manifest_url.clone().unwrap_or_else(|| {
                "https://serplex.ashees.dev/serplex-updates/manifest.json".to_string()
            }),
        )
        .env("OLLAMA_BASE_URL", config.effective_endpoint())
        .env("SERPLEX_DESKTOP_RUNTIME", "tauri")
        .env("SERPLEX_DESKTOP_PID", std::process::id().to_string())
        .env(
            "CODEX_LITE_ALLOW_COMMANDS",
            env::var("CODEX_LITE_ALLOW_COMMANDS").unwrap_or_else(|_| "0".to_string()),
        )
        .env(
            "CODEX_LITE_FULL_ACCESS",
            env::var("CODEX_LITE_FULL_ACCESS").unwrap_or_else(|_| "0".to_string()),
        );

    let update_target = detect_update_target(app_root);
    command
        .env("SERPLEX_UPDATE_STYLE", update_target.style)
        .env("SERPLEX_UPDATE_TARGET", update_target.target);

    let api_key = config.effective_api_key();
    if !api_key.trim().is_empty() {
        command.env("CODEX_LITE_MODEL_API_KEY", api_key);
    }
    if let Some(key) = &config.web_search_api_key {
        if !key.trim().is_empty() {
            command.env("WEB_SEARCH_API_KEY", key);
        }
    }
    if config.no_tunnel.unwrap_or(true) || config.use_wireguard.unwrap_or(false) {
        command.env("SERPLEX_NO_TUNNEL", "1");
    }
    if config.skip_pull.unwrap_or(true) {
        command.env("SERPLEX_SKIP_PULL", "1");
    }

    let mut child = command.spawn().map_err(|err| {
        format!(
            "Cannot start Serplex backend through {}: {err}",
            backend.program.display()
        )
    })?;

    if let Some(stdout) = child.stdout.take() {
        pipe_log(stdout, stdout_path);
    }
    if let Some(stderr) = child.stderr.take() {
        pipe_log(stderr, stderr_path);
    }
    Ok(child)
}

struct BackendCommand {
    program: PathBuf,
    args: Vec<OsString>,
}

fn backend_command(app_root: &Path) -> Result<BackendCommand, String> {
    let executable_name = if cfg!(windows) {
        "serplex-backend.exe"
    } else {
        "serplex-backend"
    };
    let bundled_executable = app_root.join("backend").join(executable_name);
    if bundled_executable.is_file() {
        return Ok(BackendCommand {
            program: bundled_executable,
            args: Vec::new(),
        });
    }

    let server_script = app_root.join("codex_local").join("codex_lite_server.py");
    if !server_script.is_file() {
        return Err(format!(
            "Backend script was not found: {}",
            server_script.display()
        ));
    }

    Ok(BackendCommand {
        program: python_path(app_root),
        args: vec![server_script.into_os_string()],
    })
}

fn python_path(app_root: &Path) -> PathBuf {
    if cfg!(windows) {
        let bundled = app_root.join("python").join("python.exe");
        if bundled.is_file() {
            return bundled;
        }
        PathBuf::from("python")
    } else {
        let bundled = app_root.join("python").join("bin").join("python3");
        if bundled.is_file() {
            return bundled;
        }
        PathBuf::from("python3")
    }
}

fn detect_update_target(app_root: &Path) -> UpdateTarget {
    if cfg!(windows) {
        return UpdateTarget {
            style: "windows-installer".to_string(),
            target: env::current_exe().unwrap_or_else(|_| PathBuf::from("Serplex.exe")),
        };
    }

    if cfg!(target_os = "macos") {
        if let Ok(exe) = env::current_exe() {
            if let Some(bundle) = macos_app_bundle(&exe) {
                return UpdateTarget {
                    style: "macos-app".to_string(),
                    target: bundle,
                };
            }
        }
    }

    if cfg!(target_os = "linux") {
        if let Ok(appimage) = env::var("APPIMAGE") {
            if !appimage.trim().is_empty() {
                return UpdateTarget {
                    style: "linux-appimage".to_string(),
                    target: PathBuf::from(appimage),
                };
            }
        }
    }

    UpdateTarget {
        style: "directory".to_string(),
        target: app_root.to_path_buf(),
    }
}

fn macos_app_bundle(exe: &Path) -> Option<PathBuf> {
    for ancestor in exe.ancestors() {
        if ancestor
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.eq_ignore_ascii_case("app"))
            .unwrap_or(false)
        {
            return Some(ancestor.to_path_buf());
        }
    }
    None
}

fn wait_for_server(app: &tauri::App, url: &str, timeout: Duration) -> Result<(), String> {
    let start = Instant::now();
    let mut last_error = String::new();
    while start.elapsed() < timeout {
        {
            let state = app.state::<BackendState>();
            if let Ok(mut guard) = state.child.lock() {
                if let Some(child) = guard.as_mut() {
                    if let Ok(Some(status)) = child.try_wait() {
                        return Err(format!(
                            "Local Serplex server exited before UI startup. Exit code: {}.\n\n{}",
                            status
                                .code()
                                .map(|code| code.to_string())
                                .unwrap_or_else(|| "unknown".to_string()),
                            read_log_tail(app)
                        ));
                    }
                }
            };
        }
        match http_ready(url) {
            Ok(true) => return Ok(()),
            Ok(false) => {}
            Err(error) => last_error = error,
        }
        thread::sleep(Duration::from_millis(750));
    }
    Err(format!(
        "Local Serplex server did not start in {} seconds. Last error: {}\n\n{}",
        timeout.as_secs(),
        last_error,
        read_log_tail(app)
    ))
}

fn http_ready(url: &str) -> Result<bool, String> {
    let parsed = Url::parse(url).map_err(|err| err.to_string())?;
    let host = parsed
        .host_str()
        .ok_or_else(|| "URL has no host".to_string())?;
    let port = parsed
        .port_or_known_default()
        .ok_or_else(|| "URL has no port".to_string())?;
    let path = parsed.path();
    let mut stream = TcpStream::connect((host, port)).map_err(|err| err.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .map_err(|err| err.to_string())?;
    let request = format!(
        "GET {} HTTP/1.1\r\nHost: {}:{}\r\nConnection: close\r\n\r\n",
        path, host, port
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|err| err.to_string())?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|err| err.to_string())?;
    let status = response
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .unwrap_or(0);
    Ok((200..500).contains(&status))
}

fn stop_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendState>();
    if let Ok(mut child) = state.child.lock() {
        if let Some(child) = child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
        *child = None;
    };
}

fn show_start_error(app: &mut tauri::App, error: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let escaped =
            serde_json::to_string(error).unwrap_or_else(|_| "\"Unknown error\"".to_string());
        let script = format!(
            "document.body.innerHTML = '<main style=\"max-width:900px;margin:12vh auto;padding:32px;font:15px Segoe UI,Arial;color:#eee;white-space:pre-wrap\"><h1 style=\"font-size:24px\">Cannot start Serplex</h1><p></p></main>'; document.querySelector('p').textContent = {};",
            escaped
        );
        let _ = window.eval(&script);
    }
}

fn pipe_log<R: Read + Send + 'static>(reader: R, path: PathBuf) {
    thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut file = match OpenOptions::new().create(true).append(true).open(path) {
            Ok(file) => file,
            Err(_) => return,
        };
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    let _ = file.write_all(line.as_bytes());
                }
            }
        }
    });
}

fn rotate_log(path: &Path) {
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let _ = File::create(path);
}

fn read_log_tail(app: &tauri::App) -> String {
    let state = app.state::<BackendState>();
    let mut parts = Vec::new();
    if let Ok(stderr) = state.stderr_log.lock() {
        if let Some(path) = stderr.as_ref() {
            add_log_tail(&mut parts, "stderr", path);
        }
    }
    if let Ok(stdout) = state.stdout_log.lock() {
        if let Some(path) = stdout.as_ref() {
            add_log_tail(&mut parts, "stdout", path);
        }
    }
    if parts.is_empty() {
        "Backend logs are empty.".to_string()
    } else {
        parts.join("\n\n")
    }
}

fn add_log_tail(parts: &mut Vec<String>, name: &str, path: &Path) {
    if let Ok(text) = fs::read_to_string(path) {
        let lines: Vec<&str> = text.lines().collect();
        let start = lines.len().saturating_sub(32);
        parts.push(format!("{}:\n{}", name, lines[start..].join("\n")));
    }
}

fn serplex_user_data_dir() -> PathBuf {
    if let Ok(value) = env::var("CODEX_LITE_USER_DATA_DIR") {
        if !value.trim().is_empty() {
            return PathBuf::from(value);
        }
    }
    if cfg!(windows) {
        env::var("APPDATA")
            .or_else(|_| env::var("LOCALAPPDATA"))
            .map(|base| PathBuf::from(base).join("Serplex"))
            .unwrap_or_else(|_| home_dir().join("AppData").join("Roaming").join("Serplex"))
    } else if cfg!(target_os = "macos") {
        home_dir()
            .join("Library")
            .join("Application Support")
            .join("Serplex")
    } else {
        env::var("XDG_DATA_HOME")
            .map(|base| PathBuf::from(base).join("serplex"))
            .unwrap_or_else(|_| home_dir().join(".local").join("share").join("serplex"))
    }
}

fn home_dir() -> PathBuf {
    env::var("HOME")
        .or_else(|_| env::var("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
}
