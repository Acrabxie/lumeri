use std::sync::Mutex;
use tauri::{Manager, State};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

struct ServerProcess(Mutex<Option<CommandChild>>);

/// Generic HTTP relay — uses reqwest with no_proxy to bypass system proxy.
/// Returns JSON: {"status": <u16>, "body": <string>}
#[tauri::command]
async fn api_call(
    method: String,
    path: String,
    body: Option<String>,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(300))
        .build()
        .map_err(|e| e.to_string())?;

    let url = format!("http://127.0.0.1:7788{}", path);

    let req = match method.to_uppercase().as_str() {
        "GET" => client.get(&url),
        "POST" => {
            let r = client.post(&url).header("Content-Type", "application/json");
            if let Some(b) = body {
                r.body(b)
            } else {
                r
            }
        }
        "DELETE" => {
            let r = client.delete(&url);
            if let Some(b) = body {
                r.header("Content-Type", "application/json").body(b)
            } else {
                r
            }
        }
        _ => return Err(format!("Unsupported method: {}", method)),
    };

    let resp = req
        .send()
        .await
        .map_err(|e| format!("Request failed: {}", e))?;
    let status = resp.status().as_u16();
    let text = resp.text().await.map_err(|e| e.to_string())?;

    Ok(serde_json::json!({ "status": status, "body": text }))
}

async fn upload_file_to_sidecar(src_path: String) -> Result<serde_json::Value, String> {
    let path = std::path::PathBuf::from(&src_path);
    if !path.exists() {
        return Err(format!("File not found: {}", src_path));
    }
    let filename = path
        .file_name()
        .ok_or("Invalid path")?
        .to_string_lossy()
        .to_string();

    let data = std::fs::read(&path).map_err(|e| e.to_string())?;

    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .post("http://127.0.0.1:7788/upload-media")
        .header("Content-Type", "application/octet-stream")
        .header("X-Filename", &filename)
        .body(data)
        .send()
        .await
        .map_err(|e| format!("Upload failed: {}", e))?;

    let status = resp.status().as_u16();
    let text = resp.text().await.map_err(|e| e.to_string())?;

    Ok(serde_json::json!({ "status": status, "body": text }))
}

/// Upload any supported media file to the Python server, bypassing system proxy.
/// src_path: absolute local file path selected by the user.
#[tauri::command]
async fn upload_media(src_path: String) -> Result<serde_json::Value, String> {
    upload_file_to_sidecar(src_path).await
}

/// Backward-compatible command kept for older frontend bundles.
#[tauri::command]
async fn upload_video(src_path: String) -> Result<serde_json::Value, String> {
    upload_file_to_sidecar(src_path).await
}

/// Fetch a video file from the server and return it as base64 for the WebView.
/// server_rel_path: relative path like "outputs/task_xxx_out.mp4"
#[tauri::command]
async fn fetch_video_b64(server_rel_path: String) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(60))
        .build()
        .map_err(|e| e.to_string())?;

    let url = format!("http://127.0.0.1:7788/file/{}", server_rel_path);
    let resp = client.get(&url).send().await.map_err(|e| e.to_string())?;

    if !resp.status().is_success() {
        return Err(format!("Server returned {}", resp.status()));
    }

    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    use base64::Engine as _;
    Ok(base64::engine::general_purpose::STANDARD.encode(&bytes))
}

/// Open a file or directory in Finder.
#[tauri::command]
async fn reveal_in_finder(path: String) -> Result<(), String> {
    std::process::Command::new("open")
        .arg("-R")
        .arg(&path)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Open a trusted external URL in the system browser.
#[tauri::command]
async fn open_url(url: String) -> Result<(), String> {
    if !url.starts_with("https://accounts.google.com/") {
        return Err("Only Google sign-in URLs can be opened here".to_string());
    }
    std::process::Command::new("open")
        .arg(&url)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
async fn save_config(openrouter_key: String, gemini_key: String) -> Result<(), String> {
    // 1. Write to ~/.gemia/config.json
    let config_dir = dirs_next::home_dir()
        .ok_or("Cannot find home directory")?
        .join(".gemia");
    std::fs::create_dir_all(&config_dir).map_err(|e| e.to_string())?;
    let config = serde_json::json!({
        "openrouter_api_key": &openrouter_key,
        "gemini_api_key": &gemini_key,
    });
    std::fs::write(
        config_dir.join("config.json"),
        serde_json::to_string_pretty(&config).unwrap(),
    )
    .map_err(|e| e.to_string())?;

    // 2. Push keys to the running sidecar so env vars take effect immediately
    //    (sidecar started before the file existed, so it has empty env vars)
    if let Ok(client) = reqwest::Client::builder()
        .no_proxy()
        .timeout(std::time::Duration::from_secs(5))
        .build()
    {
        let _ = client
            .post("http://127.0.0.1:7788/config")
            .header("Content-Type", "application/json")
            .body(
                serde_json::json!({
                    "openrouter_api_key": openrouter_key,
                    "gemini_api_key": gemini_key,
                })
                .to_string(),
            )
            .send()
            .await;
    }
    Ok(())
}

#[tauri::command]
fn config_exists() -> bool {
    let path = match dirs_next::home_dir() {
        Some(h) => h.join(".gemia").join("config.json"),
        None => return false,
    };
    if !path.exists() {
        return false;
    }
    // Validate that openrouter_api_key is present, non-empty, and not a placeholder
    let Ok(text) = std::fs::read_to_string(&path) else {
        return false;
    };
    let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) else {
        return false;
    };
    let key = json
        .get("openrouter_api_key")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    !key.is_empty() && key != "test" && key != "sk-or-..." && key.len() > 10
}

/// Read current API keys from ~/.gemia/config.json (returns empty strings if missing).
#[tauri::command]
fn get_config() -> serde_json::Value {
    let path = match dirs_next::home_dir() {
        Some(h) => h.join(".gemia").join("config.json"),
        None => return serde_json::json!({"openrouter_api_key": "", "gemini_api_key": ""}),
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return serde_json::json!({"openrouter_api_key": "", "gemini_api_key": ""});
    };
    let Ok(json) = serde_json::from_str::<serde_json::Value>(&text) else {
        return serde_json::json!({"openrouter_api_key": "", "gemini_api_key": ""});
    };
    let or_key = json
        .get("openrouter_api_key")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let gm_key = json
        .get("gemini_api_key")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    serde_json::json!({"openrouter_api_key": or_key, "gemini_api_key": gm_key})
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(ServerProcess(Mutex::new(None)))
        .setup(|app| {
            match app.shell().sidecar("gemia-server") {
                Ok(cmd) => match cmd.spawn() {
                    Ok((_rx, child)) => {
                        let state: State<ServerProcess> = app.state();
                        *state.0.lock().unwrap() = Some(child);
                        println!("gemia-server sidecar started on :7788");
                    }
                    Err(e) => eprintln!("Failed to spawn sidecar: {}", e),
                },
                Err(e) => eprintln!("Sidecar not found: {}", e),
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            api_call,
            upload_media,
            upload_video,
            fetch_video_b64,
            reveal_in_finder,
            open_url,
            save_config,
            config_exists,
            get_config,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
