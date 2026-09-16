"""Minimal NBT reader, enough to inspect structure .nbt files."""
import gzip, struct, sys, io

def rd(f):
    def u1(): return struct.unpack('>B', f.read(1))[0]
    def u2(): return struct.unpack('>h', f.read(2))[0]
    def u4(): return struct.unpack('>i', f.read(4))[0]
    def u8(): return struct.unpack('>q', f.read(8))[0]
    def f4(): return struct.unpack('>f', f.read(4))[0]
    def f8(): return struct.unpack('>d', f.read(8))[0]
    def st():
        n = struct.unpack('>H', f.read(2))[0]
        return f.read(n).decode('utf-8')
    def payload(t):
        if t == 1: return struct.unpack('>b', f.read(1))[0]
        if t == 2: return u2()
        if t == 3: return u4()
        if t == 4: return u8()
        if t == 5: return f4()
        if t == 6: return f8()
        if t == 7: return [struct.unpack('>b', f.read(1))[0] for _ in range(u4())]
        if t == 8: return st()
        if t == 9:
            it = u1(); n = u4()
            return [payload(it) for _ in range(n)]
        if t == 10:
            d = {}
            while True:
                tt = u1()
                if tt == 0: return d
                k = st()
                d[k] = payload(tt)
        if t == 11: return [u4() for _ in range(u4())]
        if t == 12: return [u8() for _ in range(u4())]
        raise ValueError('tag %d' % t)
    t = u1()
    st()
    return payload(t)

for p in sys.argv[1:]:
    raw = open(p, 'rb').read()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    d = rd(io.BytesIO(raw))
    size = d.get('size')
    pal = [b['Name'] for b in d.get('palette', [])]
    blocks = d.get('blocks', [])
    ys = sorted(set(b['pos'][1] for b in blocks))
    print('%-22s size=%s  blocks=%d  y-layers=%s' % (p.split('/')[-1], size, len(blocks), ys))
    print('   palette: %s' % ', '.join(x.split(':')[-1] for x in pal))
    from collections import Counter
    c = Counter(pal[b['state']].split(':')[-1] for b in blocks)
    print('   counts : %s' % dict(c))
