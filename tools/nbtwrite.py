"""Write Minecraft structure .nbt files (gzipped, uncompressed-NBT format)."""
import gzip, struct, io

DATA_VERSION = 3465  # 1.20.1

def _str(s):
    b = s.encode('utf-8')
    return struct.pack('>H', len(b)) + b

def _int_list(vals):
    return b'\x03' + struct.pack('>i', len(vals)) + b''.join(struct.pack('>i', v) for v in vals)

def write_structure(path, size, palette, blocks):
    """size: (x,y,z); palette: ["minecraft:stone", ...];
       blocks: [((x,y,z), palette_index), ...] - positions NOT listed are left untouched."""
    out = io.BytesIO()
    out.write(b'\x0a' + _str(''))                      # root compound

    out.write(b'\x03' + _str('DataVersion') + struct.pack('>i', DATA_VERSION))

    out.write(b'\x09' + _str('size') + _int_list(list(size)))

    out.write(b'\x09' + _str('palette') + b'\x0a' + struct.pack('>i', len(palette)))
    for name in palette:
        out.write(b'\x08' + _str('Name') + _str(name))
        out.write(b'\x00')                             # end of this compound

    out.write(b'\x09' + _str('blocks') + b'\x0a' + struct.pack('>i', len(blocks)))
    for pos, state in blocks:
        out.write(b'\x03' + _str('state') + struct.pack('>i', state))
        out.write(b'\x09' + _str('pos') + _int_list(list(pos)))
        out.write(b'\x00')

    out.write(b'\x09' + _str('entities') + b'\x0a' + struct.pack('>i', 0))

    out.write(b'\x00')                                 # end root
    with open(path, 'wb') as f:
        f.write(gzip.compress(out.getvalue()))
