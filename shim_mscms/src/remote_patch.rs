use core::ffi::c_void;
use std::fs::OpenOptions;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

use crate::pe_patch::{PatchPlan, build_rpl_single_true_stub, find_translate_tracker_patch};

const CREATE_SUSPENDED: u32 = 0x0000_0004;
const MEM_COMMIT: u32 = 0x1000;
const MEM_RESERVE: u32 = 0x2000;
const PAGE_EXECUTE_READ: u32 = 0x20;
const PAGE_EXECUTE_READWRITE: u32 = 0x40;
const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
const PROCESS_VM_READ: u32 = 0x0010;
const PROCESS_TERMINATE: u32 = 0x0001;
const SYNCHRONIZE: u32 = 0x0010_0000;
const TH32CS_SNAPPROCESS: u32 = 0x0000_0002;
const INVALID_HANDLE_VALUE: isize = -1;
const MAX_LOG_BYTES: u64 = 256 * 1024;
const KEEP_LOG_BYTES: u64 = 64 * 1024;

#[repr(C)]
struct ProcessBasicInformation {
    exit_status: usize,
    peb_base_address: usize,
    affinity_mask: usize,
    base_priority: isize,
    unique_process_id: usize,
    inherited_from_unique_process_id: usize,
}

#[repr(C)]
struct StartupInfoW {
    cb: u32,
    lp_reserved: *mut u16,
    lp_desktop: *mut u16,
    lp_title: *mut u16,
    dw_x: u32,
    dw_y: u32,
    dw_x_size: u32,
    dw_y_size: u32,
    dw_x_count_chars: u32,
    dw_y_count_chars: u32,
    dw_fill_attribute: u32,
    dw_flags: u32,
    w_show_window: u16,
    cb_reserved2: u16,
    lp_reserved2: *mut u8,
    h_std_input: *mut c_void,
    h_std_output: *mut c_void,
    h_std_error: *mut c_void,
}

#[repr(C)]
struct ProcessInformation {
    h_process: *mut c_void,
    h_thread: *mut c_void,
    dw_process_id: u32,
    dw_thread_id: u32,
}

#[repr(C)]
pub struct ProcessEntry32W {
    dw_size: u32,
    cnt_usage: u32,
    th32_process_id: u32,
    th32_default_heap_id: usize,
    th32_module_id: u32,
    cnt_threads: u32,
    th32_parent_process_id: u32,
    pc_pri_class_base: i32,
    dw_flags: u32,
    sz_exe_file: [u16; 260],
}

#[repr(C)]
struct SystemTime {
    year: u16,
    month: u16,
    day_of_week: u16,
    day: u16,
    hour: u16,
    minute: u16,
    second: u16,
    milliseconds: u16,
}

#[link(name = "ntdll")]
unsafe extern "system" {
    fn NtQueryInformationProcess(
        process_handle: *mut c_void,
        process_information_class: u32,
        process_information: *mut c_void,
        process_information_length: u32,
        return_length: *mut u32,
    ) -> i32;
}

