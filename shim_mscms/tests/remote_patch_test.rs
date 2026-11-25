use std::ffi::c_void;

use mscms::pe_patch::PatchPlan;
use mscms::remote_patch::{patch_remote_call_site, read_process_image_base};

#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetCurrentProcess() -> *mut c_void;
    fn GetModuleHandleW(lpModuleName: *const u16) -> *mut c_void;
    fn VirtualAlloc(lpAddress: *mut c_void, dwSize: usize, flAllocationType: u32, flProtect: u32) -> *mut c_void;
    fn VirtualFree(lpAddress: *mut c_void, dwSize: usize, dwFreeType: u32) -> i32;
}

#[test]
fn reads_current_process_image_base_via_peb() {
    let base = unsafe { read_process_image_base(GetCurrentProcess()) }.expect("read PEB image base");
    let expected = unsafe { GetModuleHandleW(std::ptr::null()) } as u64;
    assert_eq!(base, expected);
}

#[test]
fn patches_remote_call_site_and_writes_executable_stub() {
    let page = unsafe { VirtualAlloc(std::ptr::null_mut(), 4096, 0x3000, 0x40) } as *mut u8;
    assert!(!page.is_null());
    unsafe {
        std::ptr::write_bytes(page, 0x90, 64);
        // Simulate an E8 rel32 call instruction at offset 16 inside our synthetic module region
        *page.add(16) = 0xE8;
        std::ptr::write_bytes(page.add(17), 0, 4);
    }

    let synthetic_base = page as u64;
    let plan = PatchPlan {
        image_base: synthetic_base,
        call_site_rva: 16,
        am_premium_rva: 0,
        rpl_single_vtable_rva: 0x2000,
    };
    let stub_va = unsafe { patch_remote_call_site(GetCurrentProcess(), synthetic_base, &plan) }
        .expect("patch remote call site");

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
