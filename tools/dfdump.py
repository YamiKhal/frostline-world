"""Print a density-function tree compactly. Usage: dfdump.py <settings> <router key> [maxdepth]"""
import json, sys
d = json.load(open('data/frostline/worldgen/noise_settings/%s.json' % sys.argv[1]))
node = d['noise_router'][sys.argv[2]]
MAX = int(sys.argv[3]) if len(sys.argv) > 3 else 6

def short(n):
    if isinstance(n, (int, float)): return repr(n)
    if isinstance(n, str): return n
    return None

def walk(n, d_, path=''):
    pad = '  ' * d_
    s = short(n)
    if s is not None:
        print(pad + str(s)); return
    if isinstance(n, list):
        print(pad + '[list %d]' % len(n)); return
    t = n.get('type', '?')
    if d_ >= MAX:
        print(pad + t.split(':')[-1] + ' ...'); return
    extra = ''
    for k in ('min', 'max', 'from_value', 'to_value',
              'from_y', 'to_y', 'noise', 'xz_scale', 'y_scale', 'shift_x', 'shift_z'):
        if k in n and isinstance(n[k], (int, float, str)):
            extra += ' %s=%s' % (k, n[k])
    print(pad + t.split(':')[-1] + extra)
    for k in ('argument', 'argument1', 'argument2', 'input', 'spline', 'value'):
        if k in n and not isinstance(n[k], (int, float, str)):
            print(pad + ' .' + k)
            walk(n[k], d_ + 1)
    if t.endswith('spline') and isinstance(n.get('spline'), dict):
        sp = n['spline']
        if 'points' in sp:
            print(pad + '  coord=%s points=%s' % (
                short(sp.get('coordinate')) or 'expr',
                [(p['location'], p['value'] if isinstance(p['value'], (int, float)) else '<spline>')
                 for p in sp['points']]))
walk(node, 0)
