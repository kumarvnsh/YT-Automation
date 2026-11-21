#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use axum::{
    Json, Router,
    extract::{DefaultBodyLimit, Path, Query, Request, State},
    http::{HeaderMap, HeaderValue, Method, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Redirect, Response},
    routing::{get, post},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{net::Ipv4Addr, path::PathBuf, process::Stdio, sync::Arc, time::Duration};
use tower_http::services::{ServeDir, ServeFile};
use tower::ServiceExt;
use tokio::io::AsyncWriteExt;

#[derive(Deserialize)]
struct Settings {
    port: u16,
    tail_host: String,
    allowed_login: String,
    python: PathBuf,
    repo: PathBuf,
    archive_config: PathBuf,
    snapshot: PathBuf,
    frontend: PathBuf,
    #[serde(default)]
    controls_enabled: bool,
    #[serde(default)]
    shotsnip_root: PathBuf,
}

#[derive(Deserialize)]
struct PreviewQuery { revision: f64 }

async fn preview(
    State(settings): State<Arc<Settings>>,
    Path(id): Path<String>,
    Query(query): Query<PreviewQuery>,
    req: Request,
) -> Response {
    if !settings.controls_enabled
        || id.is_empty() || id.len() > 100
        || !id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
    {
        return StatusCode::NOT_FOUND.into_response();
    }
    let index = tokio::fs::read(settings.snapshot.with_file_name("dashboard-previews.json")).await;
    let entry = index.ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|map| map.get(&id).cloned());
    let Some(entry) = entry else { return StatusCode::NOT_FOUND.into_response(); };
    if entry.get("revision").and_then(Value::as_f64) != Some(query.revision) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let Some(path) = entry.get("path").and_then(Value::as_str) else { return StatusCode::NOT_FOUND.into_response(); };
    let path = tokio::fs::canonicalize(path).await;
    let directory = tokio::fs::canonicalize(settings.shotsnip_root.join("work").join("manual").join(id)).await;
    match (path, directory) {
        (Ok(file), Ok(root)) if file.starts_with(&root) && file.extension().is_some_and(|e| e.eq_ignore_ascii_case("mp4")) => {
            match ServeFile::new(file).oneshot(req).await {
                Ok(response) => response.into_response(),
                Err(_) => StatusCode::NOT_FOUND.into_response(),
            }
        }
        _ => StatusCode::NOT_FOUND.into_response(),
    }
}

/// Clip and draft media listed by the snapshot exporter. Each path is re-checked here: it must sit
/// under the Shotsnip work folder or the private drafts folder and be a video or image.
async fn media(
    State(settings): State<Arc<Settings>>,
    Path(id): Path<String>,
    req: Request,
) -> Response {
    if id.len() != 32 || !id.bytes().all(|c| c.is_ascii_hexdigit()) {
        return StatusCode::NOT_FOUND.into_response();
    }
    let index = tokio::fs::read(settings.snapshot.with_file_name("dashboard-media.json")).await;
    let path = index.ok().and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|map| map.get(&id).and_then(Value::as_str).map(PathBuf::from));
    let Some(path) = path else { return StatusCode::NOT_FOUND.into_response(); };
    let Ok(file) = tokio::fs::canonicalize(path).await else { return StatusCode::NOT_FOUND.into_response(); };
    let mut roots = Vec::new();
    if let Ok(root) = tokio::fs::canonicalize(settings.shotsnip_root.join("work")).await { roots.push(root); }
    if let Some(parent) = settings.snapshot.parent() {
        if let Ok(root) = tokio::fs::canonicalize(parent.join("drafts")).await { roots.push(root); }
    }
    let allowed_type = file.extension().and_then(|e| e.to_str())
        .is_some_and(|e| ["mp4", "jpg", "jpeg", "png", "webp"].iter().any(|x| e.eq_ignore_ascii_case(x)));
    if !allowed_type || !roots.iter().any(|root| file.starts_with(root)) {
        return StatusCode::NOT_FOUND.into_response();
    }
    match ServeFile::new(file).oneshot(req).await {
        Ok(response) => response.into_response(),
        Err(_) => StatusCode::NOT_FOUND.into_response(),
    }
}

