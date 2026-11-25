use std::env;
use std::fs;
use std::path::Path;

fn read_u16(b: &[u8], off: usize) -> u16 {
    u16::from_le_bytes(b[off..off + 2].try_into().unwrap())
}

fn read_u32(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes(b[off..off + 4].try_into().unwrap())
}

fn parse_exports_with_ordinals(bytes: &[u8]) -> Vec<(String, u16)> {
    let pe_off = read_u32(bytes, 0x3C) as usize;
    let nsec = read_u16(bytes, pe_off + 6) as usize;
    let opsz = read_u16(bytes, pe_off + 20) as usize;
    let opt_off = pe_off + 24;
    let sec_off = opt_off + opsz;

    let mut secs = Vec::with_capacity(nsec);
    for i in 0..nsec {
        let o = sec_off + i * 40;
        let vs = read_u32(bytes, o + 8) as usize;
        let va = read_u32(bytes, o + 12) as usize;
        let rs = read_u32(bytes, o + 16) as usize;
        let ra = read_u32(bytes, o + 20) as usize;
        secs.push((va, vs, ra, rs));
    }

    let rva_to_raw = |rva: usize| -> usize {
        for &(va, vs, ra, rs) in &secs {
            if rva >= va && rva < va + vs.max(rs) {
                return ra + (rva - va);
            }
        }
        panic!("RVA out of range: {rva:#x}");
    };

    let exp_rva = read_u32(bytes, opt_off + 112) as usize;
    let er = rva_to_raw(exp_rva);
    let base = read_u32(bytes, er + 16) as u16;
    let n_names = read_u32(bytes, er + 24) as usize;
    let addr_names = rva_to_raw(read_u32(bytes, er + 32) as usize);
    let addr_ords = rva_to_raw(read_u32(bytes, er + 36) as usize);

    let mut exports = Vec::with_capacity(n_names);
    for i in 0..n_names {
        let name_rva = read_u32(bytes, addr_names + i * 4) as usize;
        let ord_idx = read_u16(bytes, addr_ords + i * 2);
        let nr = rva_to_raw(name_rva);
        let end = bytes[nr..].iter().position(|&b| b == 0).unwrap();
        let name = std::str::from_utf8(&bytes[nr..nr + end]).unwrap().to_owned();
        exports.push((name, base + ord_idx));
    }
    exports
}

fn main() {
    let sys_dll = Path::new(r"C:\Windows\System32\mscms.dll");
    println!("cargo:rerun-if-changed={}", sys_dll.display());

    let bytes = fs::read(sys_dll).expect("failed to read C:\\Windows\\System32\\mscms.dll");
    let exports = parse_exports_with_ordinals(&bytes);
    assert_eq!(exports.len(), 123, "unexpected mscms.dll export count");

    let out_dir = env::var("OUT_DIR").unwrap();
    let out_path = Path::new(&out_dir);

    let mut def = String::from("LIBRARY mscms\nEXPORTS\n");
    let mut rs = format!("pub const EXPORT_COUNT: usize = {};\n", exports.len());
    rs.push_str("pub static EXPORT_NAMES: [&[u8]; EXPORT_COUNT] = [\n");
    for (name, ord) in &exports {
        def.push_str(&format!("  {name}=_mscms_fwd_{name} @{ord}\n"));
        rs.push_str(&format!("    b\"{name}\\0\",\n"));
    }
    rs.push_str("];\n\n");

    let mut asm = String::new();
    for (idx, (name, _)) in exports.iter().enumerate() {
        asm.push_str(&format!(
            ".globl _mscms_fwd_{name}\n\
             _mscms_fwd_{name}:\n\
             \tmov r10d, {idx}\n\
             \tjmp mscms_dispatch_trampoline\n"
        ));
    }
    rs.push_str("core::arch::global_asm!(r#\"\n");
    rs.push_str(&asm);
    rs.push_str("\"#);\n");

    let def_file = out_path.join("mscms_exports.def");
    fs::write(&def_file, def).unwrap();
    fs::write(out_path.join("mscms_forwards.rs"), rs).unwrap();

    println!("cargo:rustc-cdylib-link-arg=/DEF:{}", def_file.display());
}
