use std::convert::TryInto;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PatchPlan {
    pub image_base: u64,
    pub call_site_rva: usize,
    pub am_premium_rva: usize,
    pub rpl_single_vtable_rva: usize,
}

#[derive(Debug, Clone, Copy)]
struct Section {
    name: [u8; 8],
    virtual_size: usize,
    virtual_addr: usize,
    raw_size: usize,
    raw_addr: usize,
    characteristics: u32,
}

struct PeImage<'a> {
    bytes: &'a [u8],
    mapped: bool,
    image_base: u64,
    sections: Vec<Section>,
    export_dir_rva: usize,
}

fn read_u16(b: &[u8], off: usize) -> Option<u16> {
    b.get(off..off + 2)?.try_into().ok().map(u16::from_le_bytes)
}

fn read_u32(b: &[u8], off: usize) -> Option<u32> {
    b.get(off..off + 4)?.try_into().ok().map(u32::from_le_bytes)
}

fn read_i32(b: &[u8], off: usize) -> Option<i32> {
    b.get(off..off + 4)?.try_into().ok().map(i32::from_le_bytes)
}

fn read_u64(b: &[u8], off: usize) -> Option<u64> {
    b.get(off..off + 8)?.try_into().ok().map(u64::from_le_bytes)
}

impl<'a> PeImage<'a> {
    fn parse(bytes: &'a [u8], mapped: bool) -> Result<Self, &'static str> {
        if bytes.len() < 0x100 || bytes.get(0..2) != Some(b"MZ") {
            return Err("not a MZ executable");
        }
        let pe_off = read_u32(bytes, 0x3C).ok_or("missing PE offset")? as usize;
        if bytes.get(pe_off..pe_off + 4) != Some(b"PE\0\0") {
            return Err("invalid PE signature");
        }
        let machine = read_u16(bytes, pe_off + 4).ok_or("missing machine")?;
        if machine != 0x8664 {
            return Err("unsupported architecture (expected x64)");
        }
        let num_sections = read_u16(bytes, pe_off + 6).ok_or("missing section count")? as usize;
        let opt_size = read_u16(bytes, pe_off + 20).ok_or("missing optional header size")? as usize;
        let opt_off = pe_off + 24;
        if opt_size < 120 || bytes.len() < opt_off + opt_size {
            return Err("truncated optional header");
        }
        let magic = read_u16(bytes, opt_off).ok_or("missing optional magic")?;
        if magic != 0x020B {
            return Err("expected PE32+");
        }
        let image_base = read_u64(bytes, opt_off + 24).ok_or("missing ImageBase")?;
        let export_dir_rva = read_u32(bytes, opt_off + 112).unwrap_or(0) as usize;

        let sec_table = opt_off + opt_size;
        if bytes.len() < sec_table + num_sections * 40 {
            return Err("truncated section table");
        }
        let mut sections = Vec::with_capacity(num_sections);
        for i in 0..num_sections {
            let o = sec_table + i * 40;
            let mut name = [0u8; 8];
            name.copy_from_slice(&bytes[o..o + 8]);
            sections.push(Section {
                name,
                virtual_size: read_u32(bytes, o + 8).unwrap_or(0) as usize,
                virtual_addr: read_u32(bytes, o + 12).unwrap_or(0) as usize,
                raw_size: read_u32(bytes, o + 16).unwrap_or(0) as usize,
                raw_addr: read_u32(bytes, o + 20).unwrap_or(0) as usize,
                characteristics: read_u32(bytes, o + 36).unwrap_or(0),
            });
        }
        Ok(Self {
            bytes,
            mapped,
            image_base,
            sections,
            export_dir_rva,
        })
    }

    fn section(&self, prefix: &[u8]) -> Option<Section> {
        self.sections
            .iter()
            .copied()
            .find(|s| s.name.starts_with(prefix))
    }

    fn section_slice(&self, sec: Section) -> Option<&'a [u8]> {
        if self.mapped {
            let len = sec.virtual_size.max(sec.raw_size);
            self.bytes.get(sec.virtual_addr..sec.virtual_addr.saturating_add(len))
        } else {
            self.bytes.get(sec.raw_addr..sec.raw_addr.saturating_add(sec.raw_size))
        }
    }

