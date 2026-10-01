#!/usr/bin/env python3
"""Generate an animated Breakout game played on a GitHub contribution graph.

Every day with at least one contribution is a brick. A paddle AI keeps a ball
in play until the whole year is cleared. Output is a self-contained SMIL SVG
(no scripts, no external assets), so it renders inside a README <img>.

Usage: python scripts/breakout.py --user TingdeLiu --out dist
"""
import argparse
import html
import math
import os
import random
import re
import urllib.request

# --- layout -----------------------------------------------------------------
W, H = 880, 344
PITCH, BRICK = 15.5, 12.5
COLS, ROWS = 53, 7
X0 = (W - COLS * PITCH) / 2 + (PITCH - BRICK) / 2
Y0 = 66.0
CEIL = 46.0
PADDLE_Y, PADDLE_W, PADDLE_H = 306.0, 104.0, 5.0
WALL_L, WALL_R = 14.0, W - 14.0
RADIUS = 3.8
SPEED = 900.0          # px / s
FPS = 60               # simulation rate
OUT_FPS = 30           # keyframe rate
INTRO = 1.8            # s, bricks assemble before the ball launches
HOLD = 3.6             # s, "stage clear" screen before the loop restarts
MAX_PLAY = 70.0        # s, hard cap so the loop stays short
FIRE_EVERY = 3         # every n-th hit ignites the ball
FIRE_TIME = 1.5        # s, a burning ball pierces bricks instead of bouncing
TRAIL = 8              # ghost circles behind the ball
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

THEMES = {
    "dark": dict(
        bg0="#0a101c", bg1="#0d1117", border="#1c2736", star="#9bb2d4", halo="#00f7f4", halo_op=".10",
        text="#e6edf3", dim="#5d6e86", accent="#2ff3ee", ball="#ffffff", fire="#ffb454", flash="#ffffff",
        paddle0="#2ff3ee", paddle1="#3b82f6", empty="#151e2b", rule="#1c2736",
        levels=["#0f5865", "#14909f", "#18d3d0", "#8dfffb"], glow=True, shadow=False),
    "light": dict(
        bg0="#ffffff", bg1="#f3f7fa", border="#d0d7de", star="#9fb3c8", halo="#0891b2", halo_op=".07",
        text="#1f2328", dim="#838d99", accent="#0891b2", ball="#0b1220", fire="#d97706", flash="#0891b2",
        paddle0="#0891b2", paddle1="#2563eb", empty="#e6edf3", rule="#d8dee4",
        levels=["#a9dde6", "#52c2d4", "#0a93b3", "#0a5d78"], glow=False, shadow=True),
}


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


# --- data -------------------------------------------------------------------
def fetch_contributions(user):
    """Return ({(row, col): (count, level)}, {col: (y, m, d) of that week's first day})."""
    req = urllib.request.Request(
        f"https://github.com/users/{user}/contributions",
        headers={"User-Agent": "Mozilla/5.0 (breakout-generator)"})
    page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    tips = {m.group(1): m.group(2) for m in re.finditer(
        r'<tool-tip[^>]*\bfor="([^"]+)"[^>]*>([^<]*)</tool-tip>', page)}
    cells, week_start = {}, {}
    for m in re.finditer(r'<td\b[^>]*ContributionCalendar-day[^>]*>', page):
        tag = m.group(0)
        cid = re.search(r'\bid="([^"]+)"', tag)
        lvl = re.search(r'data-level="(\d)"', tag)
        day = re.search(r'data-date="(\d{4})-(\d{2})-(\d{2})"', tag)
        if not (cid and lvl):
            continue
        parts = cid.group(1).split("-")          # contribution-day-component-<row>-<col>
        row, col = int(parts[-2]), int(parts[-1])
        n = re.match(r"(\d+) contribution", html.unescape(tips.get(cid.group(1), "")))
        cells[(row, col)] = (int(n.group(1)) if n else 0, int(lvl.group(1)))
        if day and row == 0:
            week_start[col] = (int(day.group(1)), int(day.group(2)), int(day.group(3)))
    if not cells:
        raise RuntimeError("no contribution cells found; GitHub markup changed?")
    return cells, week_start


