"""Evaluate a noise_settings density function with the noises as free variables.

Solves depth(y)=0 to get the pre-3D-noise terrain height, so a cap in the offset
chain shows up directly as a spike in the height histogram.
"""
import json, random, collections

def ev(n, env):
    if isinstance(n, (int, float)): return float(n)
    if isinstance(n, str): return env.get(n, 0.0)
    if 'type' not in n and 'points' in n:      # bare nested spline
        return _spline(n, env)
    t = n['type']
    if t == 'minecraft:add':  return ev(n['argument1'], env) + ev(n['argument2'], env)
    if t == 'minecraft:mul':  return ev(n['argument1'], env) * ev(n['argument2'], env)
    if t == 'minecraft:max':  return max(ev(n['argument1'], env), ev(n['argument2'], env))
    if t == 'minecraft:min':  return min(ev(n['argument1'], env), ev(n['argument2'], env))
    if t == 'minecraft:abs':  return abs(ev(n['argument'], env))
    if t == 'minecraft:square': v=ev(n['argument'],env); return v*v
    if t == 'minecraft:cube':   v=ev(n['argument'],env); return v*v*v
    if t == 'minecraft:clamp':
        return max(n['min'], min(n['max'], ev(n['input'], env)))
    if t in ('minecraft:flat_cache','minecraft:cache_2d'):
        # 2D: independent of y, so memoise per sample (env carries the cache)
        c = env['_c']; k = id(n)
        if k not in c: c[k] = ev(n['argument'], env)
        return c[k]
    if t in ('minecraft:cache_once','minecraft:interpolated','minecraft:cache_all_in_cell'):
        return ev(n['argument'], env)
    if t == 'minecraft:y_clamped_gradient':
        y = env['y']
        fy, ty, fv, tv = n['from_y'], n['to_y'], n['from_value'], n['to_value']
        if y <= fy: return fv
        if y >= ty: return tv
        return fv + (tv-fv)*(y-fy)/(ty-fy)
    if t in ('minecraft:shifted_noise','minecraft:noise'):
        return env.get(n['noise'], 0.0)
    if t == 'frostline:progression':
        return env.get('progression', -1.0)
    if t == 'minecraft:spline':
        return _spline(n['spline'], env)
    if t.startswith('frostline:'):
        return env.get(t, 0.0)
    raise KeyError(t)

def _spline(sp, env):
    if True:
        x = ev(sp['coordinate'], env)
        pts = sp['points']
        loc = [p['location'] for p in pts]
        val = [p['value'] if isinstance(p['value'], (int,float)) else ev(p['value'], env) for p in pts]
        if x <= loc[0]: return float(val[0])
        if x >= loc[-1]: return float(val[-1])
        for i in range(len(loc)-1):
            if loc[i] <= x <= loc[i+1]:
                f = (x-loc[i])/(loc[i+1]-loc[i])
                return val[i] + f*(val[i+1]-val[i])
        return float(val[-1])

def surface(depth_node, env, lo=-64.0, hi=320.0):
    """depth decreases with y; find y where depth crosses 0."""
    env = dict(env); env['_c'] = {}
    env['y'] = lo; a = ev(depth_node, env)
    env['y'] = hi; b = ev(depth_node, env)
    if a < 0: return lo
    if b > 0: return hi
    for _ in range(40):
        m = (lo+hi)/2; env['y'] = m
        if ev(depth_node, env) > 0: lo = m
        else: hi = m
    return (lo+hi)/2

NOISES = ['minecraft:erosion','minecraft:ridge','frostline:relief','frostline:cliff_jitter',
          'frostline:valley','frostline:swell','frostline:river','frostline:river_branch',
          'frostline:shelf']

def sample(depth_node, n=4000, seed=1, progression=-1.0, fixed=None):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        env = {k: rnd.uniform(-1,1) for k in NOISES}
        env['progression'] = progression; env['_c'] = {}
        if fixed: env.update(fixed)
        out.append(surface(depth_node, env))
    return out

def histo(vals, label):
    h = collections.Counter(int(round(v)) for v in vals)
    tot = len(vals)
    print('%s   n=%d  min %.0f  max %.0f  mean %.1f' % (label, tot, min(vals), max(vals), sum(vals)/tot))
    for y in sorted(h):
        if h[y]/tot > 0.01:
            print('   y%-4d %5.1f%%  %s' % (y, 100*h[y]/tot, '#'*int(120*h[y]/tot)))