    fn rva_to_offset(&self, rva: usize) -> Option<usize> {
        if self.mapped {
            return (rva < self.bytes.len()).then_some(rva);
        }
        for s in &self.sections {
            let span = s.virtual_size.max(s.raw_size);
            if rva >= s.virtual_addr && rva < s.virtual_addr.saturating_add(span) {
                let delta = rva - s.virtual_addr;
                return (delta < s.raw_size).then_some(s.raw_addr + delta);
            }
        }
        None
    }

    fn slice_at_rva(&self, rva: usize, len: usize) -> Option<&'a [u8]> {
        let off = self.rva_to_offset(rva)?;
        self.bytes.get(off..off.saturating_add(len))
    }
}

fn find_bytes(haystack: &[u8], needle: &[u8]) -> Vec<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return Vec::new();
    }
    let mut hits = Vec::new();
    let mut i = 0;
    let first = needle[0];
    while i + needle.len() <= haystack.len() {
        if haystack[i] == first && &haystack[i..i + needle.len()] == needle {
            hits.push(i);
            i += needle.len();
        } else {
            i += 1;
        }
    }
    hits
}

fn match_masked(haystack: &[u8], pattern: &[Option<u8>]) -> Option<usize> {
    if pattern.is_empty() || haystack.len() < pattern.len() {
        return None;
    }
    'outer: for i in 0..=haystack.len() - pattern.len() {
        for (j, expected) in pattern.iter().enumerate() {
            if let Some(byte) = expected
                && haystack[i + j] != *byte
            {
                continue 'outer;
            }
        }
        return Some(i);
    }
    None
}

fn find_translate_tracker_vftables(pe: &PeImage<'_>) -> Vec<usize> {
    const IMAGE_SCN_MEM_DISCARDABLE: u32 = 0x0200_0000;
    const IMAGE_SCN_MEM_READ: u32 = 0x4000_0000;
    let mut td_rvas = Vec::new();
    for sec in &pe.sections {
        if sec.name.starts_with(b".text")
            || (sec.characteristics & IMAGE_SCN_MEM_DISCARDABLE) != 0
            || (sec.characteristics & IMAGE_SCN_MEM_READ) == 0
        {
            continue;
        }
        let Some(slice) = pe.section_slice(*sec) else {
            continue;
        };
        for hit in find_bytes(slice, b"setup@TranslateTracker@HistoryView@@") {
            let search_start = hit.saturating_sub(256);
            let prefix = &slice[search_start..hit];
            if let Some(rel_av) = prefix.windows(4).rposition(|w| w == b".?AV") {
                let name_off = search_start + rel_av;
                if name_off >= 16 {
                    td_rvas.push(sec.virtual_addr + name_off - 16);
                }
            }
        }
    }
    if td_rvas.is_empty() {
        return Vec::new();
    }

    let Some(rdata) = pe.section(b".rdata") else {
        return Vec::new();
    };
    let Some(rdata_slice) = pe.section_slice(rdata) else {
        return Vec::new();
    };

    let mut col_vas = Vec::new();
    for td_rva in td_rvas {
        let needle = (td_rva as u32).to_le_bytes();
        for hit in find_bytes(rdata_slice, &needle) {
            if hit >= 12 && read_u32(rdata_slice, hit - 12) == Some(1) {
                let col_rva = rdata.virtual_addr + hit - 12;
                col_vas.push(pe.image_base + col_rva as u64);
            }
        }
    }

    let mut vftables = Vec::new();
    for col_va in col_vas {
        let needle = col_va.to_le_bytes();
        for hit in find_bytes(rdata_slice, &needle) {
            let vftable_rva = rdata.virtual_addr + hit + 8;
            if !vftables.contains(&vftable_rva) {
                vftables.push(vftable_rva);
            }
        }
    }
    vftables
}