# --- simulation -------------------------------------------------------------
def simulate(bricks, rng):
    """Play the game. Returns (frames, events, fire, bounces).
    frames: [(ball_x, ball_y, paddle_x)], events: [(frame, [(row, col), ...])],
    fire: [(start_frame, end_frame)], bounces: [(frame, x)] paddle contacts."""
    alive = set(bricks)
    px = W / 2
    bx, by = px, PADDLE_Y - RADIUS - 0.5
    frames, events, fire, bounces = [], [], [], []
    hits = 0
    burn_until = -1
    step = SPEED / FPS / 4

    def cast(x, y, ang):
        """First live brick a ball launched from (x, y) at `ang` would hit, following wall
        and ceiling bounces, or None if it falls back to the paddle first."""
        dx, dy = math.cos(ang) * 2.5, math.sin(ang) * 2.5
        for _ in range(1200):
            x, y = x + dx, y + dy
            if x < WALL_L + RADIUS or x > WALL_R - RADIUS:
                dx = -dx
            if y < CEIL + RADIUS:
                dy = abs(dy)
            if y > PADDLE_Y:
                return None
            c, r = int((x - X0) // PITCH), int((y - Y0) // PITCH)
            if (r, c) in alive:
                return r, c
        return None

    lo, hi = -math.pi + 0.35, -0.35                     # keep the ball from going flat

    def aim(x, y):
        """Velocity (per substep) from (x, y) toward a random brick that is exposed from below.
        If the straight line is not playable, sweep launch angles and take one that hits."""
        exposed = {}
        for r, c in alive:
            if c not in exposed or r > exposed[c]:
                exposed[c] = r
        col = rng.choice(sorted(exposed))
        tx = X0 + col * PITCH + BRICK / 2
        ty = Y0 + exposed[col] * PITCH + BRICK / 2
        ang = math.atan2(ty - y, tx - x) + rng.uniform(-0.03, 0.03)
        ang = max(lo, min(hi, ang))
        if cast(x, y, ang) is None:
            sweep = [lo + i * (hi - lo) / 160 for i in range(161)]
            hits_target = [a for a in sweep if cast(x, y, a) == (exposed[col], col)]
            hits_any = hits_target or [a for a in sweep if cast(x, y, a) is not None]
            if hits_any:
                ang = min(hits_any, key=lambda a: abs(a - ang))
        return math.cos(ang) * step, math.sin(ang) * step

    vx, vy = aim(bx, by)
    for f in range(int(MAX_PLAY * FPS)):
        if not alive:
            break
        # paddle AI: glide toward the predicted landing point
        if vy > 0:
            t = (PADDLE_Y - RADIUS - by) / vy
            lx = bx + vx * t
            span = WALL_R - WALL_L
            lx = (lx - WALL_L) % (2 * span)
            lx = WALL_L + (lx if lx <= span else 2 * span - lx)
            px += (lx - px) * 0.35
        else:
            px += (W / 2 - px) * 0.01
        px = max(WALL_L + PADDLE_W / 2, min(WALL_R - PADDLE_W / 2, px))

        event = []
        for _ in range(4):
            bx += vx
            by += vy
            if bx < WALL_L + RADIUS:
                bx, vx = WALL_L + RADIUS, abs(vx)
            elif bx > WALL_R - RADIUS:
                bx, vx = WALL_R - RADIUS, -abs(vx)
            if by < CEIL + RADIUS:
                by, vy = CEIL + RADIUS, abs(vy)
            if vy > 0 and by + RADIUS >= PADDLE_Y and by < PADDLE_Y + PADDLE_H:
                if abs(bx - px) > PADDLE_W / 2:          # AI never misses: snap under the ball
                    px = bx
                vx, vy = aim(bx, PADDLE_Y)
                by = PADDLE_Y - RADIUS
                bounces.append((f, bx))
            # brick collision
            c_lo = int((bx - RADIUS - X0) // PITCH)
            c_hi = int((bx + RADIUS - X0) // PITCH)
            r_lo = int((by - RADIUS - Y0) // PITCH)
            r_hi = int((by + RADIUS - Y0) // PITCH)
            hit = None
            for r in range(r_lo, r_hi + 1):
                for c in range(c_lo, c_hi + 1):
                    if (r, c) not in alive:
                        continue
                    rx, ry = X0 + c * PITCH, Y0 + r * PITCH
                    nx = max(rx, min(bx, rx + BRICK))
                    ny = max(ry, min(by, ry + BRICK))
                    if (bx - nx) ** 2 + (by - ny) ** 2 < RADIUS ** 2:
                        hit = (r, c, rx + BRICK / 2, ry + BRICK / 2)
                        break
                if hit:
                    break
            if hit:
                r, c, cx, cy = hit
                hits += 1
                alive.discard((r, c))
                event.append((r, c))
                if f > burn_until:                   # a burning ball goes straight through
                    if hits % FIRE_EVERY == 0:
                        burn_until = f + int(FIRE_TIME * FPS)
                        fire.append((f, burn_until))
                    half = BRICK / 2 + RADIUS
                    if abs(bx - cx) / half > abs(by - cy) / half:
                        vx = math.copysign(abs(vx), bx - cx)
                    else:
                        vy = math.copysign(abs(vy), by - cy)
        if event:
            events.append((f, event))
        frames.append((bx, by, px))
    if alive:                                    # cap reached: clear the remainder
        events.append((len(frames) - 1, sorted(alive)))
    return frames, events, fire, bounces


# --- svg --------------------------------------------------------------------
def fmt(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def render(cells, week_start, theme_name, user):
    th = THEMES[theme_name]
    rng = random.Random(7)
    bricks = {k: v for k, v in cells.items() if v[0] > 0 and k[0] < ROWS}
    total = sum(n for n, _ in cells.values())
    frames, events, fire, bounces = simulate(bricks, rng)
    t_play = len(frames) / FPS
    D = INTRO + t_play + HOLD
    t_end = INTRO + t_play
    last_frame = len(frames) - 1

    def keytimes(ts):
        """Normalise absolute times to strictly increasing keyTimes in [0, 1]."""
        out = []
        for t in ts:
            k = max(0.0, min(1.0, t / D))
            if out and k <= out[-1]:
                k = out[-1] + 1e-5
            out.append(k)
        for i in range(len(out) - 1, 0, -1):     # squeeze back from the end if we overshot
            out[i] = min(out[i], 1.0 - (len(out) - 1 - i) * 1e-5)
        return ";".join(f"{k:.5f}" for k in out)

    dur = f'dur="{D:.2f}s" repeatCount="indefinite"'
    hit_time = {}
    for f, cells_hit in events:
        for rc in cells_hit:
            hit_time[rc] = INTRO + f / FPS
    glow = ' filter="url(#glow)"' if th["glow"] else ""
    lift = ' filter="url(#shadow)"' if th["shadow"] else ""     # light theme: soft drop shadow instead of glow
    mono ='font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"'

    o = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
             f'role="img" aria-label="Breakout game played on the GitHub contribution graph of {html.escape(user)}">')
    o.append(f'<title>Breakout on the contribution graph of {html.escape(user)}</title>')

    # defs
    o.append('<defs>')
    o.append(f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{th["bg0"]}"/>'
             f'<stop offset="1" stop-color="{th["bg1"]}"/></linearGradient>')
    o.append(f'<radialGradient id="halo" cx=".5" cy="0" r=".75"><stop offset="0" stop-color="{th["halo"]}" '
             f'stop-opacity="{th["halo_op"]}"/><stop offset="1" stop-color="{th["halo"]}" stop-opacity="0"/></radialGradient>')
    o.append(f'<linearGradient id="edge" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{th["accent"]}" stop-opacity="0"/>'
             f'<stop offset=".5" stop-color="{th["accent"]}" stop-opacity=".85"/>'
             f'<stop offset="1" stop-color="{th["accent"]}" stop-opacity="0"/></linearGradient>')
    o.append(f'<linearGradient id="pad" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{th["paddle1"]}"/>'
             f'<stop offset=".5" stop-color="{th["paddle0"]}"/><stop offset="1" stop-color="{th["paddle1"]}"/></linearGradient>')
    o.append('<linearGradient id="padsheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".55"/>'
             '<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>')
    o.append(f'<linearGradient id="prog" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{th["paddle1"]}"/>'
             f'<stop offset="1" stop-color="{th["accent"]}"/></linearGradient>')
    for i, c in enumerate(th["levels"]):
        o.append(f'<linearGradient id="L{i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{mix(c, "#ffffff", .30)}"/>'
                 f'<stop offset=".55" stop-color="{c}"/><stop offset="1" stop-color="{mix(c, "#000000", .18)}"/></linearGradient>')
    if th["glow"]:
        o.append('<filter id="glow" x="-120%" y="-120%" width="340%" height="340%">'
                 '<feGaussianBlur stdDeviation="2.4" result="b"/>'
                 '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>')
    if th["shadow"]:
        o.append('<filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">'
                 '<feDropShadow dx="0" dy="1.2" stdDeviation="1.1" flood-color="#0b3a4a" flood-opacity=".22"/></filter>')
    o.append('</defs>')

    # card
    o.append(f'<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="14" fill="url(#bg)" stroke="{th["border"]}"/>')
    o.append(f'<rect x=".5" y=".5" width="{W-1}" height="{H-1}" rx="14" fill="url(#halo)"/>')
    o.append(f'<rect x="60" y="0" width="{W-120}" height="1.5" fill="url(#edge)"/>')

    # twinkling starfield
    for _ in range(54):
        a, b = rng.uniform(.08, .22), rng.uniform(.35, .65)
        o.append(f'<circle cx="{fmt(rng.uniform(20, W-20))}" cy="{fmt(rng.uniform(178, H-34))}" '
                 f'r="{fmt(rng.uniform(.4, 1.1))}" fill="{th["star"]}" opacity="{a:.2f}">'
                 f'<animate attributeName="opacity" values="{a:.2f};{b:.2f};{a:.2f}" dur="{rng.uniform(3, 7):.1f}s" '
                 f'begin="-{rng.uniform(0, 6):.1f}s" repeatCount="indefinite"/></circle>')

    # HUD
    o.append(f'<text x="30" y="31" {mono} font-size="12.5" font-weight="700" letter-spacing="3.2" fill="{th["accent"]}">'
             f'COMMIT BREAKER<tspan font-weight="400" letter-spacing="1.2" fill="{th["dim"]}">  /  @{html.escape(user)}</tspan></text>')
    o.append(f'<text x="{W-88}" y="31" {mono} font-size="10.5" letter-spacing="2.4" text-anchor="end" fill="{th["dim"]}">SCORE</text>')
    o.append(f'<rect x="30" y="{CEIL}" width="{W-60}" height="1" fill="{th["rule"]}"/>')

    # progress line (fills as bricks are cleared)
    cleared, ptimes, pvals = 0, [0, INTRO], ["0", "0"]
    for f, cells_hit in events:
        cleared += len(cells_hit)
        ptimes.append(INTRO + f / FPS)
        pvals.append(fmt((W - 60) * cleared / len(bricks)))
    ptimes.append(D)
    pvals.append(pvals[-1])
    o.append(f'<rect x="30" y="{CEIL - .5}" width="0" height="2" rx="1" fill="url(#prog)"{glow}>'
             f'<animate attributeName="width" values="{";".join(pvals)}" keyTimes="{keytimes(ptimes)}" {dur}/></rect>')

    # month labels
    starts = [col for col in range(COLS) if col in week_start
              and (col - 1 not in week_start or week_start[col - 1][1] != week_start[col][1])]
    if len(starts) > 1 and starts[1] - starts[0] < 3:      # drop a clipped leading month, as GitHub does
        starts.pop(0)
    for col in starts:
        if col <= COLS - 2:
            o.append(f'<text x="{fmt(X0 + col * PITCH)}" y="{Y0 - 8}" {mono} font-size="9" letter-spacing=".8" '
                     f'fill="{th["dim"]}" opacity=".8">{MONTHS[week_start[col][1] - 1].upper()}</text>')

    # empty days
    for (r, c) in cells:
        if (r, c) not in bricks and r < ROWS:
            o.append(f'<rect x="{fmt(X0 + c*PITCH + 4)}" y="{fmt(Y0 + r*PITCH + 4)}" width="4.5" height="4.5" '
                     f'rx="1.5" fill="{th["empty"]}"/>')

    # bricks (one shadowed layer in the light theme), impact flashes, debris
    brick_svg, fx_svg = [], []
    for (r, c), (n, lvl) in sorted(bricks.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        cx, cy = X0 + c * PITCH + BRICK / 2, Y0 + r * PITCH + BRICK / 2
        li = max(0, min(3, lvl - 1))
        color = th["levels"][li]
        t_in = 0.15 + 1.2 * (c / COLS) + 0.03 * r
        t_hit = hit_time[(r, c)]
        k = keytimes([0, t_in, t_hit, t_hit + 0.07, t_hit + 0.26, D])
        g_glow = glow if li == 3 else ""
        brick_svg.append(f'<g transform="translate({fmt(cx)} {fmt(cy)})"{g_glow}><g>'
                 f'<animateTransform attributeName="transform" type="scale" values="0;1;1;1.4;0;0" keyTimes="{k}" {dur}/>'
                 f'<rect x="{-BRICK/2}" y="{-BRICK/2}" width="{BRICK}" height="{BRICK}" rx="2.6" fill="url(#L{li})"/></g></g>')
        kf = keytimes([0, t_hit - 0.01, t_hit, t_hit + 0.16, t_hit + 0.4, D])
        fx_svg.append(f'<circle cx="{fmt(cx)}" cy="{fmt(cy)}" r="0" fill="{th["flash"]}" stroke="{color}" stroke-width="1.2" '
                 f'fill-opacity="0" stroke-opacity="0">'
                 f'<animate attributeName="r" values="0;0;3;13;13;13" keyTimes="{kf}" {dur}/>'
                 f'<animate attributeName="fill-opacity" values="0;0;.55;0;0;0" keyTimes="{kf}" {dur}/>'
                 f'<animate attributeName="stroke-opacity" values="0;0;.95;.5;0;0" keyTimes="{kf}" {dur}/></circle>')
        kp = keytimes([0, t_hit - 0.01, t_hit, t_hit + 0.5, D])
        for _ in range(2):
            ang = rng.uniform(0, math.tau)
            dist = rng.uniform(11, 21)
            dx, dy = fmt(math.cos(ang) * dist), fmt(math.sin(ang) * dist + 5)
            fx_svg.append(f'<rect x="{fmt(cx)}" y="{fmt(cy)}" width="2.2" height="2.2" rx=".6" fill="{color}" opacity="0">'
                     f'<animateTransform attributeName="transform" type="translate" '
                     f'values="0 0;0 0;0 0;{dx} {dy};{dx} {dy}" keyTimes="{kp}" {dur}/>'
                     f'<animate attributeName="opacity" values="0;0;1;0;0" keyTimes="{kp}" {dur}/></rect>')

    o.append(f'<g{lift}>')
    o.extend(brick_svg)
    o.append('</g>')
    o.extend(fx_svg)

    # paddle ripples
    for f, x in bounces:
        t = INTRO + f / FPS
        kr = keytimes([0, t - 0.01, t, t + 0.35, D])
        o.append(f'<ellipse cx="{fmt(x)}" cy="{PADDLE_Y}" rx="0" ry="0" fill="none" stroke="{th["accent"]}" '
                 f'stroke-width="1.1" opacity="0">'
                 f'<animate attributeName="rx" values="0;0;6;40;40" keyTimes="{kr}" {dur}/>'
                 f'<animate attributeName="ry" values="0;0;1;7;7" keyTimes="{kr}" {dur}/>'
                 f'<animate attributeName="opacity" values="0;0;.8;0;0" keyTimes="{kr}" {dur}/></ellipse>')

    # score readout: one <text> per hit event, swapped discretely
    score, readouts = 0, [(0.0, 0)]
    for f, cells_hit in events:
        score += sum(bricks[rc][0] for rc in cells_hit)
        readouts.append((INTRO + f / FPS, score))
    for i, (t_s, sc) in enumerate(readouts):
        if i == 0:
            vals, ts = "visible;hidden;hidden", [0, readouts[1][0], D]
        elif i == len(readouts) - 1:
            vals, ts = "hidden;visible;visible", [0, t_s, D]
        else:
            vals, ts = "hidden;visible;hidden;hidden", [0, t_s, readouts[i + 1][0], D]
        o.append(f'<text x="{W-30}" y="31" {mono} font-size="13" font-weight="700" letter-spacing="1.4" '
                 f'text-anchor="end" fill="{th["text"]}" visibility="hidden">{sc:04d}'
                 f'<animate attributeName="visibility" calcMode="discrete" values="{vals}" '
                 f'keyTimes="{keytimes(ts)}" {dur}/></text>')

    # footer: caption + legend
    o.append(f'<text x="30" y="{H-17}" {mono} font-size="10.5" letter-spacing=".4" fill="{th["dim"]}">'
             f'{total} contributions in the last 12 months  ·  one brick per active day</text>')
    lx = W - 30 - 4 * 15 - 30
    o.append(f'<text x="{lx - 8}" y="{H-17}" {mono} font-size="10" text-anchor="end" fill="{th["dim"]}">Less</text>')
    for i in range(4):
        o.append(f'<rect x="{lx + i * 15}" y="{H-26}" width="11" height="11" rx="2.4" fill="url(#L{i})"/>')
    o.append(f'<text x="{lx + 4 * 15 + 3}" y="{H-17}" {mono} font-size="10" fill="{th["dim"]}">More</text>')

    # paddle
    n = int(round(D * OUT_FPS))

    def sample(i):
        f = int((i / OUT_FPS - INTRO) * FPS)
        return frames[max(0, min(last_frame, f))]

    pad_pos = ";".join(f"{fmt(sample(i)[2])} {fmt(PADDLE_Y)}" for i in range(n + 1))
    o.append(f'<g transform="translate({fmt(frames[0][2])} {fmt(PADDLE_Y)})"{glow or lift}>'
             f'<animateTransform attributeName="transform" type="translate" values="{pad_pos}" {dur}/>'
             f'<rect x="{-PADDLE_W/2}" y="0" width="{PADDLE_W}" height="{PADDLE_H}" rx="2.5" fill="url(#pad)"/>'
             f'<rect x="{-PADDLE_W/2 + 3}" y=".6" width="{PADDLE_W - 6}" height="1.6" rx=".8" fill="url(#padsheen)"/></g>')

    # ball and trail
    ball_pos = ";".join(f"{fmt(sample(i)[0])} {fmt(sample(i)[1])}" for i in range(n + 1))
    vis = keytimes([0, INTRO - 0.01, INTRO, t_end - 0.05, t_end + 0.2, D])
    for k in range(TRAIL, 0, -1):
        a = f"{.46 * (1 - k / (TRAIL + 1)) ** 1.6:.2f}"
        o.append(f'<circle r="{fmt(RADIUS * (1 - k * 0.085))}" fill="{th["accent"]}" opacity="0">'
                 f'<animateTransform attributeName="transform" type="translate" begin="{k*0.022:.3f}s" values="{ball_pos}" {dur}/>'
                 f'<animate attributeName="opacity" begin="{k*0.022:.3f}s" values="0;0;{a};{a};0;0" keyTimes="{vis}" {dur}/></circle>')
    o.append(f'<circle r="{RADIUS}" fill="{th["ball"]}" opacity="0"{glow or lift}>'
             f'<animateTransform attributeName="transform" type="translate" values="{ball_pos}" {dur}/>'
             f'<animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="{vis}" {dur}/></circle>')

    # burning phases: an amber halo on the ball
    ts, vals = [0], ["0"]
    for a, b in fire:
        b = min(b, last_frame)
        ts += [INTRO + a / FPS, INTRO + a / FPS + 0.05, INTRO + b / FPS, INTRO + b / FPS + 0.1]
        vals += ["0", ".95", ".95", "0"]
    ts.append(D)
    vals.append("0")
    o.append(f'<circle r="{RADIUS + 2.6}" fill="{th["fire"]}" opacity="0"{glow}>'
             f'<animateTransform attributeName="transform" type="translate" values="{ball_pos}" {dur}/>'
             f'<animate attributeName="opacity" values="{";".join(vals)}" keyTimes="{keytimes(ts)}" {dur}/></circle>')

    # stage clear
    kc = keytimes([0, t_end, t_end + 0.6, D - 0.6, D])
    o.append(f'<g opacity="0" text-anchor="middle" {mono}>'
             f'<animate attributeName="opacity" values="0;0;1;1;0" keyTimes="{kc}" {dur}/>'
             f'<text x="{W/2}" y="226" font-size="24" font-weight="700" letter-spacing="9" fill="{th["accent"]}"{glow}>STAGE CLEAR</text>'
             f'<rect x="{W/2 - 70}" y="238" width="140" height="1" fill="url(#edge)"/>'
             f'<text x="{W/2}" y="258" font-size="11" letter-spacing="3" fill="{th["dim"]}">{total} COMMITS CLEARED</text></g>')
    o.append('</svg>')
    stats = dict(bricks=len(bricks), play=round(t_play, 1), loop=round(D, 1), hit_events=len(events),
                 paddle_bounces=len(bounces), total=total)
    return "\n".join(o), stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="TingdeLiu")
    ap.add_argument("--out", default="dist")
    args = ap.parse_args()
    cells, week_start = fetch_contributions(args.user)
    os.makedirs(args.out, exist_ok=True)
    for name in THEMES:
        svg, stats = render(cells, week_start, name, args.user)
        path = os.path.join(args.out, f"breakout-{name}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(path, f"{len(svg) / 1024:.0f} KB", stats)


if __name__ == "__main__":
    main()
