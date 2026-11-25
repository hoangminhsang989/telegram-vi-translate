#![allow(non_snake_case)]

pub mod pe_patch;
#[cfg(target_os = "windows")]
pub mod remote_patch;

#[cfg(target_os = "windows")]
mod shim_runtime {
    use core::arch::global_asm;
    use core::ffi::c_void;
    use core::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::fs::OpenOptions;
    use std::io::Write;
    use std::path::PathBuf;

    use crate::pe_patch::{build_rpl_single_true_stub, find_translate_tracker_patch_mapped};

    include!(concat!(env!("OUT_DIR"), "/mscms_forwards.rs"));

    const DLL_PROCESS_ATTACH: u32 = 1;
    const MEM_COMMIT: u32 = 0x1000;
    const MEM_RESERVE: u32 = 0x2000;
    const PAGE_EXECUTE_READ: u32 = 0x20;
    const PAGE_EXECUTE_READWRITE: u32 = 0x40;

    static SLOTS: [AtomicUsize; EXPORT_COUNT] = [const { AtomicUsize::new(0) }; EXPORT_COUNT];
    static REAL_DLL: AtomicUsize = AtomicUsize::new(0);
    static PATCH_STARTED: AtomicBool = AtomicBool::new(false);

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn DisableThreadLibraryCalls(hLibModule: *mut c_void) -> i32;
        fn LoadLibraryW(lpLibFileName: *const u16) -> *mut c_void;
        fn GetProcAddress(hModule: *mut c_void, lpProcName: *const u8) -> *const c_void;
        fn GetModuleHandleW(lpModuleName: *const u16) -> *mut c_void;
        fn GetModuleFileNameW(hModule: *mut c_void, lpFilename: *mut u16, nSize: u32) -> u32;
        fn CreateThread(
            lpThreadAttributes: *mut c_void,
            dwStackSize: usize,
            lpStartAddress: unsafe extern "system" fn(*mut c_void) -> u32,
            lpParameter: *mut c_void,
            dwCreationFlags: u32,
            lpThreadId: *mut u32,
        ) -> *mut c_void;
        fn CloseHandle(hObject: *mut c_void) -> i32;
        fn GetCurrentProcess() -> *mut c_void;
        fn VirtualAlloc(
            lpAddress: *mut c_void,
            dwSize: usize,
            flAllocationType: u32,
            flProtect: u32,
        ) -> *mut c_void;
        fn VirtualProtect(
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
    }

    global_asm!(
        r#"
.globl mscms_dispatch_trampoline
mscms_dispatch_trampoline:
    mov eax, r10d
    lea r11, [rip + {slots}]
    mov rax, qword ptr [r11 + rax * 8]
    test rax, rax
    jz 2f
    jmp rax
2:
    push rcx
    push rdx
    push r8
    push r9
    push r10
    sub rsp, 0x60
    movdqu xmmword ptr [rsp + 0x20], xmm0
    movdqu xmmword ptr [rsp + 0x30], xmm1
    movdqu xmmword ptr [rsp + 0x40], xmm2
    movdqu xmmword ptr [rsp + 0x50], xmm3
    mov ecx, r10d
    call {resolver}
    movdqu xmm0, xmmword ptr [rsp + 0x20]
    movdqu xmm1, xmmword ptr [rsp + 0x30]
    movdqu xmm2, xmmword ptr [rsp + 0x40]
    movdqu xmm3, xmmword ptr [rsp + 0x50]
    add rsp, 0x60
    pop r10
    pop r9
    pop r8
    pop rdx
    pop rcx
    test rax, rax
    jz 3f
    jmp rax
3:
    xor eax, eax
    ret
"#,
        slots = sym SLOTS,
        resolver = sym resolve_mscms_slot,
    );