fn find_pdata_range(pe: &PeImage<'_>, rva: usize) -> Option<(usize, usize)> {
    let pdata = pe.section(b".pdata")?;
    let slice = pe.section_slice(pdata)?;
    for off in (0..slice.len().saturating_sub(11)).step_by(12) {
        let beg = read_u32(slice, off)? as usize;
        let end = read_u32(slice, off + 4)? as usize;
        if rva >= beg && rva < end {
            return Some((beg, end));
        }
    }
    None
}

fn scan_translate_tracker_setup(pe: &PeImage<'_>) -> Result<PatchPlan, &'static str> {
    let vftables = find_translate_tracker_vftables(pe);
    if vftables.is_empty() {
        return Err("TranslateTracker RTTI vftables not found");
    }

    let text = pe.section(b".text").ok_or(".text section not found")?;
    let text_slice = pe.section_slice(text).ok_or("cannot read .text section")?;

    let mut fn_ranges: Vec<(usize, usize)> = Vec::new();
    for i in 0..text_slice.len().saturating_sub(7) {
        let b0 = text_slice[i];
        if (b0 == 0x48 || b0 == 0x4C)
            && text_slice[i + 1] == 0x8D
            && (text_slice[i + 2] & 0xC7) == 0x05
        {
            let disp = read_i32(text_slice, i + 3).unwrap_or(0) as isize;
            let insn_rva = text.virtual_addr + i;
            let target = (insn_rva as isize + 7 + disp) as usize;
            if vftables.contains(&target)
                && let Some(range) = find_pdata_range(pe, insn_rva)
                && !fn_ranges.contains(&range)
            {
                fn_ranges.push(range);
            }
        }
    }

    // In HistoryView::TranslateTracker::setup():
    // mov rdx, [rdx+0x60]; test rdx, rdx; je ...; lea rcx, [rbp+...]; call Data::AmPremiumValue; nop; mov rax, [Core::App()]
    const SETUP_PATTERN: &[Option<u8>] = &[
        Some(0x48), Some(0x8B), Some(0x52), Some(0x60),
        Some(0x48), Some(0x85), Some(0xD2),
        Some(0x0F), Some(0x84), None, None, None, None,
        Some(0x48), Some(0x8D), Some(0x8D), None, None, Some(0x00), Some(0x00),
        Some(0xE8), None, None, None, None,
        Some(0x90),
        Some(0x48), Some(0x8B), Some(0x05), None, None, None, None,
        Some(0x48), Some(0x85), Some(0xC0),
    ];

    // Inside Data::AmPremiumValue null-session fallback:
    // test rdi, rdi; jne ...; lea rax, [rip+vtable]; mov [rcx], rax; mov byte [rcx+8], r14b; mov [rcx+0x38], rcx
    const RPL_SINGLE_PATTERN: &[Option<u8>] = &[
        Some(0x48), Some(0x85), Some(0xFF),
        Some(0x75), None,
        Some(0x48), Some(0x8D), Some(0x05), None, None, None, None,
        Some(0x48), Some(0x89), Some(0x01),
        Some(0x44), Some(0x88), Some(0x71), Some(0x08),
        Some(0x48), Some(0x89), Some(0x49), Some(0x38),
    ];

    for (beg, end) in fn_ranges {
        let len = end.saturating_sub(beg);
        let Some(fn_bytes) = pe.slice_at_rva(beg, len) else {
            continue;
        };
        let Some(hit) = match_masked(fn_bytes, SETUP_PATTERN) else {
            continue;
        };
        let call_site_rva = beg + hit + 20;
        let rel32 = read_i32(fn_bytes, hit + 21).ok_or("invalid call disp")? as isize;
        let am_premium_rva = (call_site_rva as isize + 5 + rel32) as usize;

        let am_bytes = pe
            .slice_at_rva(am_premium_rva, 160)
            .ok_or("cannot read AmPremiumValue prologue")?;
        let rpl_hit = match_masked(am_bytes, RPL_SINGLE_PATTERN)
            .ok_or("rpl::single<bool> pattern not found inside AmPremiumValue")?;
        let lea_rva = am_premium_rva + rpl_hit + 5;
        let vt_disp = read_i32(am_bytes, rpl_hit + 8).ok_or("invalid vtable disp")? as isize;
        let rpl_single_vtable_rva = (lea_rva as isize + 7 + vt_disp) as usize;

        return Ok(PatchPlan {
            image_base: pe.image_base,
            call_site_rva,
            am_premium_rva,
            rpl_single_vtable_rva,
        });
    }

    Err("TranslateTracker::setup AmPremiumValue call pattern not found")
}

/// Locate the TranslateTracker::setup() AmPremiumValue call gate inside an unmapped PE file.
pub fn find_translate_tracker_patch(file_bytes: &[u8]) -> Result<PatchPlan, &'static str> {
    let pe = PeImage::parse(file_bytes, false)?;
    scan_translate_tracker_setup(&pe)
}

