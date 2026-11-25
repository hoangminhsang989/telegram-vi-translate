#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use core::ffi::c_void;
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

use mscms::pe_patch::find_translate_tracker_patch;
use mscms::remote_patch::{
    launch_and_patch_telegram, list_processes_by_name, terminate_process_by_pid,
    verify_pid_patched, write_log,
};

const ATTACH_PARENT_PROCESS: u32 = 0xFFFF_FFFF;
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const DETACHED_PROCESS: u32 = 0x0000_0008;
const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;

#[link(name = "kernel32")]
unsafe extern "system" {
    fn AttachConsole(dwProcessId: u32) -> i32;
    fn GetStdHandle(nStdHandle: u32) -> *mut c_void;
    fn WriteFile(
        hFile: *mut c_void,
        lpBuffer: *const u8,
        nNumberOfBytesToWrite: u32,
        lpNumberOfBytesWritten: *mut u32,
        lpOverlapped: *mut c_void,
    ) -> i32;
}

fn write_console(is_err: bool, text: &str) {
    let std_id = if is_err { 0xFFFF_FFF4u32 } else { 0xFFFF_FFF5u32 }; // STD_ERROR_HANDLE / STD_OUTPUT_HANDLE
    let handle = unsafe { GetStdHandle(std_id) };
    let msg = format!("{text}\n");
    if !handle.is_null() && handle as isize != -1 {
        let mut written = 0u32;
        unsafe {
            WriteFile(
                handle,
                msg.as_ptr(),
                msg.len() as u32,
                &mut written,
                core::ptr::null_mut(),
            );
        }
    } else if is_err {
        eprintln!("{text}");
    } else {
        println!("{text}");
    }
}

fn default_telegram_path() -> PathBuf {
    let appdata = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Users\Public"));
    appdata.join("Telegram Desktop").join("Telegram.exe")
}

fn default_log_path() -> PathBuf {
    let local = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Users\Public"));
    local.join("TelegramViShim").join("shim.log")
}

fn discover_repo_root() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut cur = exe.parent();
    for _ in 0..6 {
        if let Some(dir) = cur {
            if dir.join("translator_proxy").join("__main__.py").is_file() {
                return Some(dir.to_path_buf());
            }
            cur = dir.parent();
        } else {
            break;
        }
    }
    if let Ok(cwd) = std::env::current_dir()
        && cwd.join("translator_proxy").join("__main__.py").is_file()
    {
        return Some(cwd);
    }
    None
}

fn extract_json_string(raw: &str, key: &str) -> Option<String> {
    let pattern = format!("\"{key}\"");
    let idx = raw.find(&pattern)?;
    let rest = &raw[idx + pattern.len()..];
    let colon = rest.find(':')?;
    let after = rest[colon + 1..].trim_start();
    let stripped = after.strip_prefix('"')?;
    let end = stripped.find('"')?;
    Some(stripped[..end].to_owned())
}

fn extract_json_u16(raw: &str, key: &str) -> Option<u16> {
    let pattern = format!("\"{key}\"");
    let idx = raw.find(&pattern)?;
    let rest = &raw[idx + pattern.len()..];
    let colon = rest.find(':')?;
    let after = rest[colon + 1..].trim_start();
    let digits: String = after.chars().take_while(|c| c.is_ascii_digit()).collect();
    digits.parse::<u16>().ok()
}

fn read_proxy_addr(repo_root: &Path) -> (String, u16) {
    let cfg_path = repo_root.join("translator_proxy").join("config.json");
    let default_path = repo_root.join("translator_proxy").join("config.default.json");
    let chosen = if cfg_path.is_file() { cfg_path } else { default_path };
    if let Ok(raw) = std::fs::read_to_string(chosen) {
        let host = extract_json_string(&raw, "host").unwrap_or_else(|| "127.0.0.1".to_owned());
        let port = extract_json_u16(&raw, "port").unwrap_or(18765);
        return (host, port);
    }
    ("127.0.0.1".to_owned(), 18765)
}

fn proxy_is_reachable(host: &str, port: u16) -> bool {
    let addr_str = format!("{host}:{port}");
    let Ok(addr) = addr_str.parse::<SocketAddr>() else {
        return false;
    };
    TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok()
}

fn find_pythonw_or_python() -> String {
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        let py_dir = PathBuf::from(local).join("Programs").join("Python");
        if let Ok(entries) = std::fs::read_dir(&py_dir) {
            let mut candidates: Vec<PathBuf> = entries.flatten().map(|e| e.path()).collect();
            candidates.sort();
            candidates.reverse();
            for c in candidates {
                let pyw = c.join("pythonw.exe");
                if pyw.is_file() {
                    return pyw.to_string_lossy().into_owned();
                }
            }
        }
    }
    "pythonw".to_owned()
}