#[link(name = "kernel32")]
unsafe extern "system" {
    fn CreateProcessW(
        lpApplicationName: *const u16,
        lpCommandLine: *mut u16,
        lpProcessAttributes: *mut c_void,
        lpThreadAttributes: *mut c_void,
        bInheritHandles: i32,
        dwCreationFlags: u32,
        lpEnvironment: *mut c_void,
        lpCurrentDirectory: *const u16,
        lpStartupInfo: *const StartupInfoW,
        lpProcessInformation: *mut ProcessInformation,
    ) -> i32;
    fn OpenProcess(dwDesiredAccess: u32, bInheritHandle: i32, dwProcessId: u32) -> *mut c_void;
    fn TerminateProcess(hProcess: *mut c_void, uExitCode: u32) -> i32;
    fn WaitForSingleObject(hHandle: *mut c_void, dwMilliseconds: u32) -> u32;
    fn CreateToolhelp32Snapshot(dwFlags: u32, th32ProcessID: u32) -> *mut c_void;
    fn Process32FirstW(hSnapshot: *mut c_void, lppe: *mut ProcessEntry32W) -> i32;
    fn Process32NextW(hSnapshot: *mut c_void, lppe: *mut ProcessEntry32W) -> i32;
    fn GetLocalTime(lpSystemTime: *mut SystemTime);
    fn ReadProcessMemory(
        hProcess: *mut c_void,
        lpBaseAddress: *const c_void,
        lpBuffer: *mut c_void,
        nSize: usize,
        lpNumberOfBytesRead: *mut usize,
    ) -> i32;
    fn WriteProcessMemory(
        hProcess: *mut c_void,
        lpBaseAddress: *mut c_void,
        lpBuffer: *const c_void,
        nSize: usize,
        lpNumberOfBytesWritten: *mut usize,
    ) -> i32;
    fn VirtualAllocEx(
        hProcess: *mut c_void,
        lpAddress: *mut c_void,
        dwSize: usize,
        flAllocationType: u32,
        flProtect: u32,
    ) -> *mut c_void;
    fn VirtualProtectEx(
        hProcess: *mut c_void,
        lpAddress: *mut c_void,
        dwSize: usize,
        flNewProtect: u32,
        lpflOldProtect: *mut u32,
    ) -> i32;
    fn FlushInstructionCache(
        hProcess: *mut c_void,
        lpBaseAddress: *const c_void,
        dwSize: usize,
    ) -> i32;
    fn ResumeThread(hThread: *mut c_void) -> u32;
    fn CloseHandle(hObject: *mut c_void) -> i32;
    fn GetLastError() -> u32;
}

pub fn to_wide_z(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(core::iter::once(0)).collect()
}

fn timestamp_prefix() -> String {
    let mut st = SystemTime {
        year: 0,
        month: 0,
        day_of_week: 0,
        day: 0,
        hour: 0,
        minute: 0,
        second: 0,
        milliseconds: 0,
    };
    unsafe { GetLocalTime(&mut st) };
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}",
        st.year, st.month, st.day, st.hour, st.minute, st.second
    )
}

pub fn write_log(log_path: Option<&Path>, line: &str) {
    let Some(path) = log_path else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(meta) = std::fs::metadata(path)
        && meta.len() > MAX_LOG_BYTES
        && let Ok(mut f) = OpenOptions::new().read(true).open(path)
    {
        let start = meta.len().saturating_sub(KEEP_LOG_BYTES);
        if f.seek(SeekFrom::Start(start)).is_ok() {
            let mut tail = Vec::new();
            if f.read_to_end(&mut tail).is_ok() {
                let aligned = tail
                    .iter()
                    .position(|&b| b == b'\n')
                    .map_or(0, |p| p + 1);
                let _ = std::fs::write(path, &tail[aligned..]);
            }
        }
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{} {}", timestamp_prefix(), line);
    }
}

/// Enumerate process IDs matching `exe_name` (case-insensitive, e.g. `Telegram.exe`).
pub fn list_processes_by_name(exe_name: &str) -> Vec<u32> {
    let snap = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) };
    if snap.is_null() || snap as isize == INVALID_HANDLE_VALUE {
        return Vec::new();
    }
    let mut entry: ProcessEntry32W = unsafe { core::mem::zeroed() };
    entry.dw_size = size_of::<ProcessEntry32W>() as u32;

    let mut pids = Vec::new();
    let mut ok = unsafe { Process32FirstW(snap, &mut entry) };
    while ok != 0 {
        let end = entry
            .sz_exe_file
            .iter()
            .position(|&c| c == 0)
            .unwrap_or(entry.sz_exe_file.len());
        let name = String::from_utf16_lossy(&entry.sz_exe_file[..end]);
        if name.eq_ignore_ascii_case(exe_name) {
            pids.push(entry.th32_process_id);
        }
        entry.dw_size = size_of::<ProcessEntry32W>() as u32;
        ok = unsafe { Process32NextW(snap, &mut entry) };
    }
    unsafe { CloseHandle(snap) };
    pids
}

