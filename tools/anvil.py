"""Minimal 1.20.1 Anvil reader: region -> chunks -> block/biome arrays (numpy)."""
import struct, zlib, os, math
import numpy as np


def _nbt(buf, i, t):
    if t == 1: return struct.unpack_from('>b', buf, i)[0], i + 1
    if t == 2: return struct.unpack_from('>h', buf, i)[0], i + 2
    if t == 3: return struct.unpack_from('>i', buf, i)[0], i + 4
    if t == 4: return struct.unpack_from('>q', buf, i)[0], i + 8
    if t == 5: return struct.unpack_from('>f', buf, i)[0], i + 4
    if t == 6: return struct.unpack_from('>d', buf, i)[0], i + 8
    if t == 7:
        n = struct.unpack_from('>i', buf, i)[0]; i += 4
        return bytes(buf[i:i + n]), i + n
    if t == 8:
        n = struct.unpack_from('>H', buf, i)[0]; i += 2
        return bytes(buf[i:i + n]).decode('utf-8', 'replace'), i + n
    if t == 9:
        et = buf[i]; n = struct.unpack_from('>i', buf, i + 1)[0]; i += 5
        out = []
        for _ in range(n):
            v, i = _nbt(buf, i, et); out.append(v)
        return out, i
    if t == 10:
        out = {}
        while True:
            ct = buf[i]; i += 1
            if ct == 0: return out, i
            n = struct.unpack_from('>H', buf, i)[0]; i += 2
            name = bytes(buf[i:i + n]).decode('utf-8', 'replace'); i += n
            out[name], i = _nbt(buf, i, ct)
    if t == 11:
        n = struct.unpack_from('>i', buf, i)[0]; i += 4
        return np.frombuffer(buf, '>i4', n, i), i + 4 * n
    if t == 12:
        n = struct.unpack_from('>i', buf, i)[0]; i += 4
        return np.frombuffer(buf, '>i8', n, i), i + 8 * n
    raise ValueError(t)


def read_nbt(data):
    t = data[0]
    n = struct.unpack_from('>H', data, 1)[0]
    v, _ = _nbt(data, 3 + n, t)
    return v


def region_chunks(path):
    with open(path, 'rb') as f:
        raw = f.read()
    if len(raw) < 8192:
        return
    for idx in range(1024):
        off = int.from_bytes(raw[idx * 4:idx * 4 + 3], 'big')
        if off == 0: continue
        p = off * 4096
        length = struct.unpack_from('>i', raw, p)[0]
        comp = raw[p + 4]
        body = raw[p + 5:p + 4 + length]
        if comp == 2: data = zlib.decompress(body)
        elif comp == 1:
            import gzip; data = gzip.decompress(body)
        else: continue
        yield read_nbt(data)


def unpack(longs, bits, count):
    longs = np.asarray(longs).astype(np.uint64)
    per = 64 // bits
    mask = np.uint64((1 << bits) - 1)
    out = np.empty(len(longs) * per, dtype=np.uint64)
    for k in range(per):
        out[k::per] = (longs >> np.uint64(k * bits)) & mask
    return out[:count].astype(np.int64)


def state_name(s):
    props = s.get('Properties')
    if not props: return s['Name']
    return s['Name'] + '[' + ','.join(f'{k}={v}' for k, v in sorted(props.items())) + ']'




class World:
    def __init__(self):
        self.states = {}
        self.state_list = []
        self.biomes = {}
        self.biome_list = []

    def sid(self, name):
        i = self.states.get(name)
        if i is None:
            i = len(self.state_list); self.states[name] = i; self.state_list.append(name)
        return i

    def bid(self, name):
        i = self.biomes.get(name)
        if i is None:
            i = len(self.biome_list); self.biomes[name] = i; self.biome_list.append(name)
        return i

    def chunk(self, c, min_y=-64, height=384):
        n_sec = height // 16
        min_sec = min_y // 16
        blocks = np.full((height, 16, 16), self.sid('minecraft:air'), dtype=np.int32)
        biomes = np.zeros((height // 4, 4, 4), dtype=np.int32)
        for s in c.get('sections', []):
            y = s['Y'] - min_sec
            if y < 0 or y >= n_sec: continue
            bs = s.get('block_states')
            if bs:
                pal = np.array([self.sid(state_name(p)) for p in bs['palette']], dtype=np.int32)
                if len(pal) == 1 or 'data' not in bs:
                    blocks[y * 16:(y + 1) * 16] = pal[0]
                else:
                    bits = max(4, math.ceil(math.log2(len(pal))))
                    blocks[y * 16:(y + 1) * 16] = pal[unpack(bs['data'], bits, 4096)].reshape(16, 16, 16)
            bi = s.get('biomes')
            if bi:
                pal = np.array([self.bid(p) for p in bi['palette']], dtype=np.int32)
                if len(pal) == 1 or 'data' not in bi:
                    biomes[y * 4:(y + 1) * 4] = pal[0]
                else:
                    bits = max(1, math.ceil(math.log2(len(pal))))
                    biomes[y * 4:(y + 1) * 4] = pal[unpack(bi['data'], bits, 64)].reshape(4, 4, 4)
        return blocks, biomes
