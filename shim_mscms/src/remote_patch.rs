use core::ffi::c_void;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;

use crate::pe_patch::{PatchPlan, build_rpl_single_true_stub, find_translate_tracker_patch};

const CREATE_SUSPENDED: u32 = 0x0000_0004;
const MEM_COMMIT: u32 = 0x1000;
const MEM_RESERVE: u32 = 0x2000;
const PAGE_EXECUTE_READ: u32 = 0x20;
const PAGE_EXECUTE_READWRITE: u32 = 0x40;

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

fn to_wide_z(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(core::iter::once(0)).collect()
}

fn write_log(log_path: Option<&Path>, line: &str) {
    let Some(path) = log_path else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{line}");
    }
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

    let rel = (stub_va as i64) - (call_site_va as i64 + 5);
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
    let mut desktop_w = to_wide_z(r"Winsta0\Default");
    si.lp_desktop = desktop_w.as_mut_ptr();
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