/// Terminate a running process by PID and wait briefly for exit.
pub fn terminate_process_by_pid(pid: u32) -> Result<(), String> {
    let h_proc = unsafe { OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, 0, pid) };
    if h_proc.is_null() {
        return Err(format!("OpenProcess(TERMINATE, pid={pid}) failed: err={}", unsafe {
            GetLastError()
        }));
    }
    let ok = unsafe { TerminateProcess(h_proc, 0) };
    if ok != 0 {
        unsafe { WaitForSingleObject(h_proc, 5000) };
    }
    let err = unsafe { GetLastError() };
    unsafe { CloseHandle(h_proc) };
    if ok == 0 {
        return Err(format!("TerminateProcess(pid={pid}) failed: err={err}"));
    }
    Ok(())
}

/// Read the remote process's `PEB.ImageBaseAddress` (offset `0x10` on x64).
pub unsafe fn read_process_image_base(h_process: *mut c_void) -> Result<u64, String> {
    let mut pbi = ProcessBasicInformation {
        exit_status: 0,
        peb_base_address: 0,
        affinity_mask: 0,
        base_priority: 0,
        unique_process_id: 0,
        inherited_from_unique_process_id: 0,
    };
    let mut ret_len = 0u32;
    let status = unsafe {
        NtQueryInformationProcess(
            h_process,
            0,
            (&raw mut pbi).cast(),
            size_of::<ProcessBasicInformation>() as u32,
            &mut ret_len,
        )
    };
    if status < 0 || pbi.peb_base_address == 0 {
        return Err(format!("NtQueryInformationProcess failed: status={status:#x}"));
    }

    let mut image_base = 0u64;
    let mut read = 0usize;
    let ok = unsafe {
        ReadProcessMemory(
            h_process,
            (pbi.peb_base_address + 0x10) as *const c_void,
            (&raw mut image_base).cast(),
            size_of::<u64>(),
            &mut read,
        )
    };
    if ok == 0 || read != size_of::<u64>() || image_base == 0 {
        return Err(format!(
            "ReadProcessMemory(PEB+0x10) failed: err={}",
            unsafe { GetLastError() }
        ));
    }
    Ok(image_base)
}

/// Inspect a live process's memory at `call_site_rva` to check whether the stub patch is active.
pub unsafe fn is_process_call_site_patched(
    h_process: *mut c_void,
    image_base: u64,
    plan: &PatchPlan,
) -> Result<bool, String> {
    let call_site_va = image_base + plan.call_site_rva as u64;
    let mut buf = [0u8; 5];
    let mut read = 0usize;
    let ok = unsafe {
        ReadProcessMemory(
            h_process,
            call_site_va as *const c_void,
            buf.as_mut_ptr().cast(),
            buf.len(),
            &mut read,
        )
    };
    if ok == 0 || read != buf.len() {
        return Err(format!(
            "ReadProcessMemory(call_site) failed: err={}",
            unsafe { GetLastError() }
        ));
    }
    if buf[0] != 0xE8 {
        return Err(format!("unexpected opcode {:#x} at call_site", buf[0]));
    }
    let rel32 = i32::from_le_bytes(buf[1..5].try_into().unwrap()) as i64;
    let target_rva = (plan.call_site_rva as i64) + 5 + rel32;
    Ok(target_rva != plan.am_premium_rva as i64)
}