fn ensure_proxy_running(repo_root: &Path, log_path: &Path) {
    let (host, port) = read_proxy_addr(repo_root);
    if proxy_is_reachable(&host, port) {
        return;
    }

    let logs_dir = repo_root.join("logs");
    let _ = std::fs::create_dir_all(&logs_dir);
    let proxy_log = logs_dir.join("proxy.log");
    let pid_file = logs_dir.join("proxy.pid");

    let mut spawned = None;
    'outer: for exe in [find_pythonw_or_python(), "pythonw".to_owned(), "python".to_owned()] {
        for flags in [
            CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
            CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        ] {
            let mut cmd = Command::new(&exe);
            cmd.args(["-m", "translator_proxy", "--log-file"])
                .arg(&proxy_log)
                .current_dir(repo_root)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            #[cfg(windows)]
            cmd.creation_flags(flags);
            if let Ok(child) = cmd.spawn() {
                spawned = Some(child);
                break 'outer;
            }
        }
    }

    let Some(child) = spawned else {
        write_log(Some(log_path), "[warn] failed to spawn translator_proxy");
        return;
    };
    let _ = std::fs::write(&pid_file, format!("{}\n", child.id()));

    let deadline = Instant::now() + Duration::from_secs(4);
    while Instant::now() < deadline {
        if proxy_is_reachable(&host, port) {
            write_log(
                Some(log_path),
                &format!("[ok] started translator_proxy pid={} at {host}:{port}", child.id()),
            );
            return;
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    write_log(
        Some(log_path),
        &format!("[warn] translator_proxy pid={} did not open {host}:{port} within 4s", child.id()),
    );
}

fn inspect_running_telegram(tg_path: &Path) -> (Vec<u32>, bool) {
    let pids = list_processes_by_name("Telegram.exe");
    if pids.is_empty() {
        return (pids, false);
    }
    let Ok(bytes) = std::fs::read(tg_path) else {
        return (pids, false);
    };
    let Ok(plan) = find_translate_tracker_patch(&bytes) else {
        return (pids, false);
    };
    let all_patched = pids
        .iter()
        .all(|&pid| verify_pid_patched(pid, &plan).unwrap_or(false));
    (pids, all_patched)
}

fn quote_arg_for_cmdline(arg: &str) -> String {
    if arg.is_empty() || arg.contains([' ', '\t', '"']) {
        format!("\"{}\"", arg.replace('"', "\\\""))
    } else {
        arg.to_owned()
    }
}

fn main() {
    unsafe { AttachConsole(ATTACH_PARENT_PROCESS) };

    let mut tg_path = default_telegram_path();
    let mut log_path = default_log_path();
    let mut repo_root = discover_repo_root();
    let mut check_only = false;
    let mut force_restart = false;
    let mut restart_if_unpatched = false;
    let mut no_proxy = false;
    let mut forwarded: Vec<String> = Vec::new();

    let mut args_iter = std::env::args().skip(1);
    while let Some(arg) = args_iter.next() {
        match arg.as_str() {
            "--telegram" => {
                if let Some(v) = args_iter.next() {
                    tg_path = PathBuf::from(v);
                }
            }
            "--log-file" => {
                if let Some(v) = args_iter.next() {
                    log_path = PathBuf::from(v);
                }
            }
            "--repo-root" => {
                if let Some(v) = args_iter.next() {
                    repo_root = Some(PathBuf::from(v));
                }
            }
            "--check" => check_only = true,
            "--restart" => force_restart = true,
            "--restart-if-unpatched" => restart_if_unpatched = true,
            "--no-proxy" => no_proxy = true,
            "--" => {
                forwarded.extend(args_iter);
                break;
            }
            other => forwarded.push(other.to_owned()),
        }
    }

    if check_only {
        let (pids, patched) = inspect_running_telegram(&tg_path);
        let running = !pids.is_empty();
        let pid_list = pids
            .iter()
            .map(|p| p.to_string())
            .collect::<Vec<_>>()
            .join(",");
        write_console(
            false,
            &format!("{{\"running\":{running},\"patched\":{patched},\"pids\":[{pid_list}]}}"),
        );
        std::process::exit(if !running || patched { 0 } else { 1 });
    }

    if !no_proxy && let Some(ref root) = repo_root {
        ensure_proxy_running(root, &log_path);
    }

    let (running_pids, already_patched) = inspect_running_telegram(&tg_path);
    if !running_pids.is_empty() {
        if force_restart || (restart_if_unpatched && !already_patched) {
            for pid in &running_pids {
                let _ = terminate_process_by_pid(*pid);
            }
            std::thread::sleep(Duration::from_millis(350));
        } else if already_patched {
            let mut cmd = Command::new(&tg_path);
            if !forwarded.iter().any(|a| a.eq_ignore_ascii_case("-noupdate")) {
                cmd.arg("-noupdate");
            }
            cmd.args(&forwarded);
            let _ = cmd.spawn();
            write_console(
                false,
                &format!("Telegram.exe already running patched (PIDs={running_pids:?})"),
            );
            return;
        }
    }

    if !forwarded.iter().any(|a| a.eq_ignore_ascii_case("-noupdate")) {
        forwarded.insert(0, "-noupdate".to_owned());
    }
    let extra_args = forwarded
        .iter()
        .map(|a| quote_arg_for_cmdline(a))
        .collect::<Vec<_>>()
        .join(" ");

    match launch_and_patch_telegram(&tg_path, &extra_args, Some(&log_path)) {
        Ok(pid) => {
            write_console(false, &format!("Launched Telegram.exe (PID={pid})"));
        }
        Err(err) => {
            write_console(true, &err);
            std::process::exit(1);
        }
    }
}