fn controls_error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"message":message}))).into_response()
}

fn controls_process(settings: &Settings, operation: &str) -> tokio::process::Command {
    let mut command = tokio::process::Command::new(&settings.python);
    command.current_dir(&settings.repo)
        .args(["-m", "studio_hq.dashboard_controls"])
        .arg(&settings.archive_config)
        .arg(operation)
        .stderr(Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    command
}

async fn submit_control(
    State(settings): State<Arc<Settings>>,
    headers: HeaderMap,
    Json(payload): Json<Value>,
) -> Response {
    if !settings.controls_enabled {
        return controls_error(StatusCode::SERVICE_UNAVAILABLE, "Studio controls are disabled");
    }
    let host = headers.get(header::HOST).and_then(|h| h.to_str().ok()).unwrap_or("");
    let origin = headers.get(header::ORIGIN).and_then(|h| h.to_str().ok()).unwrap_or("");
    let local_host = format!("127.0.0.1:{}", settings.port);
    let local_name = format!("localhost:{}", settings.port);
    let allowed_origin = if host == local_host || host == local_name {
        format!("http://{host}")
    } else {
        format!("https://{}", settings.tail_host)
    };
    if origin != allowed_origin || headers.get("x-studio-hq").is_none_or(|header| header != "1") {
        return controls_error(StatusCode::FORBIDDEN, "Open the dashboard to send a control");
    }
    let mut command = controls_process(&settings, "submit");
    command.stdin(Stdio::piped()).stdout(Stdio::piped());
    let response = tokio::time::timeout(Duration::from_secs(20), async {
        let mut child = command.spawn()?;
        let mut input = child.stdin.take().ok_or(std::io::Error::other("No command input"))?;
        input.write_all(payload.to_string().as_bytes()).await?;
        drop(input);
        child.wait_with_output().await
    }).await;
    match response {
        Ok(Ok(output)) => match serde_json::from_slice::<Value>(&output.stdout) {
            Ok(body) => {
                let status = match output.status.code() {
                    Some(0) => StatusCode::OK,
                    Some(2) => StatusCode::CONFLICT,
                    _ => StatusCode::SERVICE_UNAVAILABLE,
                };
                (status, Json(body)).into_response()
            },
            Err(_) => controls_error(StatusCode::SERVICE_UNAVAILABLE, "Control response was unavailable"),
        },
        _ => controls_error(StatusCode::SERVICE_UNAVAILABLE, "Control outcome was not confirmed. Retry the same request."),
    }
}

async fn maintain(settings: Arc<Settings>) {
    let mut interval = tokio::time::interval(Duration::from_secs(10));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        interval.tick().await;
        let _ = controls_process(&settings, "maintain")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .status().await;
    }
}

async fn read_snapshot(State(settings): State<Arc<Settings>>) -> Response {
    let snapshot = tokio::fs::read(&settings.snapshot)
        .await
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok());
    match snapshot {
        Some(value) if value.get("version").and_then(Value::as_u64) == Some(1) => {
            Json(value).into_response()
        }
        _ => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"error":"Waiting for the first activity snapshot"})),
        )
            .into_response(),
    }
}

