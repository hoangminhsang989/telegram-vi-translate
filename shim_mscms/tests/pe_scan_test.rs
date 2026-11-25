use std::path::Path;

use mscms::pe_patch::{build_rpl_single_true_stub, find_translate_tracker_patch};

#[test]
fn rejects_non_matching_or_truncated_pe_safely() {
    let garbage = vec![0u8; 4096];
    assert!(find_translate_tracker_patch(&garbage).is_err());
}

#[test]
fn stub_encodes_vtable_and_true_flag() {
    let stub = build_rpl_single_true_stub(0x14932eca0);
    assert_eq!(&stub[0..2], &[0x48, 0xB8]);
    assert_eq!(u64::from_le_bytes(stub[2..10].try_into().unwrap()), 0x14932eca0);
    assert_eq!(stub[stub.len() - 1], 0xC3);
}

#[test]
fn locates_translate_tracker_call_in_installed_telegram_exe() {
    let tg = Path::new(r"C:\Users\HMS-PCC\AppData\Roaming\Telegram Desktop\Telegram.exe");
    if !tg.exists() {
        return;
    }
    let bytes = std::fs::read(tg).unwrap();
    let plan = find_translate_tracker_patch(&bytes).expect("must find TranslateTracker::setup gate");
    assert!(plan.call_site_rva > 0x1000);
    assert!(plan.rpl_single_vtable_rva > 0x1000);
}
