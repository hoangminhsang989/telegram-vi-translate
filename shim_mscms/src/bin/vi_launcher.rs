use std::path::PathBuf;

use mscms::remote_patch::launch_and_patch_telegram;

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

fn main() {
    let mut tg_path = default_telegram_path();
    let mut log_path = default_log_path();
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
            other => {
                eprintln!("Unknown argument: {other}");
                std::process::exit(2);
            }
        }
    }

    match launch_and_patch_telegram(&tg_path, "-noupdate", Some(&log_path)) {
        Ok(pid) => {
            println!("Launched Telegram.exe (PID={pid})");
        }
        Err(err) => {
            eprintln!("{err}");
            std::process::exit(1);
        }
    }
}