    fn log_line(msg: &str) {
        let base = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(r"C:\Users\Public"));
        let dir = base.join("TelegramViShim");
        let _ = std::fs::create_dir_all(&dir);
        if let Ok(mut f) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("shim.log"))
        {
            let _ = writeln!(f, "{msg}");
        }
    }

    fn ensure_real_mscms() -> *mut c_void {
        let existing = REAL_DLL.load(Ordering::Acquire);
        if existing != 0 {
            return existing as *mut c_void;
        }
        let path: Vec<u16> = "C:\\Windows\\System32\\mscms.dll\0"
            .encode_utf16()
            .collect();
        let h = unsafe { LoadLibraryW(path.as_ptr()) };
        if !h.is_null() {
            for (i, name) in EXPORT_NAMES.iter().enumerate() {
                let proc_ptr = unsafe { GetProcAddress(h, name.as_ptr()) };
                if !proc_ptr.is_null() {
                    SLOTS[i].store(proc_ptr as usize, Ordering::Release);
                }
            }
            let _ = REAL_DLL.compare_exchange(0, h as usize, Ordering::AcqRel, Ordering::Acquire);
        }
        REAL_DLL.load(Ordering::Acquire) as *mut c_void
    }

    unsafe extern "C" fn resolve_mscms_slot(index: usize) -> usize {
        if index >= EXPORT_COUNT {
            return 0;
        }
        let _ = ensure_real_mscms();
        SLOTS[index].load(Ordering::Acquire)
    }

    fn read_image_size(base: usize) -> Option<usize> {
        unsafe {
            let b = core::slice::from_raw_parts(base as *const u8, 0x400);
            if b.get(0..2) != Some(b"MZ") {
                return None;
            }
            let pe_off = u32::from_le_bytes(b[0x3C..0x40].try_into().ok()?) as usize;
            let opt_off = pe_off + 24;
            if opt_off + 64 > b.len() {
                return None;
            }
            Some(u32::from_le_bytes(b[opt_off + 56..opt_off + 60].try_into().ok()?) as usize)
        }
    }

    unsafe fn alloc_near(target_va: usize, size: usize) -> *mut c_void {
        let step: usize = 0x10000;
        let max_dist: usize = 0x7000_0000;
        let mut offset = step;
        while offset < max_dist {
            if let Some(above) = target_va.checked_add(offset) {
                let p = unsafe {
                    VirtualAlloc(
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
            if let Some(below) = target_va.checked_sub(offset) && below > 0x10000 {
                let p = unsafe {
                    VirtualAlloc(
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

    unsafe extern "system" fn patch_worker(_param: *mut c_void) -> u32 {
        let _ = ensure_real_mscms();

        let h_exe = unsafe { GetModuleHandleW(core::ptr::null()) };
        if h_exe.is_null() {
            log_line("[error] GetModuleHandleW(null) returned null");
            return 1;
        }
        let base = h_exe as usize;
        let Some(image_size) = read_image_size(base) else {
            log_line("[error] failed to read SizeOfImage");
            return 1;
        };

        let module_slice = unsafe { core::slice::from_raw_parts(base as *const u8, image_size) };
        let plan = match find_translate_tracker_patch_mapped(module_slice) {
            Ok(p) => p,
            Err(err) => {
                log_line(&format!("[warn] signature mismatch, patch skipped: {err}"));
                return 0;
            }
        };

        let call_site_va = base + plan.call_site_rva;
        let stub_va = unsafe { alloc_near(call_site_va, 4096) };
        if stub_va.is_null() {
            log_line("[error] VirtualAlloc near call_site failed");
            return 1;
        }

        let vtable_va = (base + plan.rpl_single_vtable_rva) as u64;
        let stub_bytes = build_rpl_single_true_stub(vtable_va);
        unsafe {
            core::ptr::copy_nonoverlapping(stub_bytes.as_ptr(), stub_va as *mut u8, stub_bytes.len());
            let mut old_stub_prot = 0u32;
            VirtualProtect(stub_va, 4096, PAGE_EXECUTE_READ, &mut old_stub_prot);
        }

        let rel = (stub_va as isize) - (call_site_va as isize + 5);
        let Ok(rel32) = i32::try_from(rel) else {
            log_line("[error] allocated stub out of i32 call range");
            return 1;
        };

        unsafe {
            let site = call_site_va as *mut u8;
            if *site != 0xE8 {
                log_line("[warn] expected E8 opcode at call site, skipping patch");
                return 0;
            }
            let mut old_prot = 0u32;
            if VirtualProtect(site as *mut c_void, 5, PAGE_EXECUTE_READWRITE, &mut old_prot) == 0 {
                log_line("[error] VirtualProtect failed at call site");
                return 1;
            }
            let mut patch = [0u8; 5];
            patch[0] = 0xE8;
            patch[1..5].copy_from_slice(&rel32.to_le_bytes());
            core::ptr::copy_nonoverlapping(patch.as_ptr(), site, 5);
            FlushInstructionCache(GetCurrentProcess(), site as *const c_void, 5);
            let mut dummy = 0u32;
            VirtualProtect(site as *mut c_void, 5, old_prot, &mut dummy);
        }

        log_line(&format!(
            "[ok] patched TranslateTracker::setup at call_site={:#x} -> stub={:#x} (vtable={:#x})",
            call_site_va, stub_va as usize, vtable_va
        ));
        0
    }

    fn is_telegram_process() -> bool {
        let mut buf = [0u16; 512];
        let len = unsafe { GetModuleFileNameW(core::ptr::null_mut(), buf.as_mut_ptr(), buf.len() as u32) };
        if len == 0 {
            return false;
        }
        let path = String::from_utf16_lossy(&buf[..len as usize]);
        path.rsplit(['\\', '/'])
            .next()
            .is_some_and(|name| name.eq_ignore_ascii_case("Telegram.exe"))
    }

    #[unsafe(no_mangle)]
    unsafe extern "system" fn DllMain(
        hinst_dll: *mut c_void,
        fdw_reason: u32,
        _lpv_reserved: *mut c_void,
    ) -> i32 {
        if fdw_reason == DLL_PROCESS_ATTACH {
            unsafe { DisableThreadLibraryCalls(hinst_dll) };
            if is_telegram_process() && !PATCH_STARTED.swap(true, Ordering::AcqRel) {
                let th = unsafe {
                    CreateThread(
                        core::ptr::null_mut(),
                        0,
                        patch_worker,
                        core::ptr::null_mut(),
                        0,
                        core::ptr::null_mut(),
                    )
                };
                if !th.is_null() {
                    unsafe { CloseHandle(th) };
                }
            }
        }
        1
    }
}