/// Check live memory of a running PID against `PatchPlan`.
pub fn verify_pid_patched(pid: u32, plan: &PatchPlan) -> Result<bool, String> {
    let h_proc = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, 0, pid) };
    if h_proc.is_null() {
        return Err(format!("OpenProcess(pid={pid}) failed: err={}", unsafe {
            GetLastError()
        }));
    }
    let res = unsafe {
        read_process_image_base(h_proc)
            .and_then(|base| is_process_call_site_patched(h_proc, base, plan))
    };
    unsafe { CloseHandle(h_proc) };
    res
}

unsafe fn alloc_near_remote(h_process: *mut c_void, target_va: u64, size: usize) -> *mut c_void {
    let step: u64 = 0x10000;
    let max_dist: u64 = 0x7000_0000;
    let mut offset = step;
    while offset < max_dist {
        if let Some(above) = target_va.checked_add(offset) {
            let p = unsafe {
                VirtualAllocEx(
                    h_process,
                    above as *mut c_void,
                    size,
                    MEM_COMMIT | MEM_RESERVE,
                    PAGE_EXECUTE_READWRITE,
                )
            };
            if !p.is_null() {
                return p;
            }
        }
        if let Some(below) = target_va.checked_sub(offset)
            && below > 0x10000
        {
            let p = unsafe {
                VirtualAllocEx(
                    h_process,
                    below as *mut c_void,
                    size,
                    MEM_COMMIT | MEM_RESERVE,
                    PAGE_EXECUTE_READWRITE,
                )
            };
            if !p.is_null() {
                return p;
            }
        }
        offset += step;
    }
    core::ptr::null_mut()
}

/// Patch the `call Data::AmPremiumValue` inside `TranslateTracker::setup()` in a remote process.
pub unsafe fn patch_remote_call_site(
    h_process: *mut c_void,
    image_base: u64,
    plan: &PatchPlan,
) -> Result<u64, String> {
    let call_site_va = image_base + plan.call_site_rva as u64;
    let mut orig = [0u8; 5];
    let mut read = 0usize;
    let ok_read = unsafe {
        ReadProcessMemory(
            h_process,
            call_site_va as *const c_void,
            orig.as_mut_ptr().cast(),
            orig.len(),
            &mut read,
        )
    };
    if ok_read == 0 || read != orig.len() {
        return Err(format!(
            "ReadProcessMemory(call_site) failed: err={}",
            unsafe { GetLastError() }
        ));
    }
    if orig[0] != 0xE8 {
        return Err(format!("expected 0xE8 at call site, found {:#x}", orig[0]));
    }

    let stub_ptr = unsafe { alloc_near_remote(h_process, call_site_va, 4096) };
    if stub_ptr.is_null() {
        return Err("VirtualAllocEx near call_site failed".to_owned());
    }
    let stub_va = stub_ptr as u64;
    let vtable_va = image_base + plan.rpl_single_vtable_rva as u64;
    let stub_bytes = build_rpl_single_true_stub(vtable_va);

    let mut written = 0usize;
    if unsafe {
        WriteProcessMemory(
            h_process,
            stub_ptr,
            stub_bytes.as_ptr().cast(),
            stub_bytes.len(),
            &mut written,
        )
    } == 0
        || written != stub_bytes.len()
    {
        return Err(format!("WriteProcessMemory(stub) failed: err={}", unsafe {
            GetLastError()
        }));
    }

    let mut old_stub_prot = 0u32;
    unsafe {
        VirtualProtectEx(h_process, stub_ptr, 4096, PAGE_EXECUTE_READ, &mut old_stub_prot);
    }

    let rel = (stub_va as isize as i64) - (call_site_va as i64 + 5);
    let rel32 = i32::try_from(rel).map_err(|_| "allocated stub out of i32 call range".to_owned())?;
    let mut patch = [0u8; 5];
    patch[0] = 0xE8;
    patch[1..5].copy_from_slice(&rel32.to_le_bytes());

    let mut old_prot = 0u32;
    if unsafe {
        VirtualProtectEx(
            h_process,
            call_site_va as *mut c_void,
            5,
            PAGE_EXECUTE_READWRITE,
            &mut old_prot,
        )
    } == 0
    {
        return Err(format!(
            "VirtualProtectEx(call_site) failed: err={}",
            unsafe { GetLastError() }
        ));
    }

    let ok_write = unsafe {
        WriteProcessMemory(
            h_process,
            call_site_va as *mut c_void,
            patch.as_ptr().cast(),
            5,
            &mut written,
        )
    };
    let mut dummy = 0u32;
    unsafe {
        FlushInstructionCache(h_process, call_site_va as *const c_void, 5);
        VirtualProtectEx(h_process, call_site_va as *mut c_void, 5, old_prot, &mut dummy);
    }
    if ok_write == 0 || written != 5 {
        return Err(format!(
            "WriteProcessMemory(call_site) failed: err={}",
            unsafe { GetLastError() }
        ));
    }

    Ok(stub_va)
}