/// Locate the TranslateTracker::setup() AmPremiumValue call gate inside a mapped module in memory.
pub fn find_translate_tracker_patch_mapped(module_bytes: &[u8]) -> Result<PatchPlan, &'static str> {
    let pe = PeImage::parse(module_bytes, true)?;
    scan_translate_tracker_setup(&pe)
}

/// Build the 25-byte x64 machine-code stub constructing `rpl::single<bool>(true)` in `rcx`.
pub fn build_rpl_single_true_stub(rpl_single_vtable_va: u64) -> [u8; 25] {
    let mut code = [0u8; 25];
    code[0..2].copy_from_slice(&[0x48, 0xB8]); // mov rax, imm64
    code[2..10].copy_from_slice(&rpl_single_vtable_va.to_le_bytes());
    code[10..13].copy_from_slice(&[0x48, 0x89, 0x01]); // mov qword ptr [rcx], rax
    code[13..17].copy_from_slice(&[0xC6, 0x41, 0x08, 0x01]); // mov byte ptr [rcx + 8], 1
    code[17..21].copy_from_slice(&[0x48, 0x89, 0x49, 0x38]); // mov qword ptr [rcx + 0x38], rcx
    code[21..24].copy_from_slice(&[0x48, 0x89, 0xC8]); // mov rax, rcx
    code[24] = 0xC3; // ret
    code
}

/// Read all named exports from an unmapped PE64 DLL.
pub fn read_export_names(file_bytes: &[u8]) -> Result<Vec<String>, &'static str> {
    let pe = PeImage::parse(file_bytes, false)?;
    if pe.export_dir_rva == 0 {
        return Ok(Vec::new());
    }
    let exp_dir = pe
        .slice_at_rva(pe.export_dir_rva, 40)
        .ok_or("truncated export directory")?;
    let num_names = read_u32(exp_dir, 24).ok_or("missing NumberOfNames")? as usize;
    let addr_names_rva = read_u32(exp_dir, 32).ok_or("missing AddressOfNames")? as usize;
    let names_table = pe
        .slice_at_rva(addr_names_rva, num_names * 4)
        .ok_or("truncated AddressOfNames")?;
    let mut out = Vec::with_capacity(num_names);
    for i in 0..num_names {
        let name_rva = read_u32(names_table, i * 4).unwrap_or(0) as usize;
        let name_off = pe.rva_to_offset(name_rva).ok_or("invalid export name RVA")?;
        let tail = &file_bytes[name_off..];
        let end = tail
            .iter()
            .position(|&b| b == 0)
            .ok_or("unterminated export name")?;
        let s = std::str::from_utf8(&tail[..end]).map_err(|_| "non-utf8 export name")?;
        out.push(s.to_owned());
    }
    Ok(out)
}
