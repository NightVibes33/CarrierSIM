#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
rust = root / "rust-core"
lib = rust / "src" / "lib.rs"
header = rust / "include" / "airlift.h"
exploit = rust / "src" / "exploit.rs"
extension = root / "airlift-patch" / "carriersim_exploit.rs"

text = exploit.read_text()
marker = "// ---------------------------------------------------------------------------\n// CarrierSIM extension\n// ---------------------------------------------------------------------------"
if marker not in text:
    exploit.write_text(text.rstrip() + "\n\n" + extension.read_text().lstrip())

lib_text = lib.read_text()
lib_marker = "// CarrierSIM FFI"
if lib_marker not in lib_text:
    insert = r"""
// CarrierSIM FFI
#[no_mangle]
pub unsafe extern "C" fn al_carriersim_status(
    pairing_path: *const c_char,
    log_cb: exploit::ALLogCallback,
    ctx: *mut c_void,
    out_json: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    exploit::carriersim_status(pairing_path, log_cb, ctx, out_json, out_error)
}

#[no_mangle]
pub unsafe extern "C" fn al_carriersim_apply(
    pairing_path: *const c_char,
    imsi: *const c_char,
    bundle: *const c_char,
    log_cb: exploit::ALLogCallback,
    ctx: *mut c_void,
    out_json: *mut *mut c_char,
    out_error: *mut *mut c_char,
) -> i32 {
    exploit::carriersim_apply(
        pairing_path,
        imsi,
        bundle,
        log_cb,
        ctx,
        out_json,
        out_error,
    )
}
"""
    lib.write_text(lib_text.rstrip() + "\n\n" + insert.lstrip())

header_text = header.read_text()
header_marker = "int32_t al_carriersim_status("
if header_marker not in header_text:
    declarations = r"""
// ---------------------------------------------------------------------------
// CarrierSIM
// ---------------------------------------------------------------------------

int32_t al_carriersim_status(const char *pairing_path,
                             ALLogCallback log_cb,
                             void *ctx,
                             char **out_json,
                             char **out_error);

int32_t al_carriersim_apply(const char *pairing_path,
                            const char *imsi,
                            const char *bundle,
                            ALLogCallback log_cb,
                            void *ctx,
                            char **out_json,
                            char **out_error);

"""
    footer = "#ifdef __cplusplus\n}\n#endif\n\n#endif /* AIRLIFT_H */"
    if footer not in header_text:
        raise SystemExit("airlift.h footer changed; update patch-airlift.py")
    header.write_text(header_text.replace(footer, declarations + footer))