async fn protect(State(settings): State<Arc<Settings>>, req: Request, next: Next) -> Response {
    let headers = req.headers();
    let host = headers.get(header::HOST).and_then(|value| value.to_str().ok());
    let identity = headers
        .get("tailscale-user-login")
        .and_then(|value| value.to_str().ok());
    let local_host = format!("127.0.0.1:{}", settings.port);
    let local_name = format!("localhost:{}", settings.port);
    let tail_authority = format!("{}:443", settings.tail_host);
    // Tailscale Serve keeps the client's Host header and adds identity headers only for
    // user-owned devices, so anything it forwards must carry the owner's identity. Only
    // direct requests from this PC may rely on the local Host alone.
    let proxied = ["x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"]
        .iter()
        .any(|name| headers.contains_key(*name));
    let allowed = match (host, identity) {
        (Some(h), Some(login)) => {
            (h == settings.tail_host || h == tail_authority || h == local_host || h == local_name)
                && login.eq_ignore_ascii_case(&settings.allowed_login)
        }
        (Some(h), None) => !proxied && (h == local_host || h == local_name),
        _ => false,
    };
    if !allowed {
        return (StatusCode::FORBIDDEN, "Use your authorized Tailscale account").into_response();
    }
    let is_static = req.method() == Method::GET && req.uri().path().contains("/assets/");
    let mut response = next.run(req).await;
    let headers = response.headers_mut();
    if !is_static {
        headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    }
    headers.insert(header::X_CONTENT_TYPE_OPTIONS, HeaderValue::from_static("nosniff"));
    headers.insert(
        header::CONTENT_SECURITY_POLICY,
        HeaderValue::from_static("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'none'"),
    );
    headers.insert(header::REFERRER_POLICY, HeaderValue::from_static("no-referrer"));
    response
}

async fn refresh(settings: Arc<Settings>) {
    let mut schedule = tokio::time::interval(Duration::from_secs(30));
    schedule.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        schedule.tick().await;
        let mut exporter = tokio::process::Command::new(&settings.python);
        exporter.current_dir(&settings.repo)
            .args(["-m", "studio_hq.dashboard_snapshot"])
            .arg(&settings.archive_config)
            .arg(&settings.snapshot)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .kill_on_drop(true);
        #[cfg(windows)]
        exporter.creation_flags(0x08000000); // CREATE_NO_WINDOW
        match tokio::time::timeout(Duration::from_secs(20), exporter.status()).await {
            Ok(Ok(status)) if status.success() => {},
            _ => eprintln!("Studio HQ snapshot refresh failed; keeping previous snapshot"),
        }
    }
}

#[tokio::main]
async fn main() {
    if let Err(error) = run().await {
        if let Ok(executable) = std::env::current_exe() {
            let _ = std::fs::write(executable.with_file_name("last-service.log"), error.to_string());
        }
        std::process::exit(1);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let settings_file = std::env::args_os().nth(1).ok_or("Expected a settings file path")?;
    let settings: Arc<Settings> = Arc::new(serde_json::from_slice(&std::fs::read(settings_file)?)?);
    if settings.allowed_login.trim().is_empty() || settings.tail_host.trim().is_empty() {
        return Err("Configure the Tailscale owner and hostname".into());
    }
    if !settings.frontend.join("index.html").is_file() {
        return Err("Built frontend is missing".into());
    }
    let files = ServeDir::new(&settings.frontend);
    let app_routes = Router::new()
        .route("/api/snapshot", get(read_snapshot))
        .route("/api/preview/{id}", get(preview))
        .route("/api/media/{id}", get(media))
        .route("/api/command", post(submit_control).layer(DefaultBodyLimit::max(8192)))
        .fallback_service(files.clone());
    let app = Router::new()
        .route("/studio-hq", get(|| async { Redirect::temporary("./studio-hq/") }))
        .route_service("/studio-hq/", ServeFile::new(settings.frontend.join("index.html")))
        .nest("/studio-hq", app_routes.clone())
        .merge(app_routes)
        .layer(middleware::from_fn_with_state(settings.clone(), protect))
        .with_state(settings.clone());
    let listener = tokio::net::TcpListener::bind((Ipv4Addr::LOCALHOST, settings.port)).await?;
    tokio::spawn(refresh(settings.clone()));
    if settings.controls_enabled {
        tokio::spawn(maintain(settings));
    }
    axum::serve(listener, app).await?;
    Ok(())
}
