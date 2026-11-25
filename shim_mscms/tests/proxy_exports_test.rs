use std::collections::BTreeSet;
use std::path::Path;

use mscms::pe_patch::read_export_names;

#[test]
fn built_shim_exports_match_system32_mscms_123_functions() {
    let sys_dll = Path::new(r"C:\Windows\System32\mscms.dll");
    let built_dll = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/release/mscms.dll");
    assert!(built_dll.exists(), "Run cargo build --release first");

    let sys_exports: BTreeSet<String> = read_export_names(&std::fs::read(sys_dll).unwrap())
        .unwrap()
        .into_iter()
        .collect();
    let shim_exports: BTreeSet<String> = read_export_names(&std::fs::read(&built_dll).unwrap())
        .unwrap()
        .into_iter()
        .collect();
    assert_eq!(sys_exports.len(), 123);
    assert!(
        sys_exports.is_subset(&shim_exports),
        "missing exports: {:?}",
        sys_exports.difference(&shim_exports).collect::<Vec<_>>()
    );
    let extra: BTreeSet<String> = shim_exports.difference(&sys_exports).cloned().collect();
    assert!(
        extra.is_empty() || (extra.len() == 1 && extra.contains("DllMain")),
        "unexpected extra exports: {:?}",
        extra
    );
}