/// Spawn `exe_path` suspended, patch `TranslateTracker::setup()`, and resume execution.
pub fn launch_and_patch_telegram(
    exe_path: &Path,
    extra_args: &str,
    log_path: Option<&Path>,
) -> Result<u32, String> {
    let exe_bytes = std::fs::read(exe_path)
        .map_err(|e| format!("failed to read {}: {e}", exe_path.display()))?;
    let plan_opt = find_translate_tracker_patch(&exe_bytes);

    let cmdline_str = if extra_args.trim().is_empty() {
        format!("\"{}\"", exe_path.display())
    } else {
        format!("\"{}\" {}", exe_path.display(), extra_args.trim())
    };
    let exe_w = to_wide_z(&exe_path.to_string_lossy());
    let mut cmd_w = to_wide_z(&cmdline_str);
    let cwd_w = exe_path
        .parent()
        .map(|p| to_wide_z(&p.to_string_lossy()));

    let mut si: StartupInfoW = unsafe { core::mem::zeroed() };
    si.cb = size_of::<StartupInfoW>() as u32;
    si.dw_flags = 1; // STARTF_USESHOWWINDOW
    si.w_show_window = 1; // SW_SHOWNORMAL
    let mut pi: ProcessInformation = unsafe { core::mem::zeroed() };

    let ok = unsafe {
        CreateProcessW(
            exe_w.as_ptr(),
            cmd_w.as_mut_ptr(),
            core::ptr::null_mut(),
            core::ptr::null_mut(),
            0,
            CREATE_SUSPENDED,
            core::ptr::null_mut(),
            cwd_w.as_ref().map_or(core::ptr::null(), |v| v.as_ptr()),
            &si,
            &mut pi,
        )
    };
    if ok == 0 {
        let err = unsafe { GetLastError() };
        let msg = format!("[error] CreateProcessW failed: err={err}");
        write_log(log_path, &msg);
        return Err(msg);
    }

    let result = match plan_opt {
        Ok(plan) => unsafe {
            read_process_image_base(pi.h_process).and_then(|image_base| {
                patch_remote_call_site(pi.h_process, image_base, &plan).map(|stub_va| {
                    write_log(
                        log_path,
                        &format!(
                            "[ok] patched pid={} base={:#x} call_site={:#x} stub={:#x} vtable={:#x}",
                            pi.dw_process_id,
                            image_base,
                            image_base + plan.call_site_rva as u64,
                            stub_va,
                            image_base + plan.rpl_single_vtable_rva as u64,
                        ),
                    );
                })
            })
        },
        Err(err) => {
            write_log(
                log_path,
                &format!("[warn] signature mismatch, launching unpatched: {err}"),
            );
            Ok(())
        }
    };

    if let Err(ref e) = result {
        write_log(log_path, &format!("[warn] patch skipped: {e}"));
    }

    unsafe {
        ResumeThread(pi.h_thread);
        CloseHandle(pi.h_thread);
        CloseHandle(pi.h_process);
    }
    Ok(pi.dw_process_id)
}
