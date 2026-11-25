use std::ffi::c_void;

use mscms::pe_patch::PatchPlan;
use mscms::remote_patch::{
    ProcessEntry32W, is_process_call_site_patched, list_processes_by_name, patch_remote_call_site,
    read_process_image_base,
};

#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetCurrentProcess() -> *mut c_void;
    fn GetCurrentProcessId() -> u32;
    fn GetModuleHandleW(lpModuleName: *const u16) -> *mut c_void;
    fn VirtualAlloc(
        lpAddress: *mut c_void,
        dwSize: usize,
        flAllocationType: u32,
        flProtect: u32,
    ) -> *mut c_void;
    fn VirtualFree(lpAddress: *mut c_void, dwSize: usize, dwFreeType: u32) -> i32;
}

#[test]
fn reads_current_process_image_base_via_peb() {
    let base = unsafe { read_process_image_base(GetCurrentProcess()) }.expect("read PEB image base");
    let expected = unsafe { GetModuleHandleW(std::ptr::null()) } as u64;
    assert_eq!(base, expected);
}

#[test]
fn process_entry_struct_matches_win32_x64_size_and_lists_current_exe() {
    assert_eq!(std::mem::size_of::<ProcessEntry32W>(), 568);
    let current_exe = std::env::current_exe().unwrap();
    let exe_name = current_exe.file_name().unwrap().to_string_lossy();
    let pids = list_processes_by_name(&exe_name);
    let my_pid = unsafe { GetCurrentProcessId() };
    assert!(pids.contains(&my_pid), "expected pid {my_pid} in {pids:?}");
}

#[test]
fn patches_remote_call_site_writes_executable_stub_and_verifies_state() {
    let page = unsafe { VirtualAlloc(std::ptr::null_mut(), 4096, 0x3000, 0x40) } as *mut u8;
    assert!(!page.is_null());
    unsafe {
        std::ptr::write_bytes(page, 0x90, 64);
        // Simulate an E8 rel32 call instruction at offset 16 targeting RVA 0x400 (disp = 0x400 - 21)
        *page.add(16) = 0xE8;
        let orig_rel = (0x400i32 - 21i32).to_le_bytes();
        std::ptr::copy_nonoverlapping(orig_rel.as_ptr(), page.add(17), 4);
    }

    let synthetic_base = page as u64;
    let plan = PatchPlan {
        image_base: synthetic_base,
        call_site_rva: 16,
        am_premium_rva: 0x400,
        rpl_single_vtable_rva: 0x2000,
    };

    let before = unsafe { is_process_call_site_patched(GetCurrentProcess(), synthetic_base, &plan) }
        .expect("check unpatched");
    assert!(!before);

    let stub_va = unsafe { patch_remote_call_site(GetCurrentProcess(), synthetic_base, &plan) }
        .expect("patch remote call site");

    let after = unsafe { is_process_call_site_patched(GetCurrentProcess(), synthetic_base, &plan) }
        .expect("check patched");
    assert!(after);

    let rel = unsafe { i32::from_le_bytes(*(page.add(17) as *const [u8; 4])) };
    let target = ((synthetic_base + 16 + 5) as i64 + rel as i64) as u64;
    assert_eq!(target, stub_va);

    let stub_bytes = unsafe { std::slice::from_raw_parts(stub_va as *const u8, 25) };
    assert_eq!(&stub_bytes[0..2], &[0x48, 0xB8]);
    assert_eq!(
        u64::from_le_bytes(stub_bytes[2..10].try_into().unwrap()),
        synthetic_base + 0x2000
    );
    assert_eq!(stub_bytes[24], 0xC3);

    unsafe {
        VirtualFree(stub_va as *mut c_void, 0, 0x8000);
        VirtualFree(page as *mut c_void, 0, 0x8000);
    }
}
