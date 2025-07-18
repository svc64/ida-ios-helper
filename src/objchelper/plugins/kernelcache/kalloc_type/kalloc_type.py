__all__ = ["apply_kalloc_types"]

from ida_typeinf import tinfo_t

from objchelper.idahelper import memory, segments, tif

KALLOC_FLAG_DEFAULT        = 0x0001
KALLOC_FLAG_PRIV_ACCT      = 0x0002
KALLOC_FLAG_SHARED_ACCT    = 0x0004
KALLOC_FLAG_DATA_ONLY      = 0x0008
KALLOC_FLAG_VM             = 0x0010
KALLOC_FLAG_CHANGED        = 0x0020
KALLOC_FLAG_CHANGED2       = 0x0040
KALLOC_FLAG_PTR_ARRAY      = 0x0080
KALLOC_FLAG_NOSHARED       = 0x2000
KALLOC_FLAG_SLID           = 0x4000
KALLOC_FLAG_PROCESSED      = 0x8000
KALLOC_FLAG_HASH           = 0xffff0000

KALLOC_TYPE_DEFINITIONS = """
struct zone_view {
    void*          zv_zone;
    void*    zv_stats;
    const char     *zv_name;
    void*     zv_next;
};

enum kalloc_type_flags_t : uint32_t {
    KT_DEFAULT        = 0x0001,
    KT_PRIV_ACCT      = 0x0002,
    KT_SHARED_ACCT    = 0x0004,
    KT_DATA_ONLY      = 0x0008,
    KT_VM             = 0x0010,
    KT_CHANGED        = 0x0020,
    KT_CHANGED2       = 0x0040,
    KT_PTR_ARRAY      = 0x0080,
    KT_NOSHARED       = 0x2000,
    KT_SLID           = 0x4000,
    KT_PROCESSED      = 0x8000,
    KT_HASH           = 0xffff0000,
};

struct kalloc_type_view {
    struct zone_view        kt_zv;
    const char             *kt_signature;
    kalloc_type_flags_t     kt_flags;
    uint32_t                kt_size;
    void                   *unused1;
    void                   *unused2;
};
"""
KALLOC_TYPE_VIEW_OFFSET_NAME = 16  # void *zv_zone + void *zv_stats
KALLOC_TYPE_VIEW_OFFSET_SIGNATURE = 32  # zone_view
KALLOC_TYPE_VIEW_OFFSET_FLAGS = 40  # zone_view + const char *kt_signature
KALLOC_TYPE_VIEW_OFFSET_SIZE = 44  # zone_view + kalloc_type_flags_t kt_flags

VOID_PTR_TYPE: tinfo_t = tif.from_c_type("void*")  # type: ignore   # noqa: PGH003


def apply_kalloc_types():
    kalloc_type_view_tif = tif.from_struct_name("kalloc_type_view")
    if kalloc_type_view_tif is None:
        if not tif.create_from_c_decl(KALLOC_TYPE_DEFINITIONS):
            print("[Error] failed to created kalloc_type_view type")

        kalloc_type_view_tif = tif.from_struct_name("kalloc_type_view")
        if kalloc_type_view_tif is None:
            print("[Error] could not find kalloc type view")
            return

    classes_handled: set[str] = set()
    for segment in segments.get_segments():
        if not segment.name.endswith("__kalloc_type"):
            continue
        set_kalloc_type_for_segment(segment, kalloc_type_view_tif, classes_handled)


def set_kalloc_type_for_segment(segment: segments.Segment, kalloc_type_view_tif: tinfo_t, classes_handled: set[str]):
    kalloc_type_view_size = kalloc_type_view_tif.get_size()

    if segment.size % kalloc_type_view_size != 0:
        print(
            f"[Warning] {segment.name} at {segment.start_ea:X} is not a multiplie of kalloc_type_view size. is: {segment.size}, not multiplie of: {kalloc_type_view_size}"
        )
        return

    for kty_ea in range(segment.start_ea, segment.end_ea, kalloc_type_view_size):
        if not tif.apply_tinfo_to_ea(kalloc_type_view_tif, kty_ea):
            print(f"[Error] failed to apply kalloc_type_view on {kty_ea:X}")

        site_name_ea = memory.qword_from_ea(kty_ea + KALLOC_TYPE_VIEW_OFFSET_NAME)
        site_name = memory.str_from_ea(site_name_ea)
        if site_name is None:
            print(f"[Error] failed to read name for kalloc_type_view on {kty_ea:X}")
            continue

        if not site_name.startswith("site."):
            print(f"[Error] invalid site name on {kty_ea:X}, is: {site_name!r}")
            continue

        class_name = site_name[5:]
        if class_name.startswith("struct "):
            class_name = class_name[7:]
        if class_name.startswith("typeof(") or class_name == "T":
            # Clang generates them using macro, so it might lead to some weird specific ones...
            continue

        new_name = f"{class_name}_kty"
        if not memory.set_name(kty_ea, new_name, retry=True, retry_count=50):
            print(f"[Error] failed to rename kalloc_type_view on {kty_ea:X} to {new_name!r}")
            continue
        
        flags = memory.qword_from_ea(kty_ea + KALLOC_TYPE_VIEW_OFFSET_FLAGS)
        if flags & KALLOC_FLAG_DATA_ONLY or flags & KALLOC_FLAG_VM:
            data_size = memory.dword_from_ea(kty_ea + KALLOC_TYPE_VIEW_OFFSET_SIZE)
            create_data_type(class_name, data_size, classes_handled)
        else:
            signature_ea = memory.qword_from_ea(kty_ea + KALLOC_TYPE_VIEW_OFFSET_SIGNATURE)
            signature = memory.str_from_ea(signature_ea)
            if signature is None:
                print(f"[Error] failed to read signature for {new_name} on {kty_ea:X}")
                continue

            try_enrich_type(class_name, signature, classes_handled)

def create_data_type(name: str, size: int, classes_handled: set[str]):
    if name in classes_handled:
        return
    classes_handled.add(name)
    """Create a new data type with the given name and size"""
    tif.create_from_c_decl(f"struct {name} {{ char data[{size}]; }};")

def try_enrich_type(class_name: str, signature: str, classes_handled: set[str]):
    # Don't try to enrich the same type multiple times
    if class_name in classes_handled:
        return
    classes_handled.add(class_name)

    class_tif = tif.from_struct_name(class_name)
    if class_tif is None:
        return

    class_base_offset = tif.get_base_offset_for_class(class_tif)
    if class_base_offset is None:
        return

    # Align it to multiplication of 8
    class_base_offset = int((class_base_offset + 7) / 8) * 8

    # The signature is on 8 bytes each time
    for i in range(class_base_offset // 8, len(signature)):
        c = signature[i]
        if c != "1":
            continue

        member = tif.get_member(class_tif, i * 8)
        if member is None:
            print(f"[Error] {class_name} has no member for at offset {i * 8:#X}")
            return

        # I check for if this is a pointer, because if it was union of data + pointer, the compiler should have yelled
        # So if you changed the type to non pointer, you were mistaken...
        if not member.type.is_ptr() and not tif.set_udm_type(class_tif, member, VOID_PTR_TYPE):
            print(f"[Error] failed to set type for {class_name} member at offset {member.offset}")
