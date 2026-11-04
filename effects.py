"""
effects.py — библиотека GPU-эффектов SoundGrip (3 звуковых + игровые).

Рендер: один RenderContext с кастомными GLSL ES 100 шейдерами (одинаково
работает на ПК и Android GLES2). Каждая частица — квад из 2 треугольников
в общем Mesh-батче; форма считается процедурно во фрагментном шейдере
по UV квада, поворот квада задаётся на CPU.

Формы (shape id в aux.x):
    0  spark    искра (вытянута, мерцает)
    1  ring     кольцо (aux.y=радиус, aux.z=толщина, в UV квада)
    2  drop     круглое свечение / капля
    3  flash    яркая вспышка
    8  beam     мягкий луч
    9  cube     куб (фасетная заливка + грань)
    10 ball     шар (диффузное затенение + блик)
    11 star     космическая сфера-звезда бочки (glow + вспышка + лучи)
    12 pixel    квадратный осколок (взрыв куба)
    13 hole     чёрная дыра с неоновым вихрем (взрыв параллелограмма)
    17 cube     куб-фигура: ОДИН цельный куб с выпуклыми гранями, бликом и
                тенями (та же пластика, что у звезды и пузыря); осколки —
                такие же мелкие кубики, падают на пол
    18 water    водяной пузырь (фигура): жидкий контур перетекает во времени,
                при разрезе — случайный всплеск капель (крупные падают
                на пол, растекаются и тают)
    20-23 SDF-таргеты osu!: куб/сфера/пирамида/параллелограмм (ray marching)

Эффекты (публичный API — привязаны к звуку):
    emit_kick      бочка/бас (30-160 Гц) → сфера-звезда с вспышкой
    emit_snare     снейр (200-2000 Гц)   → вспышка + кольцо + искры
    emit_hat       хэт (4-12 кГц)        → лучи + искры
    emit_onset     ЛЮБОЙ другой звук     → кольцо + вспышка + искры
                   (цвет = спектральный центроид, hue)
    set_kick_energy / trigger_flash     → uniform для сферы бочки

osu!-геймплей (ритм-экшен, спавн фигур в такт бочки):
    spawn_target                 → SDF-фигура в случайной точке + approach-круг
    hit_targets_along/hit_target → свайп попал: уникальный взрыв по форме
    deactivate_target/pop_expired→ кольцо сжалось без свайпа = Miss
    add_trail                    → след лезвия
    update(dt, audio_time), clear()
"""

import colorsys
import math
import random

import numpy as np
from kivy.clock import Clock
from kivy.graphics import Mesh, RenderContext
from kivy.graphics.instructions import Callback
from kivy.graphics.opengl import (
    GL_ONE, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA, glBlendFunc)
from kivy.uix.widget import Widget
from kivy.uix.floatlayout import FloatLayout


def _hue_rgb(h):
    """Hue [0..1] -> неоновый RGB (насыщенный, звонкий).

    h=0 тёплый (бас), h~0.33 зелёный (середина), h~0.66+ холодный (верх).
    """
    r, g, b = colorsys.hsv_to_rgb(min(0.92, max(0.0, h)), 0.85, 1.0)
    return (r, g, b)

VERTEX_SHADER = '''$HEADER$
attribute vec2 a_pos;
attribute vec2 a_uv;
attribute vec4 a_color;
attribute vec4 a_aux;
attribute float a_z;  // персональная глубина частицы (з-слой)

varying vec2 v_uv;
varying vec4 v_color;
varying vec4 v_aux;

void main(void) {
    v_uv = a_uv;
    v_color = a_color;
    v_aux = a_aux;
    gl_Position = projection_mat * modelview_mat * vec4(a_pos, a_z, 1.0);
}
'''

FRAGMENT_SHADER = '''$HEADER$
uniform float fx_time;
uniform float u_kick_energy;      // уровень баса 30-160 Гц (размер/мощь сферы)
uniform float u_flash_intensity;  // вспышка ядра в момент тика метронома

varying vec2 v_uv;
varying vec4 v_color;
varying vec4 v_aux;

/* --- SDF (неоновые 3D-фигуры таргетов, osu!-стиль) ----------------------- */
float sdBox(vec3 p, vec3 b) {
    vec3 q = abs(p) - b;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}
float sdSphere(vec3 p, float r) {
    return length(p) - r;
}
float sdPyramid(vec3 p, float h) {
    float m2 = h * h + 0.25;
    p.xz = abs(p.xz);
    p.xz = (p.z > p.x) ? p.zy : p.xz;
    p.xz -= 0.5;
    vec3 q = vec3(p.z, h * p.y - 0.5 * p.x, h * p.x + 0.5 * p.y);
    float s = max(-q.x, 0.0);
    float t = clamp((q.y - 0.5 * p.z) / (m2 + 0.25), 0.0, 1.0);
    float a = m2 * (q.x + s) * (q.x + s) + q.y * q.y;
    float b = m2 * (q.x + 0.5 * t) * (q.x + 0.5 * t) + (q.y - m2 * t) * (q.y - m2 * t);
    float d2 = min(q.y, -q.x * m2 - q.y) * 0.5;
    return sqrt((min(d2, a) / max(1.0, m2)) * sign(max(q.y, -q.x * m2 - q.y)));
}
float sdPara(vec3 p, vec3 b, float skew) {
    p.x -= p.y * skew;
    return sdBox(p, b) - 0.02;
}
float mapShape(vec3 p, float kind, float scl) {
    if (kind < 0.5) {
        return sdBox(p, vec3(0.62, 0.62, 0.62) * scl);
    }
    if (kind < 1.5) {
        return sdSphere(p, 0.70 * scl);
    }
    if (kind < 2.5) {
        return sdPyramid(p, 1.05 * scl);
    }
    return sdPara(p, vec3(0.58, 0.40, 0.40) * scl, 0.9);
}
vec3 rotY(vec3 p, float a) {
    float c = cos(a);
    float s = sin(a);
    return vec3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z);
}
vec3 calcNormal(vec3 p, float kind, float scl) {
    vec2 e = vec2(0.0024, 0.0);
    return normalize(vec3(
        mapShape(p + e.xyy, kind, scl) - mapShape(p - e.xyy, kind, scl),
        mapShape(p + e.yxy, kind, scl) - mapShape(p - e.yxy, kind, scl),
        mapShape(p + e.yyx, kind, scl) - mapShape(p - e.yyx, kind, scl)));
}

/* --- КУБ-ФИГУРА: КУБИК-РУБИК 3×3..5×5 / КЛЕТКА-КУБИК 1×1 ---------------- */
float sdSeg2(vec2 p, vec2 a, vec2 b) {
    vec2 pa = p - a;
    vec2 ba = b - a;
    float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-5), 0.0, 1.0);
    return length(pa - ba * h);
}
/* ортографическая проекция точки куба с поворотом yaw (вокруг Y) и pitch */
vec2 cubeProj(vec3 p, float cy, float sy, float cp, float sp) {
    float x1 = p.x * cy + p.z * sy;
    float z1 = -p.x * sy + p.z * cy;
    return vec2(x1, p.y * cp - z1 * sp);
}
/* та же проекция + глубина (z к зрителю) — для заливки граней куба:
   по (screen.xy, depth) выбирается ближайшая к камере грань пикселя */
vec3 cubeProjD(vec3 p, float cy, float sy, float cp, float sp) {
    float x1 = p.x * cy + p.z * sy;
    float z1 = -p.x * sy + p.z * cy;
    return vec3(x1, p.y * cp - z1 * sp, p.y * sp + z1 * cp);
}

void main(void) {
    float d = length(v_uv);
    float a = 0.0;
    float boost = 1.0;
    vec3 rgb = v_color.rgb;
    float sh = v_aux.x;
    float cutoff = 1.0;
    bool cube_fragment = false;  // осколок куба 1x1 (grid == 1.0) — нужен для discard/alpha

    if (sh < 0.5) {
        /* искра */
        vec2 q = vec2(v_uv.x * 0.42, v_uv.y * 1.15);
        float e = length(q);
        float core = 1.0 - smoothstep(0.0, 0.30, e);
        a = (1.0 - smoothstep(0.0, 1.0, e)) * 0.85 + core * 1.10;
        boost = 1.0 + 1.6 * core;
        float h = fract(sin(dot(gl_FragCoord.xy,
                    vec2(12.9898, 78.233))) * 43758.5453);
        a *= 0.80 + 0.25 * sin(fx_time * 40.0 + h * 6.2831);
    } else if (sh < 1.5) {
        /* кольцо */
        float r = max(v_aux.y, 0.001);
        float th = max(v_aux.z, 0.02);
        float ring = 1.0 - smoothstep(0.0, th, abs(d - r));
        a = ring * 1.15 + (1.0 - smoothstep(0.0, r, d)) * 0.10;
        boost = 1.0 + 0.8 * ring;
    } else if (sh < 2.5) {
        /* капля / свечение */
        float core = 1.0 - smoothstep(0.0, 0.45, d);
        a = (1.0 - smoothstep(0.0, 1.0, d)) * 0.80 + core * 0.90;
        boost = 1.0 + 1.2 * core;
    } else if (sh < 3.5) {
        /* вспышка */
        float core = 1.0 - smoothstep(0.0, 0.50, d);
        a = (1.0 - smoothstep(0.15, 1.0, d)) * 0.90 + core * 1.20;
        boost = 1.2 + 1.8 * core;
    } else if (sh < 8.5) {
        /* мягкий луч */
        float across = 1.0 - smoothstep(0.0, 0.55, abs(v_uv.y));
        float taper = 1.0 - smoothstep(0.35, 1.0, abs(v_uv.x));
        a = across * taper * 0.85;
        boost = 1.0 + 0.5 * across;
    } else if (sh < 9.5) {
        /* куб: честный заполненный текстурный квадрат от края до края,
           без скругления углов, без темных граней, чистый неон */
        float s = max(abs(v_uv.x), abs(v_uv.y));
        float square = 1.0 - smoothstep(0.90, 1.0, s);
        a = square * 1.0;
        boost = 1.0;
        rgb = v_color.rgb;
        cutoff = 1.5;   /* sqrt(2) ≈ 1.414 — углы квада не отрезаем */
    } else if (sh < 10.5) {
        /* шар: сферическое затенение + блик */
        float r = 0.88;
        float sph = 1.0 - smoothstep(0.0, r, d);
        float nx = v_uv.x / max(r, 0.001);
        float ny = v_uv.y / max(r, 0.001);
        float diff = clamp(0.40 + 0.85 * ny, 0.0, 1.25);
        float spec = pow(clamp(ny * 0.82 - nx * 0.55, 0.0, 1.0), 10.0);
        a = sph * 0.95;
        boost = diff + spec * 1.3;
    } else if (sh < 11.5) {
        /* космическая сфера-звезда бочки: объёмное inverse-square свечение
           + белая вспышка ядра в такт + «дышащие» радиальные протуберанцы */
        float g = 0.10 / (d * d + 0.012);             // 1/r^2 glow (объём)
        float ang = atan(v_uv.y, v_uv.x);
        /* лучи-протуберанцы — вращаются и дышат со временем */
        float rays = 0.0;
        for (int i = 0; i < 8; i++) {
            float a0 = 0.7854 * float(i) + fx_time * 0.7;
            rays += pow(max(cos(ang - a0), 0.0), 4.0);
        }
        float breath = 0.82 + 0.18 * sin(fx_time * 2.4 + ang * 3.0);
        float core = 1.0 - smoothstep(0.0, 0.20, d);  // раскалённое ядро
        float ke = clamp(u_kick_energy, 0.0, 2.0);    // энергия баса: мощь и раздув
        float fl = clamp(u_flash_intensity, 0.0, 2.0);// вспышка: белый ореол
        /* ядро выгорает тёплым → белым вместе со вспышкой и басом */
        vec3 warm = vec3(1.0, 0.78, 0.45);
        float melt = clamp(fl * 0.6 + ke * 0.35, 0.0, 1.0);
        rgb = mix(rgb, mix(rgb, warm, 0.5), melt);
        rgb = mix(rgb, vec3(1.0, 0.97, 0.92), clamp(fl, 0.0, 1.0) * 0.9);
        a = clamp(g * 0.55 + rays * 0.05 * breath
                  + core * (0.55 + fl * 1.15) + ke * 0.22, 0.0, 1.35);
        boost = 1.0 + fl * 1.6 + ke * 0.7
                + core * 1.2 + rays * 0.15 * breath;
        /* сфера затухает точно до нуля к границе света: без жёсткого края
           и без хвоста на всю площадь квадрата (убирает серые подложки) */
        cutoff = 0.5;
        a *= (1.0 - smoothstep(0.16, cutoff, d));
    } else if (sh < 12.5) {
        /* квадратный пиксель-осколок (взрыв куба) */
        float s = max(abs(v_uv.x), abs(v_uv.y));
        float px = 1.0 - smoothstep(0.70, 0.96, s);
        a = px * 1.25;
        boost = 1.0 + 0.7 * px;
    } else if (sh < 13.5) {
        /* чёрная дыра: тёмное ядро + неоновый вихрь (параллелограмм) */
        float r2 = length(v_uv);
        float disc = 1.0 - smoothstep(0.0, 0.26, r2);
        float sw = sin(atan(v_uv.y, v_uv.x) * 6.0 + fx_time * 9.0);
        float ringn = (1.0 - smoothstep(0.0, 0.10, abs(r2 - 0.48)))
                      * (0.75 + 0.25 * sw);
        a = disc * 1.4 + ringn * 1.05;
        rgb = rgb * (0.15 + 0.85 * ringn);
        boost = 1.0 + ringn * 0.6;
    } else if (sh < 14.5) {
        /* Genesis-звезда (точный порт space_effect4.glsl): диск с лимбовым
           затемнением + ободок + корона. aux.y = радиус в долях высоты
           экрана, aux.z = полуквад в тех же единицах, aux.w = тип цвета
           (0..5), fade — в a_color.a. */
        vec2 p = v_uv * v_aux.z;      /* uv квада = [-1,1]: центр = центр фигуры */
        float dist = length(p) + 0.0001;
        float starScale = v_aux.y / 0.09;
        float targetRadius = 0.09 * starScale * clamp(v_color.a, 0.0, 1.0);
        float maxGlowDist = targetRadius + 0.15 * starScale;
        if (dist > maxGlowDist) {
            discard;
        }
        float st = v_aux.w;
        vec3 starColor = vec3(1.0);
        float brightness = 3.5;
        float coronaFalloff = 4.0;
        if (st > 5.5) {
            /* STAR_CUSTOM: цвет прогрессии мелодии из CPU (v_color.rgb),
               brightness мягче палитры — гамма не выгорает в белое */
            starColor = v_color.rgb;
            brightness = 2.0;
            coronaFalloff = 6.0;
        } else if (st < 0.5) {
            starColor = vec3(1.0, 0.0, 0.0);
            brightness = 4.0; coronaFalloff = 8.5;
        } else if (st < 1.5) {
            starColor = vec3(1.0, 0.65, 0.0);
            brightness = 1.8; coronaFalloff = 8.0;
        } else if (st < 2.5) {
            starColor = vec3(0.55, 0.85, 1.0);
            brightness = 3.5; coronaFalloff = 5.5;
        } else if (st < 3.5) {
            starColor = vec3(0.02, 0.35, 1.0);
            brightness = 3.8; coronaFalloff = 8.5;
        } else if (st < 4.5) {
            starColor = vec3(0.35, 0.0, 0.40);
            brightness = 2.0; coronaFalloff = 10.0;
        } else {
            starColor = vec3(0.0, 0.22, 0.02);
            brightness = 1.6; coronaFalloff = 9.0;
        }
        vec3 finalColor = vec3(0.0);
        float distFactor = dist / (targetRadius + 0.0001);
        float centerDarkness = 0.4;
        if (st < 0.5) { centerDarkness = 0.25; }
        if (st < 1.5) { centerDarkness = 0.20; }
        if (st > 3.5) { centerDarkness = 0.12; }
        float limbDarkening = mix(centerDarkness, 1.0,
                                  pow(clamp(distFactor, 0.0, 1.0), 2.0));
        float edgeRim = smoothstep(0.5, 1.0, distFactor) * 1.5;
        vec3 diskColor = starColor * (limbDarkening + edgeRim) * brightness;
        float bodyMask = smoothstep(targetRadius,
                                    targetRadius - 0.008 * starScale, dist);
        finalColor += diskColor * bodyMask;
        float coronaFactor = clamp((dist - targetRadius)
                                   / (maxGlowDist - targetRadius + 0.0001),
                                   0.0, 1.0);
        float coronaGlow = pow(1.0 - coronaFactor, coronaFalloff);
        float innerGlowDensity = (st < 1.5 || st > 3.5) ? 1.5 : 0.7;
        float innerGlow = smoothstep(1.0, 0.0, distFactor)
                          * innerGlowDensity * clamp(v_color.a, 0.0, 1.0);
        finalColor += starColor * (coronaGlow + innerGlow)
                      * (brightness * 1.5) * clamp(v_color.a, 0.0, 1.0);
        rgb = clamp(finalColor, vec3(0.0), vec3(1.0));
        a = 1.0;
        boost = 1.0;
        cutoff = 1.0;
    } else if (sh < 15.5) {
        /* Половинка разрезанной звезды: полудиск, плоский срез вдоль
           локальной оси X, светящаяся кромка среза. Половинки скользят
           в стороны вдоль линии среза (разъезд), затем тают. */
        vec2 p = v_uv;
        float r = length(p);
        float halfMask = smoothstep(-0.06, 0.03, p.y);
        float disc = 1.0 - smoothstep(0.74, 0.98, r);
        if (disc * halfMask <= 0.004) {
            discard;
        }
        float rim = smoothstep(0.40, 0.95, r);
        float cutGlow = exp(-max(p.y, 0.0) * 9.0);
        vec3 body = v_color.rgb * (0.60 + 0.40 * rim);
        vec3 col = body + vec3(1.0) * cutGlow * 0.55 + v_color.rgb * rim * 0.7;
        rgb = clamp(col, vec3(0.0), vec3(1.0));
        a = disc * halfMask;
        boost = 1.0;
        cutoff = 1.0;
    } else if (sh < 16.5) {
        /* Остывающая целая звезда (мост сфера -> половинки): тот же профиль
           тела, что у половинки, но полный диск + мягкая цветная корона.
           Гаснет за ~0.12 с, пока половинки проявляются из нуля, — переход
           без видимого скачка яркости и радиуса. */
        vec2 p = v_uv;
        float r = length(p);
        float disc = 1.0 - smoothstep(0.74, 0.98, r);
        if (disc <= 0.004) {
            discard;
        }
        float rim = smoothstep(0.40, 0.95, r);
        float corona = exp(-max(r - 0.55, 0.0) * 5.0) * 0.35;
        vec3 body = v_color.rgb * (0.60 + 0.40 * rim);
        vec3 col = body + vec3(1.0) * rim * 0.45 + v_color.rgb * corona;
        rgb = clamp(col, vec3(0.0), vec3(1.0));
        a = disc;
        boost = 1.0;
        cutoff = 1.0;
    } else if (sh < 17.5) {
        /* КУБ = КУБИК РУБИКА 3x3: одна цельная фигура, каждая видимая грань
           разбита сеткой на 3x3 наклеек цвета фигуры с тёмно-серыми швами.
           aux.y = yaw (вокруг вертикали), aux.z = pitch (наклон),
           aux.w = grid: >= 0.5 → кубик Рубика (фигура ноты и осколки),
           < 0.5 → полый контур из 12 рёбер (резерв, сейчас не спавнится).
           uv квада = [-1,1] -> локальные координаты точки куба. */
        float yaw = v_aux.y;
        float pitch = v_aux.z;
        float grid = v_aux.w;
        float cw = cos(yaw);
        float sw = sin(yaw);
        float cp = cos(pitch);
        float sp = sin(pitch);
        vec2 q = v_uv * 1.06;                    /* запас под свечение */
        if (grid >= 0.5) {
            /* ===== ЦЕЛЬНЫЙ КУБ: пиксель раскладывается на (u,v) ближайшей
               к зрителю грани (ортопроекция + глубина); дальше грань
               рисуется выпуклой — фаска, диффуз по нормали и блики. */
            vec2 pc = q / 0.70;                  /* обратно в кубич. проекцию */
            float mrg = 0.18;                    /* запас: грань вне пикселя
                                                     всё равно участвует в
                                                     заполнении шва (covAny) */
            bool hit = false;
            float bdep = -1e9;
            float covAny = 0.0;   /* ОБЪЕДИНЕНИЕ покрытий всех видимых граней:
                                     закрывает швы между гранями (см. ниже) */
            vec2 fA = vec2(0.0);
            vec2 fB = vec2(0.0);
            float fu = 0.0;
            float fv = 0.0;
            vec3 fn = vec3(0.0, 0.0, 1.0);
            float minOv = 1e9;   /* дистанция пикселя до силуэта (для неоновой ауры) */
            for (int fam = 0; fam < 3; fam++) {
                for (int si = 0; si < 2; si++) {
                    float sgn = (si == 0) ? -1.0 : 1.0;
                    vec3 o3 = vec3(0.0);
                    vec3 a3 = vec3(0.0);
                    vec3 b3 = vec3(0.0);
                    if (fam == 0) {
                        o3 = vec3(sgn, 0.0, 0.0);
                        a3 = vec3(0.0, 1.0, 0.0);
                        b3 = vec3(0.0, 0.0, 1.0);
                    }
                    if (fam == 1) {
                        o3 = vec3(0.0, sgn, 0.0);
                        a3 = vec3(1.0, 0.0, 0.0);
                        b3 = vec3(0.0, 0.0, 1.0);
                    }
                    if (fam == 2) {
                        o3 = vec3(0.0, 0.0, sgn);
                        a3 = vec3(1.0, 0.0, 0.0);
                        b3 = vec3(0.0, 1.0, 0.0);
                    }
                    vec3 O = cubeProjD(o3, cw, sw, cp, sp);
                    vec3 A = cubeProjD(a3, cw, sw, cp, sp);
                    vec3 B = cubeProjD(b3, cw, sw, cp, sp);
                    float det = A.x * B.y - A.y * B.x;
                    if (abs(det) < 1e-4) {
                        continue;                /* грань почти ребром */
                    }
                    vec2 dlt = pc - O.xy;
                    float u = (dlt.x * B.y - dlt.y * B.x) / det;
                    float v = (A.x * dlt.y - A.y * dlt.x) / det;
                    /* ПРЯМОУГОЛЬНАЯ (не радиальная!) дистанция до квада грани.
                       В UV-координатах ЭТОЙ грани берём выход за квад [-1,1]
                       по каждой оси отдельно: ovUV = max(abs(uv)-1, 0). Норма
                       Chebyshev (max по осям) даёт контуры, ПАРАЛЛЕЛЬНЫЕ
                       рёбрам куба, а не круги. Раньше была евклидова
                       length(pc-closest) — на углах силуэта она скруглялась
                       и рисовала круглый «шар» вокруг куба. Переводим в
                       экранные px по масштабу грани (средняя длина проекций
                       осей A,B). min по всем 6 граням = прямоугольный
                       контур силуэта (0 внутри). Копим ДО continue. */
                    vec2 ovUV = max(abs(vec2(u, v)) - 1.0, vec2(0.0));
                    float faceScale = 0.5 * (length(A.xy) + length(B.xy));
                    minOv = min(minOv, max(ovUV.x, ovUV.y) * faceScale);
                    if (abs(u) > 1.0 + mrg || abs(v) > 1.0 + mrg) {
                        continue;
                    }
                    /* покрытие ЭТОЙ грани. Ширина AA ограничена ЧЕТВЕРТЬЮ
                       размера грани: у грани, видимой почти ребром, прежний
                       clamp(…, 0.15) давал сглаживание шире самой грани --
                       грань целиком уходила в полупрозрачность и давала
                       тёмное смазанное пятно (чёрную полосу) поперёк куба. */
                    float la_i = max(length(A.xy) * 0.70, 1e-4);
                    float lb_i = max(length(B.xy) * 0.70, 1e-4);
                    float au = min(clamp(0.020 / la_i, 0.004, 0.15),
                                   0.25 * la_i);
                    float av = min(clamp(0.020 / lb_i, 0.004, 0.15),
                                   0.25 * lb_i);
                    float cov_i = min(smoothstep(-au, au, 1.0 - abs(u)),
                                      smoothstep(-av, av, 1.0 - abs(v)));
                    covAny = max(covAny, cov_i);
                    /* ГЛАВНОЕ: основной гранью берём ТОЛЬКО ту, что реально
                       накрывает пиксель (|u|, |v| <= 1). Раньше по глубине
                       выбиралась любая грань в пределах запаса mrg -- и для
                       пикселя ВНЕ неё покрытие было ~0, из-за чего куб
                       покрывался жёсткими чёрными полосами. Грань вне
                       пикселя теперь участвует только в заполнении шва. */
                    if (abs(u) > 1.0 || abs(v) > 1.0) {
                        continue;
                    }
                    float dep = O.z + u * A.z + v * B.z;
                    if (!hit || dep > bdep) {    /* ближайшая к зрителю */
                        hit = true;
                        bdep = dep;
                        fA = A.xy;
                        fB = B.xy;
                        fu = u;
                        fv = v;
                        fn = cubeProjD(o3, cw, sw, cp, sp); /* = нормаль */
                    }
                }
            }
            if (!hit) {
                /* ===== ВНЕ КУБА: НЕОНОВАЯ АУРА ВОКРУГ СИЛУЭТА =====
                   minOv — экранное расстояние пикселя до ближайшей грани
                   (0 на контуре, растёт наружу). Два слоя exp дают плотное
                   ядро у куба и мягкий разлив за пределами фигуры —
                   как корона сферы-звезды, но повторяющая форму куба. */
                /* ОСКОЛОК 1x1 (grid == 1) — БЕЗ ауры: маленький чистый кубик с
                   прозрачным фоном. Раньше КАЖДЫЙ осколок рисовал свой ореол, и
                   осколки, скопившись на полу, сливались в плотные зелёные
                   лепёшки. Аура — только у большой фигуры (grid >= 2.5). */
                if (grid < 2.5) {
                    discard;
                }
                /* Шаг экспоненты 4.5/9.0: аура ПЛОТНЕЕ облегает рёбра —
                   компактный неоновый контур вплотную к силуэту куба. */
                float auraSoft = exp(-minOv * 4.5);
                float auraCore = exp(-minOv * 9.0);
                float aura = max(auraSoft * 0.70, auraCore);
                /* ПРЯМОУГОЛЬНОЕ гашение у внешней границы поля (НЕ радиальное
                   d=length(v_uv), которое рисовало круг): берём Chebyshev-
                   координату max(abs(uv)), чтобы кромка тоже шла строго
                   параллельно рёбрам куба, без круглого скругления. */
                float dRect = max(abs(v_uv.x), abs(v_uv.y));
                aura *= 1.0 - smoothstep(0.90, 1.0, dRect);
                /* ЖЁСТКАЯ отсечка: за пределами ауры пиксель полностью
                   прозрачен — никаких «невидимых» пикселей, которые на
                   аддитивном блендинге копились бы в зелёные тарелки на полу. */
                if (aura < 0.05) {
                    discard;
                }
                /* МЯГКОЕ ЗАТУХАНИЕ: в ноль уходит и ЦВЕТ (rgb *= aura), и
                   альфа (a = aura). При аддитивном (SRC_ALPHA, ONE) вклад =
                   rgb*boost*a = v_color.rgb*1.5*aura^2 — квадратичный спад
                   даёт узкий, мягкий ободок без широкой плоской площадки.
                   boost константный, чтобы край не разгонялся в пересвет. */
                vec3 acol = v_color.rgb * aura * 1.5;
                rgb = acol;
                a = aura;                   /* прозрачность = затухание ауры */
                boost = 1.0;                /* без прибавки 0.6*aura на краю */
                cutoff = 1.35;
            } else {
            /* сглаживание внешнего края силуэта (AA -- в долях самой грани,
               см. комментарий в цикле: иначе сжатая грань даёт тёмную полосу) */
            float la = max(length(fA) * 0.70, 1e-4);
            float lb = max(length(fB) * 0.70, 1e-4);
            float aau = min(clamp(0.020 / la, 0.004, 0.15), 0.25 * la);
            float aav = min(clamp(0.020 / lb, 0.004, 0.15), 0.25 * lb);
            float covFace = min(smoothstep(-aau, aau, 1.0 - abs(fu)),
                                smoothstep(-aav, aav, 1.0 - abs(fv)));
            /* ===== ШВЫ МЕЖДУ ГРАНЯМИ НЕ ЧЁРНЫЕ =====
               Альфа = ОБЪЕДИНЕНИЕ покрытий всех видимых граней (covAny), а
               не только покрытие выбранной: так стык всегда закрашен цветом
               куба, и тёмный фон не проступает линией. Вне силуэта все
               покрытия = 0 -- пиксель пустой. */
            float cov = max(covFace, covAny);
            if (cov <= 0.004) {
                discard;
            }
            /* ==== ОБЩЕЕ ОСВЕЩЕНИЕ (и фигура, и осколок) ====
               grid (v_aux.w) РАЗДЕЛЯЕТ их БЕЗ uniform: фигуру шлёт set_stars
               с grid=3/4/5, а осколки — emit_cube с grid=1. uniform не подходит:
               фигура и осколки рисуются в ОДНОМ батче (один draw call), а
               uniform — одно значение на весь батч, разделить им частицы нельзя.
               Поэтому режем по самому grid (per-vertex атрибут). */
            vec3 base = v_color.rgb;
            /* ===== ЗОНА 1: ЧЕСТНОЕ ОСВЕЩЕНИЕ ГРАНЕЙ (dot product) =====
               fn — нормаль выбранной грани, уже повёрнутая yaw/pitch
               (cubeProjD; вращение сохраняет длину => |fn| = 1).
               Диффуз = dot(normal, light). Второй термин ndv = dot(normal,
               view = +Z): грань, отвёрнутая от зрителя, темнеет — куб
               перестаёт выглядеть плоской картинкой. */
            vec3 Ld = normalize(vec3(-0.45, 0.75, 0.60));
            float diff = max(dot(fn, Ld), 0.0);
            float ndv = max(dot(fn, vec3(0.0, 0.0, 1.0)), 0.0);
            float shade = (0.55 + 0.45 * diff) * (0.72 + 0.28 * ndv);
            /* ===== AMBIENT OCCLUSION НА СТЫКАХ ТРЁХ БОЛЬШИХ СТОРОН =====
               edgeDist — расстояние в UV до ближайшего края выбранной грани;
               на стыке смежных граней оно => 0. Плавно притемняем до 0.74
               (не в чёрный), чтобы 3D-геометрия читалась, а швы не грязнились. */
            float edgeDist = min(1.0 - abs(fu), 1.0 - abs(fv));
            float ao = mix(0.74, 1.0, smoothstep(0.0, 0.17, edgeDist));
            /* ===== ЗОНА 2: ГЛЯНЦЕВЫЙ БЛИК (Blinn-Phong) =====
               fn зависит от yaw/pitch => при вращении блик переходит с грани
               на грань. Экспонента 18 даёт резкое контрастное пятно, а sheen
               добавляет градиент ВНУТРИ грани — глянец читается как полоса,
               а не плоская заливка. Кладётся ПОВЕРХ цвета фигуры (v_color). */
            vec3 Hv = normalize(Ld + vec3(0.0, 0.0, 1.0));
            float ndh = max(dot(fn, Hv), 0.0);
            /* ЛОКАЛЬНЫЙ глянцевый блик (Blinn-Phong): острое ядро (pow 35) +
               мягкий слой (pow 8). Считается здесь по нормали грани fn, а
               НАКЛАДЫВАЕТСЯ внутри КАЖДОЙ подушечки (по dome) — см. gloss
               ниже. Никакого глобального белого пятна на весь куб. */
            float spec = pow(ndh, 35.0);      /* резкое зеркальное ядро (было 18) */
            float specSoft = pow(ndh, 8.0);   /* мягкий широкий слой под ним */
            float sheen = 0.55 + 0.45 * smoothstep(-1.0, 1.0,
                            (fu * Ld.x + fv * Ld.y));
            spec *= sheen;
            specSoft *= sheen;
            vec3 col;

            /* ===== ЛОКАЛЬНЫЕ КООРДИНАТЫ НАКЛЕЙКИ (от её центра) =====
               grid >= 2.5 -> грань делится на 3x3 наклейки, lp = [-0.5,0.5]
               внутри КАЖДОЙ. grid == 1 (осколок) -> вся грань ОДНА наклейка,
               lp = [-0.5,0.5] по всей грани. Один код даёт и 3x3, и 1x1, и
               осколки при разлёте сохраняют чёрную обводку по краю. */
            vec2 lp;
            if (grid >= 2.5) {
                float cell = 2.0 / 3.0;
                vec2 cellCoord = (vec2(fu, fv) + 1.0) / cell;  /* 0..3 */
                vec2 cellFrac = fract(cellCoord);              /* 0..1 в наклейке */
                lp = cellFrac - 0.5;                            /* центр => 0 */
            } else {
                lp = vec2(fu, fv) * 0.5;                        /* вся грань = 1 наклейка */
            }
            /* SDF скруглённого прямоугольника (пластиковая подушечка):
               половина размера минус зазор шва, со скруглением углов rad.
               sd < 0 внутри наклейки, > 0 — уже в чёрном шве. */
            float gap = 0.075;                 /* полутолщина ЧЁРНОГО шва */
            float rad = 0.15;                  /* скругление углов подушечки */
            vec2 pq = abs(lp) - (vec2(0.5) - gap) + rad;
            float sd = length(max(pq, vec2(0.0))) + min(max(pq.x, pq.y), 0.0) - rad;
            float pillow = smoothstep(0.012, -0.012, sd);   /* 1 = наклейка, 0 = шов */
            /* DOME: к центру наклейки — ярко (1.0), к краю перед швом — темно
               (0.30). Выпуклая пластиковая деталь, а не плоская заливка. */
            float dome = smoothstep(0.0, 0.30, -sd);
            /* база наклейки: честное освещение грани (shade*ao); центр горит
               ЧИСТЫМ v_color.rgb, к краю цвет плавно темнеет перед чёрным швом */
            vec3 stickerCol = base * shade * ao * 1.30;
            stickerCol *= mix(0.30, 1.0, dome);         /* сильное dome-затенение */
            stickerCol += base * 0.25;                   /* сочность, не плоско */
            /* ЛОКАЛЬНЫЙ глянцевый блик — играет ТОЛЬКО внутри подушечки, под-
               чёркивая её выпуклую округлую форму (сильнее на вершине купола) */
            float gloss = (spec * 0.85 + specSoft * 0.25) * (0.20 + 0.80 * dome);
            stickerCol += vec3(1.0) * gloss * 0.60;
            /* ===== ЧЁРНЫЙ ШОВ: матовый, БЕЗ прорыва неона/белого =====
               vec3(0.02): под аддитивным блендингом добавляет ~ноль => тёмный
               фон читается как глубокий чёрный стык. Осколки 1x1 получают такую
               же чёрную обводку => остаются чёткими кубиками при разлёте. */
            vec3 seamCol = vec3(0.02);
            col = mix(seamCol, stickerCol, pillow);

            // Осколок куба 1x1: вырезаем швы и делаем непрозрачным
            if (v_aux.w == 1.0) {
                if (pillow < 0.1) {
                    discard;  // шов осколка — полностью отбрасываем
                }
                cube_fragment = true;  // флаг для финального выхода: монолитная альфа
            }

            rgb = clamp(col, vec3(0.0), vec3(1.0)); /* cov уже в alpha:
               аддитивный блендинг даёт rgb*alpha — premultiply не нужен */
            /* ГЛЯНЦЕВЫЙ БЛИК — как у водяного пузыря: яркое отражение света */
            vec3 specGloss = vec3(1.0) * spec * 0.9;   /* яркий неоновый блик */
            rgb += specGloss;
            rgb = clamp(rgb, vec3(0.0), vec3(1.0));
            a = 1.0;   /* непрозрачный кубик — задние грани не видны сквозь передние */
            boost = 1.35 + spec * 0.20;   /* общая яркость; блик уже локальный */
            cutoff = 1.35;      /* кадр не режем: силуэт задаёт cov */
            }   /* конец else-ветки «внутри куба» (тело фигуры/осколка) */
        } else {
            /* КАРКАС 1×1: контур из 12 рёбер (резерв: spawn с grid < 0.5) */
            float best = 1e9;
            for (int fam = 0; fam < 3; fam++) {
                for (int i = 0; i < 6; i++) {
                    bool bx = (i == 0 || i == 5);    /* координата = ±1 */
                    float u = -1.0 + 0.4 * float(i); /* -1 ... 1 */
                    for (int j = 0; j < 6; j++) {
                        bool by = (j == 0 || j == 5);
                        bool take = bx && by;        /* только 12 рёбер */
                        if (take) {
                            float v = -1.0 + 0.4 * float(j);
                            vec3 a3 = vec3(0.0);
                            vec3 b3 = vec3(0.0);
                            if (fam == 0) {
                                a3 = vec3(-1.0, u, v);
                                b3 = vec3(1.0, u, v);
                            }
                            if (fam == 1) {
                                a3 = vec3(u, -1.0, v);
                                b3 = vec3(u, 1.0, v);
                            }
                            if (fam == 2) {
                                a3 = vec3(u, v, -1.0);
                                b3 = vec3(u, v, 1.0);
                            }
                            vec2 pa = cubeProj(a3, cw, sw, cp, sp) * 0.70;
                            vec2 pb = cubeProj(b3, cw, sw, cp, sp) * 0.70;
                            best = min(best, sdSeg2(q, pa, pb));
                        }
                    }
                }
            }
            float line = 1.0 - smoothstep(0.030, 0.080, best);
            float halo = exp(-best * 9.0) * 0.30;
            if (best > 0.30) {
                discard;
            }
            rgb = clamp(v_color.rgb * (0.80 + line), vec3(0.0), vec3(1.0));
            a = line * 1.15 + halo;
            boost = 1.0 + line * 1.15;
            cutoff = 1.42;                       /* не срезать углы куба */
        }
    } else if (sh < 18.5) {
        /* ВОДЯНОЙ ПУЗЫРЬ: неправильная жидкая форма — контур перетекает
           (три синуса по углу со своей фазой), внутри градиент, мениск-
           кромка, блик и два медленно плывущих пузырька.
           aux.y = радиус в долях высоты, aux.z = полуквад (их отношение =
           базовый радиус в UV ~ 1/1.39), aux.w = фаза волны (своя). */
        float ph = v_aux.w;
        float base = v_aux.y / max(v_aux.z, 1e-5);
        float th = atan(v_uv.y, v_uv.x);
        float rr = base * (1.0
            + 0.115 * sin(th * 3.0 + fx_time * 2.1 + ph)
            + 0.075 * sin(th * 5.0 - fx_time * 1.9 + ph * 2.0)
            + 0.050 * sin(th * 2.0 + fx_time * 3.8 - ph));
        float body = 1.0 - smoothstep(rr - 0.035, rr + 0.01, d);
        if (body <= 0.004) {
            discard;
        }
        /* тело: сверху светлее (вертикальный градиент), снизу — глубина */
        float up = clamp(0.5 - 0.5 * (v_uv.y / max(rr, 0.001)), 0.0, 1.0);
        vec3 deep = v_color.rgb * 0.55;
        vec3 lite = mix(v_color.rgb, vec3(1.0), 0.45);
        vec3 col = mix(lite, deep, up);
        /* мениск-кромка: яркий ореол по краю капли */
        float rim = smoothstep(rr - 0.14, rr, d);
        col = mix(col, mix(v_color.rgb, vec3(1.0), 0.65), rim * 0.85);
        /* блик сверху-слева */
        vec2 hp = v_uv - vec2(-0.30, 0.34) * rr;
        float spec = exp(-dot(hp, hp) * 26.0 / max(rr * rr, 0.01));
        col += vec3(1.0) * spec * 0.50;
        /* два внутренних пузырька медленно плывут по телу */
        vec2 b1 = vec2(sin(fx_time * 1.3 + ph) * 0.35,
                       cos(fx_time * 1.1 + ph) * 0.30) * rr;
        vec2 b2 = vec2(cos(fx_time * 0.9 - ph) * 0.45,
                       sin(fx_time * 1.6 - ph) * 0.35) * rr;
        float bub = (1.0 - smoothstep(0.05, 0.10, length(v_uv - b1)))
                  + (1.0 - smoothstep(0.03, 0.06, length(v_uv - b2))) * 0.7;
        col += vec3(0.85, 0.97, 1.0) * bub * 0.35;
        rgb = clamp(col, vec3(0.0), vec3(1.0));
        a = body * (0.92 + 0.10 * rim);
        boost = 1.0 + spec * 0.5 + bub * 0.15;
        cutoff = 1.0;
    } else if (sh < 20.5) {
        a = 0.0;
    } else if (sh < 24.5) {
        /* SDF-фигуры таргетов: неоновый 3D в 2D (ray marching) */
        float kind = sh - 20.0;
        float grow = clamp(v_aux.y, 0.0, 1.0);
        float prg = clamp(v_aux.z, 0.0, 1.0);
        float se = 1.0 - (1.0 - grow) * (1.0 - grow) * (1.0 - grow);
        float scl = mix(0.16, 0.42, se);          /* появление: малышка -> 1 */
        float ang = fx_time * 0.55 + v_aux.w;     /* вращение фигуры */
        vec3 ro = vec3(0.0, 0.34, -2.35);
        vec3 rd = normalize(vec3(v_uv.xy * 1.30, 1.7));
        float trot = 0.0;
        float md = 1e4;
        bool hasHit = false;
        for (int i = 0; i < 30; i++) {
            vec3 pp = rotY(ro + rd * trot, ang);
            md = mapShape(pp, kind, scl);
            if (md < 0.0035) {
                hasHit = true;
                break;
            }
            trot += md * 0.75;
            if (trot > 4.5) {
                break;
            }
        }
        /* approach-круг osu!: сжимается к моменту бочки */
        float rr = mix(1.05, 0.42, prg);
        float arc = abs(length(v_uv) - rr);
        float ringa = (1.0 - smoothstep(0.0, 0.06, arc))
                      * (0.55 + 0.45 * prg);
        if (!hasHit) {
            a = ringa;
            boost = 1.0 + ringa * 0.8;
            rgb = mix(rgb, vec3(0.65, 0.9, 1.0), 0.35);
            cutoff = 1.0;
        } else {
            vec3 pp = rotY(ro + rd * trot, ang);
            vec3 n = calcNormal(pp, kind, scl);
            vec3 L = normalize(vec3(0.6, 1.0, 0.7));
            float lam = clamp(dot(n, L), 0.0, 1.0);
            vec3 hv = normalize(L + rd);
            float spec = pow(clamp(dot(n, hv), 0.0, 1.0), 26.0);
            float glow = exp(-trot * 2.2);
            rgb = rgb * (0.32 + 0.9 * lam)
                  + vec3(1.0) * (0.85 * spec)
                  + v_color.rgb * (0.35 * glow);
            a = clamp(0.85 + 0.6 * glow, 0.0, 1.2) + ringa * 0.5;
            boost = 1.0 + ringa * 0.5;
        }
    } else {
        /* неизвестная форма — полностью прозрачна */
        a = 0.0;
    }

    float alpha = clamp(a, 0.0, 1.2) * v_color.a;
    /* ЖЁСТКОЕ отсечение фона: за пределами радиуса свечения пиксель
       полностью прозрачен — никаких серых/чёрных подложек от квадрата */
    if (d > cutoff || alpha < 0.004) {
        discard;
    }
    // Осколок куба 1x1: монолитная непрозрачность (перекрывает объекты за собой)
    if (cube_fragment) {
        gl_FragColor = vec4(rgb * boost, 1.0);
    } else {
        gl_FragColor = vec4(rgb * boost, clamp(alpha, 0.0, 1.0));
    }
}
'''

# Формат вершины: pos(2) + uv(2) + rgba(4) + aux(4) + z(1) = 13 float'ов.
FMT = [
    (b'a_pos', 2, 'float'),
    (b'a_uv', 2, 'float'),
    (b'a_color', 4, 'float'),
    (b'a_aux', 4, 'float'),
    (b'a_z', 1, 'float'),  # персональная глубина частицы (з-слой)
]
STRIDE = 13
VERTS_PER_QUAD = 6
_CORNERS = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
_TRI_ORDER = (0, 1, 2, 0, 2, 3)

SHAPE_SPARK = 0.0
SHAPE_RING = 1.0
SHAPE_DROP = 2.0
SHAPE_FLASH = 3.0
SHAPE_BEAM = 8.0
SHAPE_CUBE = 9.0
SHAPE_BALL = 10.0
SHAPE_STAR = 11.0   # космическая сфера-звезда (бочка)
SHAPE_PIXEL = 12.0  # квадратный осколок (куб, pixelated shatter)
SHAPE_HOLE = 13.0   # чёрная дыра (параллелограмм)
SHAPE_GENESIS_STAR = 14.0  # звезда по клику из Genesis (space_effect4.glsl)
STAR_CUSTOM = 6.0          # a1-маркер: цвет звезды из v_color.rgb (прогрессия
                           # мелодии), а не из палитры типа 0..5
SHAPE_HALF = 15.0   # половинка разрезанной звезды (Fruit Ninja)
SHAPE_REMNANT = 16.0  # остывающая целая звезда (мост сфера -> половинки)
SHAPE_CUBE_WIRE = 17.0  # КУБ: цельный куб (фигура) / такой же кубик (осколок)
SHAPE_WATER = 18.0      # ВОДЯНОЙ ПУЗЫРЬ (фигура): жидкий контур, перетекает

# --- фигура-куб и раскол осколков -----------------------------------------
CUBE_QUAD_K = 1.35       # полуквад фигуры-куба: half = r * CUBE_QUAD_K
CUBE_FRAME = 0.70        # доля полуквада, которую занимает сам куб (шейдер)
WATER_QUAD_K = 1.39      # полуквад водяного пузыря: half = r * K — запас под
                         # «плеск» контура (в UV максимум ~0.89 < 1.0), при
                         # этом средний радиус контура = r хитбокса

# --- осколки кубика на полу (половинки сферы пола не имеют: летят вниз) ---
FLOOR_MARGIN = 8.0       # линия пола: px над нижним краем окна
DEBRIS_G = 380.0         # падение осколков (вдвое ленивее прежних 760/900)
# запас на полёт клеток к полу: пол сбрасывает жизнь в REST_LIFE при касании
DEBRIS_FLY_LIFE = (4.0, 4.0)
REST_HOLD = 2.0          # сек: осколок ЛЕЖИТ на полу с полной яркостью
REST_LIFE = 0.55         # сек: тает после лежания
REST_SLIDE_T = 0.40      # сек: скольжение по полу после касания
REST_SLIDE_K = 0.55      # доля боковой скорости, с которой осколок скользит
REST_DAMP = 0.86         # трение о пол: затухание скольжения (кадр @60 FPS)
# Вода: крупная капля лежит на полу КОРОЧЕ клетки куба (0.6 с), затем тает
# тем же REST_LIFE — «растеклась и исчезла», а не лежит лужей 2 секунды
WATER_HOLD = 0.6
# ПУЛ — общая масса воды из разрезанного пузыря: куски падают, растекаются
# и лежат пару секунд (как клетки куба), потом тают за REST_LIFE
WATER_POOL_HOLD = 2.0


def _cube_cells(grid):
    """Центры клеток кубика-Рубика N×N в локальных координатах [-1,1]³.

    Ровно те клетки, из которых шейдер собирает залитую фигуру (у фигуры
    N = 3), поэтому осколки ложатся точно на места клеток большого кубика.
    """
    n = max(2, int(grid))
    c = -1.0 + (2.0 * np.arange(n, dtype=np.float64) + 1.0) / n
    xx, yy, zz = np.meshgrid(c, c, c, indexing='ij')
    return np.stack((xx.ravel(), yy.ravel(), zz.ravel()), axis=1)


def _cube_proj(pts, yaw, pitch):
    """CPU-копия шейдерной cubeProj(): экранные XY точки куба (y — вверх).

    Порядок поворотов обязан совпадать с GLSL (yaw вокруг вертикали, затем
    pitch), иначе осколки родятся не на местах клеток фигуры.
    """
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    x1 = pts[:, 0] * cy + pts[:, 2] * sy
    z1 = -pts[:, 0] * sy + pts[:, 2] * cy
    return np.stack((x1, pts[:, 1] * cp - z1 * sp), axis=1)


HALF_CUT_ANGLE = math.radians(200.0)  # линия среза: почти горизонталь (+20°)
HALF_SLIDE_T = 0.16   # сек разъезда вдоль среза (пока половинки соприкасаются)
HALF_TURN_T = 0.35    # сек доворота «острым концом вниз» после отрыва
HALF_SPIN = (0.6, 1.1)  # рад/с: небольшое вращение половинки во время падения

# Опорный радиус фигуры: зеркалит main.SPHERE_R. Эффекты разреза нормируются
# по нему, поэтому смена SPHERE_R не сжимает и не растягивает разлёт/искры.
FIG_R = 22.5
PERFECT_RING_T = 2.6    # кольцо «PERFECT»: не меньше 2 с уходит за края экрана
# --- «сварка» (бенгальский огонь) на конце следа в режиме INCREDIBLE ---------
WELD_TRIP = 3.0 * 2.0 * FIG_R   # 135 px: искры летят на 3 диаметра сферы
WELD_LIFE = 0.5                 # искры живут 0.5 с и гаснут (было 1.0)
# КОРОТКИЕ искры: длина искры = 2·size·elong. Делим её ровно в 7 раз,
# уменьшая ОБА множителя в √7 раз — искра остаётся вытянутой (не блоб),
# но больше не тянется «удочкой» через пол-экрана.
WELD_SPARK_LEN = 1.0 / 7.0      # во столько раз короче прежней искры
WELD_SPARK_K = math.sqrt(WELD_SPARK_LEN)   # √(1/7): делитель size и elong
WELD_SPARK_SIZE = (3.6, 7.8)    # толщина искры ДО сокращения, px
WELD_SPARK_ELONG = (4.2, 8.4)   # вытянутость ДО сокращения
# Скорость поднята: жизнь вдвое короче (0.5 с), а разлёт держим ~WELD_TRIP
# (три диаметра сферы). Под трением ~0.955/кадр за 0.5 с набирается ~135 px.
WELD_SPEED = (420.0, 560.0)
WELD_G = 150.0            # лёгкая дуга, чтобы суммарно вышло ~WELD_TRIP

# --- SDF-фигуры таргетов (osu! объекты, Raymarching в шейдере) -----------
SDF_CUBE = 20.0
SDF_SPHERE = 21.0
SDF_PYRAMID = 22.0
SDF_PARA = 23.0

# цвета таргетов по форме (неон киберпанк)
TARGET_COLORS = {
    'cube': (1.00, 0.35, 0.45),      # неоново-красный
    'sphere': (0.30, 0.65, 1.00),    # неоново-синий
    'pyramid': (0.95, 0.55, 0.15),   # неоново-оранжевый
    'para': (0.80, 0.40, 1.00),      # неоново-фиолетовый
}

WATER = (0.50, 0.85, 1.00)

class ParticleFx(FloatLayout):
    """Оверлей эффектов. Координаты — абсолютные пиксели (низ-лево)."""

    MAX_PARTICLES = 1500   # квадов частиц (регион 0)
    MAX_RINGS = 24         # кольца — регион 1
    MAX_TARGETS = 16       # osu!-таргеты (SDF-фигуры) — регион 2
    MAX_STARS = 16         # летящие Genesis-звезды — регион 3
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # uniform-данные сферы-звезды (бочка): энергия баса и вспышка ядра
        self._kick_energy = 0.0
        self._flash_intensity = 0.0
        self.canvas = RenderContext(
            vs=VERTEX_SHADER, fs=FRAGMENT_SHADER,
            use_parent_projection=True,
            use_parent_modelview=True,
        )

        self._ring_base = self.MAX_PARTICLES
        self._target_base = self._ring_base + self.MAX_RINGS
        self._star_base = self._target_base + self.MAX_TARGETS
        total = self._star_base + self.MAX_STARS
        nvert = total * VERTS_PER_QUAD
        with self.canvas:
            self._blend_on = Callback(self._additive_on)
            self._mesh = Mesh(
                vertices=np.zeros(nvert * STRIDE, dtype=np.float32),
                indices=np.arange(nvert, dtype=np.uint16),
                fmt=list(FMT),
                mode='triangles',
            )
            self._blend_off = Callback(self._additive_off)

        # Пинг-понг буферы: Kivy может игнорировать присвоение того же
        # numpy-объекта, поэтому каждый кадр отдаём в Mesh другой.
        self._bufs = [
            np.zeros((total, VERTS_PER_QUAD, STRIDE), dtype=np.float32)
            for _ in range(2)
        ]
        self._buf_idx = 0
        self._buf = self._bufs[0]

        n = self.MAX_PARTICLES
        z = np.zeros
        self._pos = z((n, 2), dtype=np.float32)
        self._z = z((n,), dtype=np.float32)        # персональная глубина частицы (з-слой)
        self._vel = z((n, 2), dtype=np.float32)
        self._life = z(n, dtype=np.float32)
        self._max_life = np.ones(n, dtype=np.float32)
        self._size = z(n, dtype=np.float32)
        self._elong = np.ones(n, dtype=np.float32)
        self._ang = z(n, dtype=np.float32)        # поворот квада, рад
        self._spin = z(n, dtype=np.float32)       # угловая скорость, рад/с
        self._follow_vel = z(n, dtype=np.float32) # 1 = повёрнут по скорости
        self._damp = np.ones(n, dtype=np.float32)
        self._grav = z(n, dtype=np.float32)
        self._strength = z(n, dtype=np.float32)
        self._shape = z(n, dtype=np.float32)
        self._weld = z(n, dtype=np.float32)     # 1 = искра «сварки»: РЕЖЕТ сферы
        self._core = z(n, dtype=np.float32)     # 1 = частица-ядро коллапса (секретный эффект)
        # доп. параметры формы (aux.y/z/w): куб — yaw, pitch, grid (1 / 3..5)
        self._aux = z((n, 3), dtype=np.float32)
        self._rgb = z((n, 3), dtype=np.float32)
        self._wobble = z(n, dtype=np.float32)     # амплитуда покачивания
        self._wphase = z(n, dtype=np.float32)
        self._fade_in = z(n, dtype=np.float32)    # сек плавного проявления
        self._ramp = z(n, dtype=np.float32)       # сек разгона разваливания
        # раскол половинок: момент отрыва (конец разъезда) и доворот
        self._slide_t = z(n, dtype=np.float32)    # сек: до этого — разъезд
        self._turn = z(n, dtype=np.float32)       # угловая скорость доворота
        self._turn_dur = z(n, dtype=np.float32)   # длительность доворота, с
        # «ПОЛ» для осколков-кубиков (у остальных частиц _floor = 0):
        self._floor = z(n, dtype=np.float32)      # y линии пола, px (0 = нет)
        self._hold = z(n, dtype=np.float32)       # лежание: 0 = летит,
        #                                           >0 = лежит (сек осталось),
        #                                           -1 = лежание кончилось
        self._rest_hold = np.full(n, REST_HOLD, dtype=np.float32)  # сколько лежать
        self._slide = z(n, dtype=np.float32)      # сек скольжения по полу
        # Чёрная дыра (Genesis): точка-аттрактор для живых частиц
        self._attractor = None

        self._wphase = z(n, dtype=np.float32)     # фаза покачивания
        self._cursor = 0

        self._rings = [None] * self.MAX_RINGS   # активные кольца
        self._ring_cursor = 0
        self._targets = [None] * self.MAX_TARGETS  # osu!-таргеты (SDF)
        self._stars = []            # летящие Genesis-звезды (текущий кадр)
        self._dirty = False   # в mesh остались ненулевые квады

        # --- счёт osu!-геймплея --------------------------------------------
        self._score = 0              # число попаданий свайпом
        self._misses = 0             # число Miss (кольцо сжалось без свайпа)
        self._expired = []           # Miss-таргеты, накопленные за кадр

    # --- blend state -------------------------------------------------------

    def _additive_on(self, instr):
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)

    def _additive_off(self, instr):
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    # --- спавн частиц -------------------------------------------------------

    def _spawn(self, count, x, y, *, speed=(100.0, 400.0), ang0=None,
               spread=2 * math.pi, life=(0.3, 0.8), size=(3.0, 6.0),
               elong=(1.0, 1.0), color=(1.0, 1.0, 1.0), strength=1.0,
               damp=(0.92, 0.96), gravity=0.0, shape=SHAPE_DROP,
               spin=0.0, follow_vel=0, wobble=0.0, fade_in=0.0, ramp=0.0,
               slide_t=0.0, turn=0.0, turn_dur=0.0, aux=None, ang=None,
               floor=0.0, z=0.0, hold=REST_HOLD, weld=False, core=False):
        """Родить count частиц. x/y/ang0/spin/aux/ang/floor принимают и
        массивы длины count (тогда каждая частица рождается со своим
        значением — так рождаются клетки кубика-Рубика), и скаляры.
        hold — сколько секунд частица ЛЕЖИТ на полу после касания
        (REST_HOLD у клеток куба, короче — у водяных капель)."""
        count = int(max(0, min(count, self.MAX_PARTICLES)))
        if count == 0:
            return
        idx = (self._cursor + np.arange(count)) % self.MAX_PARTICLES
        self._cursor = int((self._cursor + count) % self.MAX_PARTICLES)

        if ang0 is None:
            angs = np.random.uniform(0.0, 2.0 * math.pi, count)
        else:
            a0 = np.asarray(ang0, dtype=np.float64)
            angs = np.random.uniform(a0 - spread / 2.0,
                                     a0 + spread / 2.0, count)
        spd = np.random.uniform(speed[0], speed[1], count)
        self._pos[idx, 0] = np.broadcast_to(np.asarray(x, np.float32),
                                            (count,))
        self._pos[idx, 1] = np.broadcast_to(np.asarray(y, np.float32),
                                          (count,))
        self._z[idx] = np.broadcast_to(np.asarray(z, np.float32),
                                       (count,))           # персональная глубина
        self._vel[idx, 0] = np.cos(angs) * spd
        self._vel[idx, 1] = np.sin(angs) * spd
        if ang is None:
            self._ang[idx] = angs
        else:
            self._ang[idx] = np.broadcast_to(np.asarray(ang, np.float32),
                                             (count,))

        lf = np.random.uniform(life[0], life[1], count)
        self._life[idx] = lf
        self._max_life[idx] = np.maximum(lf, 1e-4)
        self._size[idx] = np.random.uniform(size[0], size[1], count)
        self._elong[idx] = np.random.uniform(elong[0], elong[1], count)
        self._strength[idx] = strength
        self._damp[idx] = np.random.uniform(damp[0], damp[1], count)
        self._grav[idx] = gravity
        self._shape[idx] = shape
        self._weld[idx] = 1.0 if weld else 0.0
        self._core[idx] = 1.0 if core else 0.0
        self._spin[idx] = spin
        self._follow_vel[idx] = float(follow_vel)
        self._wobble[idx] = wobble
        self._wphase[idx] = np.random.uniform(0.0, 2.0 * math.pi, count)
        self._fade_in[idx] = float(fade_in)
        self._ramp[idx] = float(ramp)
        self._slide_t[idx] = float(slide_t)
        self._turn[idx] = float(turn)
        self._turn_dur[idx] = float(turn_dur)
        # пол: 0 = обычная частица (падает за экран), >0 = осядет и полежит
        self._floor[idx] = np.broadcast_to(np.asarray(floor, np.float32),
                                           (count,))
        self._rest_hold[idx] = np.broadcast_to(np.asarray(hold, np.float32),
                                               (count,))
        self._hold[idx] = 0.0                     # осколок ещё в полёте
        self._slide[idx] = 0.0                    # и по полу ещё не скользит
        jitter = np.random.uniform(0.80, 1.15, (count, 1))
        self._rgb[idx] = np.asarray(color, dtype=np.float32) * jitter
        if aux is not None:
            self._aux[idx] = np.asarray(aux, dtype=np.float32)

# Размер эффекта = ГРОМКОСТЬ (RMS) × ЧАСТОТА (чем выше баннер — тем крупнее):
# kick (низкие Гц) < snare (средние) < hat (высокие). counts/size пропорциональны
# power; _FREQ_SCALE — глобальный множитель по частоте, меняйте в одном месте.

    _FREQ_SCALE = 1.0

    def set_kick_energy(self, energy):
        """Текущий уровень баса (30-160 Гц) -> uniform u_kick_energy."""
        self._kick_energy = max(0.0, float(energy))

    def set_attractor(self, x, y, strength=1400.0):
        """Чёрная дыра: живые частицы стягиваются к точке (Genesis hold)."""
        self._attractor = (float(x), float(y), float(strength))

    def clear_attractor(self):
        """Отпустить частицы (дыра исчезла)."""
        self._attractor = None


    def trigger_flash(self, strength=1.0):
        """Вспышка ядра в момент тика метронома -> uniform u_flash_intensity."""
        self._flash_intensity = min(2.0, max(
            self._flash_intensity, 0.4 + 1.5 * float(strength)))

    def emit_kick(self, x, y, power=1.0, color=None):
        """Космическая сфера-звезда: объёмное свечение с вспышкой в такт.

        Вместо плоского круга — шейдер SHAPE_STAR (inverse-square glow,
        раскалённое ядро, радиальные протуберанцы). Размер/мощь сферы и
        белая вспышка ядра управляются uniform u_kick_energy и
        u_flash_intensity (питаются из детектора в main.py).
        """
        color = color or (1.00, 0.58, 0.12)
        s = 0.9 * self._FREQ_SCALE
        sz = 30.0 + 46.0 * power * s
        # объёмная сфера-звезда: ядро + glow + протуберанцы
        self._spawn(1, x, y, speed=(0.0, 0.0), life=(0.32, 0.55),
                    size=(sz, sz + 12.0), elong=(1.0, 1.0),
                    color=color, strength=1.7, damp=(0.88, 0.94),
                    gravity=0.0, shape=SHAPE_STAR, follow_vel=1)
        # тонкое кольцо ударной волны для импульса такта
        self._add_ring(x, y, r_max=36.0 + 116.0 * power * s, dur=0.45,
                       th0=8.0, th1=4.0, color=color, alpha=0.8)

    def emit_snare(self, x, y, power=1.0, color=None):
        """Снейр: вспышка-язык + искорки (средние частоты)."""
        color = color or (0.55, 0.92, 1.00)
        s = 1.05 * self._FREQ_SCALE
        if power > 0.0:
            self._spawn(1, x, y, speed=(0.0, 0.0), life=(0.08, 0.15),
                        size=(13.0 * power * s, 20.0 * power * s),
                        color=color, strength=1.4, shape=SHAPE_FLASH)
        self._add_ring(x, y, r_max=24.0 + 95.0 * power * s, dur=0.33,
                       th0=6.0, th1=14.0, color=color, alpha=0.72)
        self._spawn(int(12 * power), x, y,
                    speed=(85.0, 500.0 * power * s),
                    life=(0.10, 0.27), size=(1.5, 3.6), elong=(1.2, 1.9),
                    color=color, strength=1.05, damp=(0.88, 0.94),
                    gravity=0.0, shape=SHAPE_SPARK, follow_vel=1)

    def emit_hat(self, x, y, power=1.0, color=None):
        """Хэт: быстрые лучи + искорки (высокие Гц — крупнее при равной громкости)."""
        color = color or (0.96, 0.98, 1.00)
        s = 1.35 * self._FREQ_SCALE
        base = random.uniform(0.0, 2 * math.pi)
        self._spawn(int(6 * power), x, y, speed=(0.0, 0.0),
                    ang0=base, spread=0.0, life=(0.12, 0.26),
                    size=(2.0, 4.0), elong=(6.0, 15.0 * power * s),
                    color=color, strength=1.15, shape=SHAPE_BEAM)
        self._spawn(int(8 * power), x, y,
                    speed=(130.0, 820.0 * power * s),
                    life=(0.08, 0.22), size=(1.1, 2.8), elong=(1.1, 1.5),
                    color=color, strength=1.05, damp=(0.90, 0.96),
                    gravity=0.0, shape=SHAPE_SPARK, follow_vel=1)

    def emit_slice(self, x, y, color=None, power=1.0, radius=None):
        """Разрез летящей сферы: вспышка + кольцо Genesis + искры.

        Сфера исчезает МОМЕНТАЛЬНО (без остатка-моста): сразу после этой
        вспышки в той же точке рисуется раскол на две половинки, поэтому
        глаз не замечает подмены. power 1.0 = разрез ровно в бит.
        Искры: радиальный разлёт из точки реза цветом сферы + белое
        горячее ядро. Больше сфера / точнее удар — больше искр.
        """
        color = color or (1.0, 0.72, 0.30)
        s = self._FREQ_SCALE
        # мгновенная вспышка в точке реза
        self._spawn(1, x, y, speed=(0.0, 0.0), life=(0.07, 0.11),
                    size=(13.0 + 12.0 * power * s, 20.0 + 14.0 * power * s),
                    color=(1.0, 1.0, 1.0), strength=1.3, shape=SHAPE_FLASH)
        # кольцо: расширяется и пропадает — как в Genesis
        self._add_ring(x, y,
                       r_max=(70.0 + 130.0 * power) * s, dur=0.45,
                       th0=7.0, th1=2.0, color=color, alpha=0.9)
        # ИСКРЫ: радиальный веер из точки реза, слегка падают (гравитация),
        # чтобы разлёт читался «взрывом», а не статичным салютом
        rboost = 1.0 if not radius else min(1.5, max(0.6, radius / FIG_R))
        self._spawn(int(10 + 14 * power * s * rboost), x, y,
                    speed=(160.0, 720.0 * (0.7 + 0.6 * power) * s),
                    life=(0.18, 0.45), size=(1.3, 3.0), elong=(1.4, 2.4),
                    color=color, strength=1.15, damp=(0.90, 0.95),
                    gravity=420.0, shape=SHAPE_SPARK, follow_vel=0)
        # горячее белое ядро: короткие быстрые искры строго из центра
        self._spawn(int(3 + 3 * power), x, y,
                    speed=(60.0, 340.0 * max(0.5, power)), life=(0.10, 0.22),
                    size=(1.0, 2.2), elong=(1.2, 2.0),
                    color=(1.0, 0.97, 0.9), strength=1.25, damp=(0.93, 0.97),
                    gravity=0.0, shape=SHAPE_SPARK, follow_vel=0)

    def slice_halves(self, x, y, radius, color=None, angle=None, power=1.0):
        """Раскол звезды на две половинки — «живой» вариант:

        половинки стартуют мгновенно (fade_in=0) в точке реза и расходятся
        СТРОГО симметрично, как раскрытая книга: направления ровно
        противоположные, скорость у обеих одинаковая, без разброса угла.
        Дальше они медленно крутятся (HALF_SPIN) и просто ПАДАЮТ ВНИЗ за
        нижний край экрана — пола и лежания тут нет: это обломки звезды, а не
        клетки кубика. Яркость НЕ падает: пока половинка на экране, она
        светится как в момент разреза, и исчезает разом уже за кадром (life
        = время падения до низа + запас).
        angle — направление линии среза (по умолчанию HALF_CUT_ANGLE).
        """
        color = color or (1.0, 0.72, 0.30)
        if angle is None:
            angle = HALF_CUT_ANGLE
        k = 1.0 if not radius else radius / FIG_R
        spd = (55.0 + 45.0 * power) * k   # скорость разлёта (от размера фигуры)
        # диск половинки в шейдере доходит до ~0.86 полуквада, поэтому
        # квад = radius*1.16 даёт видимую половинку точно радиуса сферы
        size = (radius * 1.16, radius * 1.16)
        # жизнь = падение до нижнего края (sqrt(2d/g)) + запас на догарание
        _fall = math.sqrt(2.0 * max(24.0, self._view_h() + 24.0 - y) / DEBRIS_G)
        life = (_fall + 0.35, _fall + 0.35)
        # Пара раскрывается СТРОГО симметрично, как книга: направления ровно
        # противоположные (перпендикулярно линии среза — плоские стороны обеих
        # половинок остаются вдоль неё), скорость ОДИНАКОВАЯ, без разброса
        # угла. Пока падают за экран — медленно крутятся (HALF_SPIN).
        spin_mag = random.uniform(*HALF_SPIN)
        damp_mag = random.uniform(0.998, 1.0)      # один на пару: симметрия
        for _i, _sign in enumerate((1.0, -1.0)):
            self._spawn(1, x, y,
                        ang0=angle + math.pi / 2.0 + _i * math.pi,
                        spread=0.0,
                        speed=(spd, spd),
                        life=life, size=size, elong=(1.0, 1.0),
                        color=color, strength=1.15, damp=(damp_mag, damp_mag),
                        gravity=DEBRIS_G, shape=SHAPE_HALF, fade_in=0.0,
                        spin=_sign * spin_mag,
                        follow_vel=0, ramp=0.0)

    def emit_perfect_ring(self, x, y, dur=PERFECT_RING_T, color=None):
        """Волна PERFECT: от взрыва объекта расходится на ВЕСЬ экран.

        Радиус растёт ЛИНЕЙНО (без ease-out) ровно до самого дальнего угла
        экрана: кольцо неспешно доходит до краёв и уходит за них за dur
        секунд (по умолчанию PERFECT_RING_T = 2.6 с — «не слишком быстро»).
        Затухает мягко (fade = 0.5), чтобы было видно, как волна пересекает
        границы экрана.
        """
        w, h = self._view_w(), self._view_h()
        x = min(max(float(x), 0.0), w)
        y = min(max(float(y), 0.0), h)
        r_max = max(math.hypot(x, y), math.hypot(w - x, y),
                    math.hypot(x, h - y), math.hypot(w - x, h - y)) + 40.0
        self._add_ring(x, y, r_max=r_max, dur=dur, th0=10.0, th1=2.0,
                       color=color or (0.35, 1.00, 0.55), alpha=0.85,
                       ease='linear', fade=0.5)

    def emit_cube(self, x, y, size, color=None, power=1.0, grid=3,
                  yaw=0.0, pitch=0.0, floor=None, vel_x=0.0, vel_y=0.0):
        """Разрез кубика-Рубика: вспышка + кольцо + искры + РАЗВАЛ НА КЛЕТКИ.

        Саму фигуру (решётка 3×3) рисует main.py формой SHAPE_CUBE_WIRE,
        а здесь рождается «разваливание»: КАЖДАЯ клетка фигуры становится
        самостоятельным кубиком 1×1 — залитым, как большой куб (grid=1:
        одна клетка на грань) — и стартует РОВНО из своего места в фигуре,
        с её же ориентацией (yaw/pitch). Куб разламывается по клеткам, а
        не «взрывается» из центра.

        Дальше клетки разлетаются, ПАДАЮТ (DEBRIS_G — вдвое ленивее прежнего)
        на «пол» (линия FLOOR_MARGIN над нижним краем), коротко СКОЛЬЗЯТ по
        нему, ЛЕЖАТ REST_HOLD секунд с полной яркостью и тают за REST_LIFE.

        size  — радиус фигуры в пикселях (= половина куба),
        grid  — клеток по ребру (у фигуры 3, у осколка всегда 1),
        yaw/pitch — ориентация фигуры В МОМЕНТ РЕЗА (берётся из main.py),
        floor — y линии пола, px; None — CUBE_FLOOR_MARGIN (низ окна).
        vel_x/vel_y — скорость мыши/лезвия в момент удара (px/с); добавляется
                    к начальной скорости ВСЕХ осколков (инерция разреза), а 3
                    случайным осколкам — мощный дополнительный импульс вперёд.
        """
        color = color or (1.0, 0.72, 0.30)
        s = self._FREQ_SCALE
        # вспышка + кольцо Genesis — как при разрезе сферы
        self._spawn(1, x, y, speed=(0.0, 0.0), life=(0.07, 0.11),
                    size=(13.0 + 12.0 * power * s, 20.0 + 14.0 * power * s),
                    color=(1.0, 1.0, 1.0), strength=1.3, shape=SHAPE_FLASH,
                    z=-1.0)
        self._add_ring(x, y, r_max=(70.0 + 130.0 * power) * s, dur=0.45,
                       th0=7.0, th1=2.0, color=color, alpha=0.9)
        # искры: веер цветом фигуры + белое ядро
        self._spawn(int(8 + 10 * power * s), x, y,
                    speed=(150.0, 620.0 * (0.7 + 0.6 * power) * s),
                    life=(0.16, 0.40), size=(1.3, 2.8), elong=(1.4, 2.2),
                    color=color, strength=1.1, damp=(0.90, 0.95),
                    gravity=420.0, shape=SHAPE_SPARK, follow_vel=0,
                    z=-1.0)
        self._spawn(int(3 + 3 * power), x, y,
                    speed=(60.0, 320.0 * max(0.5, power)), life=(0.10, 0.22),
                    size=(1.0, 2.2), elong=(1.2, 2.0),
                    color=(1.0, 0.97, 0.9), strength=1.25, damp=(0.93, 0.97),
                    gravity=0.0, shape=SHAPE_SPARK, follow_vel=0,
                    z=-1.0)
        # --- РАЗВАЛ: клетки фигуры = кубики 1×1 (залитые, как большой) ---
        # 18 осколков вместо 27: решётка 3×3×2 (по одной оси 2 слоя вместо 3)
        nx, ny, nz = 3, 3, 2
        cx = -1.0 + (2.0 * np.arange(nx, dtype=np.float64) + 1.0) / nx
        cy = -1.0 + (2.0 * np.arange(ny, dtype=np.float64) + 1.0) / ny
        cz = -1.0 + (2.0 * np.arange(nz, dtype=np.float64) + 1.0) / nz
        xx, yy, zz = np.meshgrid(cx, cy, cz, indexing='ij')
        cells = np.stack((xx.ravel(), yy.ravel(), zz.ravel()), axis=1)
        nc = len(cells)                                # 18 клеток
        cell = size * CUBE_QUAD_K * CUBE_FRAME / max(nx, ny, nz)  # половина клетки
        qh = cell / CUBE_FRAME                         # полуквад осколка
        off = _cube_proj(cells, yaw, pitch) * (size * CUBE_QUAD_K * CUBE_FRAME)
        # Осыпание вниз: минимальный разброс, почти вертикальное падение
        mag = np.hypot(off[:, 0], off[:, 1])
        out = np.arctan2(off[:, 1], off[:, 0])
        out = np.where(mag < 1e-6,
                       np.random.uniform(0.0, 2.0 * math.pi, nc), out)
        spd_k = 0.6 + 0.7 * power
        if floor is None:
            floor = FLOOR_MARGIN
        sy = y + off[:, 1]                     # место рождения клетки
        fl = floor + cell + np.random.uniform(-0.20, 0.05, nc) * cell
        fl = np.minimum(fl, sy)                # ...но не выше рождения
        fl[fl < 0.0] = 0.0
        self._spawn(nc, x + off[:, 0], sy,
                    speed=(100.0 * spd_k, 250.0 * spd_k), ang0=out, spread=6.28,
                    life=(3.0, 3.0),           # СТРОГО 3.0 секунды до полного растворения
                    size=(qh * 0.96, qh * 1.04),
                    color=color, strength=1.35, damp=(0.97, 0.995),
                    gravity=DEBRIS_G, shape=SHAPE_CUBE_WIRE, follow_vel=0,  # кубик с обводкой граней
                    spin=np.random.uniform(-2.6, 2.6, nc),
                    ang=np.random.uniform(-0.10, 0.10, nc),
                    floor=fl,
                    hold=0.0,                  # тает сразу при касании пола
                    z=-1.0,                    # задний план: за любыми целыми кубами
                    aux=np.stack((yaw + np.random.uniform(-0.35, 0.35, nc),
                                  pitch + np.random.uniform(-0.30, 0.30, nc),
                                  np.ones(nc)), axis=1))   # grid=1 — одна клетка на грань
        # --- ПЛАЗМЕННЫЕ ИСКРЫ: радиальный всплеск из точки разреза ---
        # Время жизни точно как у брызг водяного пузыря (STRУИ: life 0.55–1.05 с).
        self._spawn(int(10 + 8 * power * s), x, y,
                    speed=(100.0, 500.0 * (0.8 + 0.5 * power) * s),
                    life=(0.55, 1.05), size=(1.0, 2.0), elong=(1.5, 2.5),
                    color=color, strength=1.15, damp=(0.92, 0.96),
                    gravity=0.0, shape=SHAPE_SPARK, follow_vel=0,
                    z=-1.0)
        # --- СЕКРЕТНЫЙ ЭФФЕКТ: коллапс ядра (шанс 10%) — ОТКЛЮЧЕН для отладки краша ---
        # if np.random.random() < 0.10:
        #     center = nc // 2
        #     cx = x + off[center, 0]
        #     cy = sy[center]
        #     self._spawn(1, cx, cy,
        #                 speed=(0.0, 0.0), ang0=0.0, spread=0.0,
        #                 life=(2.0, 2.0),
        #                 size=(qh, qh),
        #                 color=color, strength=1.0, damp=(1.0, 1.0),
        #                 gravity=0.0, shape=SHAPE_CUBE_WIRE, follow_vel=0,
        #                 spin=15.0, ang=0.0,
        #                 floor=0.0, z=-1.0, hold=0.0,
        #                 aux=np.stack((yaw, pitch, np.ones(1)), axis=1),
        #                 core=True)

    def emit_water(self, x, y, color=None, power=1.0, radius=None):
        """Разрез ВОДЯНОГО ПУЗЫРЯ: каждый раз СЛУЧАЙНЫЙ всплеск воды.

        Четыре яруса (все параметры тянутся из random при каждом резе,
        поэтому два одинаковых по силе разреза выглядят по-разному):
          * СТРУИ — 3..6 вееров капель в случайных направлениях;
          * МУТЬ — мелкие капельки, гасят прямо в воздухе, но успевают
            улететь далеко (длинная жизнь + слабое трение);
          * КРУПНЫЕ КАПЛИ — падают на пол (_floor), коротко скользят
            (растекаются) и тают — механика пола та же, что у клеток куба,
            но лежат они WATER_HOLD секунд, а не REST_HOLD.
          * ПУЛ — общая масса воды (2..3 широких куска): падает под
            тяжестью, растекается по полу и лежит WATER_POOL_HOLD секунд.
        power 1.0 = разрез ровно в бит; radius — радиус пузыря (для
        нормировки разлёта, как у emit_slice).
        """
        color = color or WATER
        s = self._FREQ_SCALE
        r = FIG_R if radius is None else float(radius)
        k = min(1.5, max(0.6, r / FIG_R))     # крупнее пузырь — пышнее всплеск
        # мгновенная вспышка + кольцо ударной волны (как у звезды, но водой)
        self._spawn(1, x, y, speed=(0.0, 0.0), life=(0.07, 0.11),
                    size=(13.0 + 12.0 * power * s, 20.0 + 14.0 * power * s),
                    color=(1.0, 1.0, 1.0), strength=1.3, shape=SHAPE_FLASH)
        self._add_ring(x, y, r_max=(70.0 + 130.0 * power) * s, dur=0.45,
                       th0=7.0, th1=2.0, color=color, alpha=0.9)
        # --- СТРУИ: у каждого реза своя карта направлений -------------------
        for _ in range(random.randint(3, 6)):
            a0 = random.uniform(0.0, 2.0 * math.pi)
            sp = random.uniform(200.0, 460.0) * k * (0.75 + 0.45 * power)
            self._spawn(random.randint(3, 7), x, y,
                        speed=(sp, sp * random.uniform(1.2, 1.9)),
                        ang0=a0, spread=random.uniform(0.35, 1.0),
                        life=(0.55, 1.05), size=(2.5, 6.0),
                        elong=(1.4, 2.8), color=color, strength=1.15,
                        damp=(0.95, 0.975),
                        gravity=random.uniform(240.0, 480.0),
                        shape=SHAPE_DROP, follow_vel=1)
        # --- МЕЛКАЯ МУТЬ: дальний полёт, тает в воздухе далеко от центра ---
        self._spawn(random.randint(8, 16), x, y,
                    speed=(110.0, 470.0), life=(0.45, 0.95),
                    size=(1.5, 3.5), elong=(1.0, 1.7), color=color,
                    strength=1.05, damp=(0.945, 0.97), gravity=150.0,
                    shape=SHAPE_DROP, follow_vel=1)
        # --- КРУПНЫЕ КАПЛИ: падают на пол, растекаются, тают ----------------
        nb = random.randint(4, 7)
        fl = np.minimum(FLOOR_MARGIN + np.random.uniform(-2.0, 6.0, nb),
                        float(y))
        fl[fl < 0.0] = 0.0             # родилась у самого пола — без пола
        self._spawn(nb, x, y, speed=(70.0, 300.0),
                    life=DEBRIS_FLY_LIFE, size=(5.0, 10.0),
                    elong=(1.0, 1.6), color=color, strength=1.2,
                    damp=(0.97, 0.995), gravity=DEBRIS_G,
                    shape=SHAPE_DROP, follow_vel=1,
                    floor=fl, hold=WATER_HOLD)
        # --- ПУЛ: общая масса воды широкими кусками ------------------------
        # Летит вниз с небольшим разбросом (ang0 = -π/2 — вниз, spread ±0.9),
        # форма горизонтальная (ang=0, follow_vel=0 — не разворачивается по
        # скорости), на полу скользит и лежит WATER_POOL_HOLD секунд.
        npl = random.randint(2, 3)
        flp = np.minimum(FLOOR_MARGIN + np.random.uniform(-2.0, 6.0, npl),
                         float(y))
        flp[flp < 0.0] = 0.0
        self._spawn(npl, x, y, speed=(30.0, 130.0),
                    ang0=-0.5 * math.pi, spread=0.9,
                    life=DEBRIS_FLY_LIFE,
                    size=(9.0, 15.0), elong=(2.6, 4.2),
                    color=color, strength=1.25,
                    damp=(0.985, 0.995), gravity=DEBRIS_G * 1.4,
                    shape=SHAPE_DROP, ang=0.0, follow_vel=0,
                    floor=flp, hold=WATER_POOL_HOLD)

    def emit_onset(self, x, y, power=1.0, hue=0.5):
        """Универсальный эффект на ЛЮБОЙ звук: кольцо + вспышка + искры.

        hue — спектральный центроид из детектора: бас тёплый, верха
        холодные. Короткая жизнь (0.15-0.40 с) — визуал «щёлкает» в такт
        без смазывания кадра. При 4-8 звуках/с даёт 4-8 событий.
        """
        color = _hue_rgb(hue)
        p = 0.35 + 0.65 * min(1.0, max(0.0, power))
        self._add_ring(x, y, r_max=34.0 + 62.0 * p, dur=0.30,
                       th0=6.0, th1=2.0, color=color, alpha=0.85)
        self._spawn(1, x, y, speed=(0.0, 0.0), life=(0.07, 0.11),
                    size=(10.0 + 14.0 * p, 16.0 + 10.0 * p),
                    color=(1.0, 1.0, 1.0), strength=1.2, shape=SHAPE_FLASH)
        self._spawn(int(8 + 16 * p), x, y,
                    speed=(120.0, 620.0 * p),
                    life=(0.15, 0.40), size=(1.4, 3.2), elong=(1.2, 1.7),
                    color=color, strength=1.1, damp=(0.90, 0.95),
                    gravity=0.0, shape=SHAPE_SPARK, follow_vel=1)

    def _add_ring(self, x, y, r_max=150.0, dur=0.65, th0=6.0, th1=18.0,
                  color=WATER, alpha=0.95, ease='out', fade=1.5):
        """Кольцо ударной волны: r 0 -> r_max за dur с, толщина th0 -> th1.

        ease — закон роста радиуса: 'out' (рывок наружу, по умолчанию) или
        'linear' (равномерно — для волны на весь экран); fade — степень
        затухания альфы (меньше = дольше светится).
        """
        j = self._ring_cursor
        self._ring_cursor = (self._ring_cursor + 1) % self.MAX_RINGS
        self._rings[j] = {
            'cx': float(x), 'cy': float(y),
            'age': 0.0, 'dur': float(dur),
            'r_max': float(r_max), 'th0': float(th0), 'th1': float(th1),
            'rgb': tuple(color), 'alpha': float(alpha),
            'ease': ease, 'fade': float(fade),
        }

    # --- геометрия экрана ------------------------------------------------------

    def _view_w(self):
        return max(self.width, 1.0)

    def _view_h(self):
        return max(self.height, 1.0)

    def add_trail(self, x1, y1, x2, y2, width=9.0, life=(0.10, 0.16),
                  tip=None):
        """Метеорный след курсора — как разрез в Genesis (space_effect3):
        белое неоновое ядро + циановый слой + изумрудно-циановая аура.
        Рисуется по сегменту движения мыши, слои тают за life.

        tip=(r,g,b) — «сварка» на КОНЦЕ следа: крупное (×3) горячее ядро и веер
        жёлтых искр в стороны; искры пролетают ~WELD_TRIP (три диаметра
        сферы) и гаснут через WELD_LIFE (бенгальский огонь INCREDIBLE).
        """
        seg_len = math.hypot(x2 - x1, y2 - y1)
        if seg_len < 3.0:
            return
        ang = math.atan2(y2 - y1, x2 - x1)
        mx = (x1 + x2) * 0.5
        my = (y1 + y2) * 0.5
        elong = (max(seg_len / width, 1.2),) * 2
        # 1) широкая изумрудно-циановая аура (космическое свечение)
        self._spawn(1, mx, my, speed=(0.0, 0.0), ang0=ang, spread=0.0,
                    life=life, size=(width * 2.6, 4.0),
                    elong=elong, color=(0.04, 0.62, 0.48), strength=0.9,
                    shape=SHAPE_BEAM, follow_vel=0)
        # 2) циановый средний слой
        self._spawn(1, mx, my, speed=(0.0, 0.0), ang0=ang, spread=0.0,
                    life=life, size=(width * 1.4, 3.0),
                    elong=elong, color=(0.30, 0.85, 0.80), strength=1.1,
                    shape=SHAPE_BEAM, follow_vel=0)
        # 3) белое неоновое ядро
        self._spawn(1, mx, my, speed=(0.0, 0.0), ang0=ang, spread=0.0,
                    life=life, size=(width * 0.6, 2.0),
                    elong=elong, color=(1.0, 1.0, 1.0), strength=1.5,
                    shape=SHAPE_BEAM, follow_vel=0)
        if tip is not None:
            # --- «СВАРКА» на конце следа (INCREDIBLE), эффект ×3 --------------
            # Горячее ядро и веер искр втрое крупнее прежнего; скорость и
            # жизнь подобраны так, чтобы искры пролетели ~WELD_TRIP (три
            # диаметра сферы) и погасли через WELD_LIFE секунду.
            self._spawn(1, x2, y2, speed=(0.0, 0.0),
                        life=(0.06, 0.13), size=(width * 2.55, width * 2.55),
                        color=tip, strength=1.45, shape=SHAPE_DROP, follow_vel=0)
            # искры В 7 РАЗ КОРОЧЕ (было до 130 px): оба множителя длины
            # (size и elong) делим в √7 — форма по-прежнему вытянутая, но
            # короткая. weld=True: эти искры РЕЖУТ сферы при касании
            # (мост к _spark_cuts через fx.weld_points()).
            self._spawn(3, x2, y2, speed=WELD_SPEED,
                        ang0=ang + 0.5 * math.pi, spread=2.0 * math.pi,
                        life=(WELD_LIFE, WELD_LIFE),
                        size=(WELD_SPARK_SIZE[0] * WELD_SPARK_K,
                              WELD_SPARK_SIZE[1] * WELD_SPARK_K),
                        elong=(WELD_SPARK_ELONG[0] * WELD_SPARK_K,
                               WELD_SPARK_ELONG[1] * WELD_SPARK_K),
                        color=tip, strength=1.35, damp=(0.945, 0.965),
                        gravity=WELD_G, shape=SHAPE_SPARK, follow_vel=1,
                        weld=True)

    def weld_points(self):
        """Живые искры «сварки»: [(x, y, r_касания)] — ими РЕЖУТСЯ сферы.

        r — половина ДЛИНЫ искры (2·size·elong / 2 = size·elong): искра
        дотягивается до сферы, если её черта дошла до диска. Искры короткие
        (в 7 раз короче прежних), поэтому и режут вплотную к следу лезвия.
        Позиции берутся после update(dt) — то есть ровно те, что на экране.
        """
        idx = np.nonzero((self._life > 0.0) & (self._weld > 0.5))[0]
        return [(float(self._pos[i, 0]), float(self._pos[i, 1]),
                 float(self._size[i] * self._elong[i])) for i in idx]

    # --- osu!-таргеты (SDF-фигуры, approach-круг) -------------------------

    def spawn_target(self, x, y, kind='cube', deadline=1.0, radius=38.0,
                     audio_time=0.0, color=None):
        """Создать таргет в свободном слоте. Возвращает idx (или None)."""
        for i in range(self.MAX_TARGETS):
            if self._targets[i] is None:
                break
        else:
            return None
        if kind not in TARGET_COLORS:
            kind = 'cube'
        color = color or TARGET_COLORS[kind]
        self._targets[i] = {
            'x': float(x), 'y': float(y), 'kind': kind,
            'sdf': {'cube': SDF_CUBE, 'sphere': SDF_SPHERE,
                    'pyramid': SDF_PYRAMID, 'para': SDF_PARA}[kind],
            'radius': float(radius), 'born': float(audio_time),
            'deadline': float(deadline),
            'grow': 0.0, 'prog': 0.0, 'active': True,
            'color': tuple(color), 'ang': random.uniform(0.0, 6.2832),
        }
        return i

    def update_targets(self, audio_time):
        """Анимация таргетов по аудио-часам; истёкшие (prog>=1) — Miss."""
        for i, t in enumerate(self._targets):
            if t is None or not t['active']:
                continue
            span = max(t['deadline'] - t['born'], 1e-3)
            prog = min(1.0, max(0.0, (audio_time - t['born']) / span))
            t['grow'] = min(1.0, max(0.0, (audio_time - t['born']) / 0.12))
            t['prog'] = prog
            if prog >= 1.0:
                # кольцо сжалось, а свайпа не было — исчезает с «Miss»
                self.deactivate_target(i, count_miss=True)

    def deactivate_target(self, idx, count_miss=False):
        """Убрать таргет БЕЗ взрыва (Miss). Возвращает снимок или None."""
        if idx is None or idx >= self.MAX_TARGETS:
            return None
        t = self._targets[idx]
        if t is None or not t['active']:
            return None
        t['active'] = False
        if count_miss:
            self._misses += 1
            self._expired.append(dict(t))
            # вялый серый «пшик» вместо взрыва — промах не награждаем
            self._add_ring(t['x'], t['y'], r_max=t['radius'] * 1.5, dur=0.32,
                           th0=4.0, th1=2.0, color=(0.55, 0.58, 0.62),
                           alpha=0.4)
        return dict(t)

    def pop_expired(self):
        """Miss-таргеты, накопленные с прошлого кадра (снимки dict)."""
        out = self._expired
        self._expired = []
        return out

    def hit_target(self, idx):
        """Свайп попал в таргет: деактивируем и взрываем по форме."""
        if idx is None or idx >= self.MAX_TARGETS:
            return None
        t = self._targets[idx]
        if t is None or not t['active']:
            return None
        t['active'] = False
        self._score += 1
        self._burst_target(t)
        return dict(t)

    def hit_targets_along(self, x1, y1, x2, y2, blade=30.0):
        """Все активные таргеты, задетые отрезком свайпа (Mouse Collision).

        Коллизия: расстояние от центра таргета до отрезка свайпа
        <= radius + blade. Возвращает список снимков попаданий
        ({'x', 'y', 'kind', 'prog', 'radius', 'idx', ...}), по 'prog'
        main.py выставляет оценку 300/100/50 (фаза approach-кольца).
        """
        hits = []
        dx = x2 - x1
        dy = y2 - y1
        seg2 = dx * dx + dy * dy
        for i, t in enumerate(self._targets):
            if t is None or not t['active']:
                continue
            cx = t['x']
            cy = t['y']
            if seg2 < 1e-6:
                px, py = x1, y1
            else:
                tt = min(1.0, max(0.0,
                                  ((cx - x1) * dx + (cy - y1) * dy) / seg2))
                px = x1 + tt * dx
                py = y1 + tt * dy
            reach = t['radius'] + blade
            if (cx - px) * (cx - px) + (cy - py) * (cy - py) <= reach * reach:
                snap = self.hit_target(i)
                if snap is not None:
                    snap['idx'] = i
                    hits.append(snap)
        return hits

    def _burst_target(self, t):
        """Уникальный взрыв в зависимости от формы таргета."""
        x, y = t['x'], t['y']
        color = t['color']
        p = 1.0
        kind = t['kind']
        if kind == 'cube':
            # Pixelated shatter: квадратные пиксели-осколки
            self._add_ring(x, y, r_max=46.0 + 44.0 * p, dur=0.4,
                           th0=7.0, th1=3.0, color=color, alpha=0.85)
            self._spawn(int(18 + 14 * p), x, y,
                        speed=(90.0, 520.0 * p),
                        life=(0.3, 0.75), size=(3.0, 6.0), elong=(0.85, 1.2),
                        color=color, strength=1.3, damp=(0.9, 0.94),
                        gravity=220.0, shape=SHAPE_PIXEL,
                        spin=random.uniform(-8.0, 8.0), follow_vel=0)
        elif kind == 'sphere':
            # Круговая ударная волна: двойное кольцо + вспышка
            self._add_ring(x, y, r_max=52.0 + 120.0 * p, dur=0.42,
                           th0=9.0, th1=4.0, color=color, alpha=1.0)
            self._add_ring(x, y, r_max=34.0 + 200.0 * p, dur=0.7,
                           th0=3.0, th1=18.0, color=color, alpha=0.5)
            self._spawn(1, x, y, speed=(0.0, 0.0), life=(0.10, 0.16),
                        size=(18.0 + 16.0 * p, 30.0 * p),
                        color=(1.0, 1.0, 1.0), strength=1.45,
                        shape=SHAPE_FLASH)
        elif kind == 'pyramid':
            # Три острых лазерных луча под 120 градусов
            base = random.uniform(0.0, 2 * math.pi)
            for j in range(3):
                ang0 = base + j * 2.0944
                self._spawn(1, x, y, speed=(0.0, 0.0), ang0=ang0,
                            spread=0.0, life=(0.16, 0.28),
                            size=(2.0, 4.0),
                            elong=(14.0, 22.0 * p),
                            color=color, strength=1.35, shape=SHAPE_BEAM)
            self._spawn(int(6 * p), x, y, speed=(80.0, 420.0 * p),
                        life=(0.15, 0.4), size=(1.5, 3.2), elong=(1.2, 1.8),
                        color=color, strength=1.15, damp=(0.9, 0.95),
                        shape=SHAPE_SPARK, follow_vel=1)
        else:  # 'para' — сингулярность + неоновые искры
            self._spawn(1, x, y, speed=(0.0, 0.0), life=(0.22, 0.3),
                        size=(20.0 + 14.0 * p, 34.0 * p),
                        color=color, strength=1.3, shape=SHAPE_HOLE)
            self._spawn(int(14 + 12 * p), x, y,
                        speed=(70.0, 460.0 * p),
                        life=(0.2, 0.5), size=(1.4, 3.0), elong=(1.2, 1.9),
                        color=color, strength=1.2, damp=(0.92, 0.96),
                        gravity=60.0, shape=SHAPE_SPARK, follow_vel=1)

    def get_score(self):
        return self._score

    def get_misses(self):
        return self._misses

    # --- движок ----------------------------------------------------------------

    def _fill_particles(self, qbuf, idx, dt):
        # физика (только по живым слотам idx)
        self._vel[idx] *= np.power(self._damp[idx], dt * 60.0)[:, np.newaxis]
        self._vel[idx, 1] -= self._grav[idx] * dt
        # покачивание: модуляция бокового смещения по возрасту
        age = self._max_life[idx] - self._life[idx]
        wob_vx = self._wobble[idx] * np.cos(self._wphase[idx] + age * 6.0)
        # разваливание (Fruit Ninja): первые _ramp секунд частица едва
        # расходится (5-6 кадров при 60 FPS), затем плавно выходит на
        # полную скорость — глаз видит «разлом», а не мгновенный прыжок
        rp = self._ramp[idx]
        rt = np.clip(age / np.maximum(rp, 1e-6), 0.0, 1.0)
        vf = np.where(rp > 1e-6,
                      0.15 + 0.85 * rt * rt * (3.0 - 2.0 * rt), 1.0)
        # раскол половинок: до ОТРЫВА (slide_t) части разъезжаются вдоль среза,
        # после отрыва боковой ход прекращается (разъезжаться больше незачем),
        # и они постепенно разворачиваются острым концом вниз (доворот)
        st = self._slide_t[idx]
        half = st > 1e-6
        if np.any(half):
            td = self._turn_dur[idx]
            self._vel[idx[half & (age > st)], 0] = 0.0        # хватит разъезда
            turning = half & (age > st) & (age <= st + td)
            self._spin[idx[turning]] = self._turn[idx[turning]]
            self._spin[idx[half & (age > st + td)]] = 0.0     # доворот завершён
        self._pos[idx, 0] += (self._vel[idx, 0] + wob_vx) * vf * dt
        self._pos[idx, 1] += (self._vel[idx, 1] * vf) * dt
        self._ang[idx] += self._spin[idx] * dt

        # «ПОЛ» осколков-кубиков (_floor > 0): коснулся — оседает, коротко
        # СКОЛЬЗИТ по полу и лежит до таяния (половинки сферы пола не имеют).
        onto = self._floor[idx] > 0.0
        if np.any(onto):
            land = onto & (self._pos[idx, 1] <= self._floor[idx])
            if np.any(land):
                li = idx[land]
                self._pos[li, 1] = self._floor[li]
                self._vel[li, 1] = 0.0                # вертикаль — в ноль
                self._grav[li] = 0.0
                self._wobble[li] = 0.0
                self._spin[li] = 0.0                  # лёг — не крутится
                fresh = land & (self._hold[idx] == 0.0)
                if np.any(fresh):
                    fi = idx[fresh]                   # первое касание пола:
                    self._hold[fi] = -1.0             # тает сразу, без задержки на лежание
                    self._slide[fi] = 0.0             # нет скольжения, сразу в лужу
                    self._vel[fi, 0] = 0.0            # убираем боковой ход
                    # _life и _max_life НЕ сбрасываем: timer 3.0 с идёт непрерывно
                stop = land & (self._slide[idx] <= 0.0)
                if np.any(stop):
                    self._vel[idx[stop], 0] = 0.0     # домял — больше не едет

        # угол квада: по скорости либо фиксированный
        fv = self._follow_vel[idx] > 0.5
        ang_fv = np.arctan2(self._vel[idx, 1], self._vel[idx, 0])
        ang = np.where(fv, ang_fv, self._ang[idx])
        c = np.cos(ang)
        s = np.sin(ang)

        t = np.clip(1.0 - self._life[idx] / self._max_life[idx], 0.0, 1.0)
        # осколки-кубики В ПОЛЁТЕ к полу не тускнеют: их гасит только «лежание»
        # (таймер жизни сбрасывается в момент касания, см. блок «ПОЛ»)
        if np.any(onto):
            t = np.where(onto & (self._hold[idx] == 0.0), 0.0, t)
        # Половинки сферы НЕ ТУСКНЕЮТ вовсе: пока живы — полная яркость,
        # исчезают разом уже улетев за нижний край (life из slice_halves —
        # время падения до низа). Остальные формы гаснут по своему life.
        half_f = self._shape[idx] == SHAPE_HALF
        if np.any(half_f):
            t = np.where(half_f, 0.0, t)
        inten = np.clip(self._strength[idx] * np.power(1.0 - t, 1.4),
                        0.0, 1.2)
        # плавное проявление (мост при разрезе): такие частицы начинаются
        # с нулевой яркости и выходят на полную за _fade_in секунд
        fi = self._fade_in[idx]
        age = self._max_life[idx] - self._life[idx]
        f = np.where(fi > 1e-6,
                     np.clip(age / np.maximum(fi, 1e-6), 0.0, 1.0), 1.0)
        inten *= f * f * (3.0 - 2.0 * f)
        hw = self._size[idx] * self._elong[idx]
        hh = self._size[idx]
        # --- ЯДРО КОЛЛАПСА: яркость и размер растут к концу жизни — ОТКЛЮЧЕНО ---
        # try:
        #     core_mask = self._core[idx] > 0.5
        #     if np.any(core_mask):
        #         ci = idx[core_mask]
        #         ct = t[core_mask]
        #         inten[core_mask] = np.clip(
        #             self._strength[ci] * np.exp(ct * 2.5), 0.0, 1.2)
        #         cs = self._size[ci] * (1.0 + ct * 2.0)
        #         hh[core_mask] = cs
        #         hw[core_mask] = cs * self._elong[ci]
        # except Exception as e:
        #     print(f"[Core Melt Crash]: {e}")
        # --- ПЛАВЛЕНИЕ НА ПОЛУ: безопасный скалярный расчет (STRIDE=12) ---
        try:
            melting_mask = self._hold[idx] < 0.0
            if np.any(melting_mask):
                melt_positions = np.where(melting_mask)[0]
                for pos in melt_positions:
                    pos = int(pos)
                    mi = int(idx[pos])
                    # Безопасный скалярный расчет прогресса таяния от 0.0 до 1.0
                    l_val = float(self._life[mi])
                    m_val = max(float(self._max_life[mi]), 1e-5)
                    t_melt = np.clip(1.0 - (l_val / m_val), 0.0, 1.0)
                    # Модификация размеров и альфы
                    hh[pos] = float(hh[pos]) * (1.0 - t_melt)
                    hw[pos] = float(hw[pos]) * (1.0 + t_melt)
                    inten[pos] = float(self._strength[mi]) * (1.0 - t_melt)
        except Exception as e:
            print(f'[Melt crash] particle melting block: {e}')
        px = self._pos[idx, 0]
        py = self._pos[idx, 1]
        rgb = self._rgb[idx]
        shape = self._shape[idx]
        aux = self._aux[idx]

        for k in range(VERTS_PER_QUAD):
            ux, uy = _CORNERS[_TRI_ORDER[k]]
            qbuf[idx, k, 0] = px + c * (hw * ux) - s * (hh * uy)
            qbuf[idx, k, 1] = py + s * (hw * ux) + c * (hh * uy)
            qbuf[idx, k, 2] = ux
            qbuf[idx, k, 3] = uy
            qbuf[idx, k, 4:7] = rgb
            qbuf[idx, k, 7] = inten
            qbuf[idx, k, 8] = shape
            qbuf[idx, k, 9:12] = aux
            qbuf[idx, k, 12] = self._z[idx]   # персональная глубина (z-слой)

    def _fill_rings(self, qbuf, dt):
        base = self._ring_base
        for j, ring in enumerate(self._rings):
            if ring is None:
                continue
            ring['age'] += dt
            if ring['age'] >= ring['dur']:
                self._rings[j] = None
                continue
            p = ring['age'] / ring['dur']
            ease = (p if ring.get('ease') == 'linear'
                    else 1.0 - (1.0 - p) * (1.0 - p))   # ease-out по умолчанию
            rad = ring['r_max'] * ease
            th = ring['th0'] + (ring['th1'] - ring['th0']) * p
            alpha = ring['alpha'] * (1.0 - p) ** ring.get('fade', 1.5)
            half = rad + th + 6.0
            rn = max(rad / half, 0.001)   # радиус в UV квада
            thn = th / half
            slot = base + j
            r0, g0, b0 = ring['rgb']
            for k in range(VERTS_PER_QUAD):
                ux, uy = _CORNERS[_TRI_ORDER[k]]
                qbuf[slot, k, 0] = ring['cx'] + half * ux
                qbuf[slot, k, 1] = ring['cy'] + half * uy
                qbuf[slot, k, 2] = ux
                qbuf[slot, k, 3] = uy
                qbuf[slot, k, 4] = r0
                qbuf[slot, k, 5] = g0
                qbuf[slot, k, 6] = b0
                qbuf[slot, k, 7] = alpha
                qbuf[slot, k, 8] = SHAPE_RING
                qbuf[slot, k, 9] = rn
                qbuf[slot, k, 10] = thn
                qbuf[slot, k, 12] = 0.0   # з-слой = 0 (передняя плоскость)

    def _fill_targets(self, qbuf):
        base = self._target_base
        for i, t in enumerate(self._targets):
            if t is None or not t['active']:
                continue
            cx, cy = t['x'], t['y']
            r = t['radius']
            half = r * 2.35 + 8.0
            shape = t['sdf']
            grow = t['grow']
            prg = t['prog']
            ang = t['ang']
            r0, g0, b0 = t['color']
            slot = base + i
            for k in range(VERTS_PER_QUAD):
                ux, uy = _CORNERS[_TRI_ORDER[k]]
                qbuf[slot, k, 0] = cx + half * ux
                qbuf[slot, k, 1] = cy + half * uy
                qbuf[slot, k, 2] = ux
                qbuf[slot, k, 3] = uy
                qbuf[slot, k, 4] = r0
                qbuf[slot, k, 5] = g0
                qbuf[slot, k, 6] = b0
                qbuf[slot, k, 7] = 1.0
                qbuf[slot, k, 8] = shape
                qbuf[slot, k, 9] = grow
                qbuf[slot, k, 10] = prg
                qbuf[slot, k, 11] = ang
                qbuf[slot, k, 12] = 0.0   # таргеты на передней плоскости

    def set_stars(self, stars):
        """Летящие фигуры текущего кадра (регион 3).

        Элемент: (x, y, r_px, a1, fade, shape, a2, rgb[, grid]):
          * shape = SHAPE_GENESIS_STAR → a1 = тип палитры Genesis, rgb = None
            (цвет берёт шейдер из палитры типа);
          * shape = SHAPE_CUBE_WIRE   → a1 = yaw, a2 = pitch, rgb = цвет куба,
            grid = размер клеток (3/4/5; без поля — 5);
          * shape = SHAPE_WATER       → a1 = фаза волны пузыря (своя у каждой),
            rgb = цвет воды (полуквад = r × WATER_QUAD_K).
        Короткая форма из 5 полей трактуется как звезда.
        Вызывать каждый кадр; список заменяется целиком.
        """
        norm = []
        for st in list(stars)[-self.MAX_STARS:]:
            if len(st) == 5:
                norm.append((st[0], st[1], st[2], st[3], st[4],
                             SHAPE_GENESIS_STAR, 0.0, None))
            elif len(st) == 7:
                norm.append(tuple(st) + (None,))
            else:
                norm.append(tuple(st))
        self._stars = norm

    def _fill_stars(self, qbuf):
        """Залить квады Genesis-звезд и кубов в регион 3."""
        base = self._star_base
        vh = max(self._view_h(), 1.0)
        for i, st in enumerate(self._stars[:self.MAX_STARS]):
            x, y, r_px, a1, fade, shape, a2, user_rgb = st[:8]
            # куб несёт 9-е поле grid (3/4/5); без него — прежние 5×5
            grid = float(st[8]) if len(st) > 8 else 5.0
            if shape == SHAPE_CUBE_WIRE:
                # куб: цельный куб занимает CUBE_FRAME полуквада -> half даёт
                # ровно r; решётки больше нет, grid -- только признак формы
                half = r_px * CUBE_QUAD_K
            elif shape == SHAPE_WATER:
                # пузырь: контур «плещет» до ~0.89 полуквада в UV,
                # средний радиус контура совпадает с r хитбокса
                half = r_px * WATER_QUAD_K
            else:
                # квад должен покрыть корону: maxGlow = r + 1.667*r в норм. ед.
                half = r_px * 2.8 + 2.0
            slot = base + i
            for k in range(VERTS_PER_QUAD):
                ux, uy = _CORNERS[_TRI_ORDER[k]]
                qbuf[slot, k, 0] = x + half * ux
                qbuf[slot, k, 1] = y + half * uy
                qbuf[slot, k, 2] = ux
                qbuf[slot, k, 3] = uy
                if user_rgb is None:
                    qbuf[slot, k, 4] = 1.0
                    qbuf[slot, k, 5] = 1.0
                    qbuf[slot, k, 6] = 1.0
                else:
                    qbuf[slot, k, 4] = user_rgb[0]
                    qbuf[slot, k, 5] = user_rgb[1]
                    qbuf[slot, k, 6] = user_rgb[2]
                qbuf[slot, k, 7] = max(0.0, min(1.0, fade))
                qbuf[slot, k, 8] = shape
                if shape == SHAPE_CUBE_WIRE:
                    qbuf[slot, k, 9] = a1          # yaw
                    qbuf[slot, k, 10] = a2         # pitch
                    qbuf[slot, k, 11] = grid       # размер: 3 / 4 / 5
                else:
                    qbuf[slot, k, 9] = r_px / vh
                    qbuf[slot, k, 10] = half / vh
                    qbuf[slot, k, 11] = a1         # тип палитры
                qbuf[slot, k, 12] = 0.0   # новая фигура — передний план (z=0)

    def update(self, dt, audio_time=None):
        """Вызывать каждый кадр. Физика на CPU, заливка квадов в GPU.

        audio_time — время в аудио-часах (сек) для анимации approach-колец
        таргетов (основной игровой синхрон с бочкой). None — таймеры стоят.
        """
        dt = min(float(dt), 1.0 / 30.0)

        # время и uniform-данные сферы бочки; вспышка затухает по экспоненте
        self._flash_intensity *= math.exp(-dt * 4.0)
        if self._flash_intensity < 0.01:
            self._flash_intensity = 0.0
        try:
            self.canvas['fx_time'] = Clock.get_boottime()
            self.canvas['u_kick_energy'] = self._kick_energy
            self.canvas['u_flash_intensity'] = self._flash_intensity
        except Exception:
            pass

        self._life -= dt
        # ЛЕЖАНИЕ осколков-кубиков: пока _hold > 0, таймер жизни заморожен —
        # клетка лежит на полу с постоянной яркостью. Лежание кончилось
        # (_hold = -1) — жизнь снова тикает, клетка тает и пропадает.
        held = self._hold > 0.0
        if np.any(held):
            self._hold[held] -= dt
            self._life[held] += dt
            over = held & (self._hold <= 0.0)
            self._hold[over] = -1.0
        alive = self._life > 0
        # --- ЯДРО КОЛЛАПСА: финальная вспышка — ОТКЛЮЧЕНО для отладки ---
        # core_idx = np.nonzero(self._core > 0.5)[0]
        # if len(core_idx):
        #     core_died = core_idx[self._life[core_idx] <= 0.0]
        #     if len(core_died):
        #         self._life[core_died] = 0.05
        #         self._max_life[core_died] = 0.05
        #         self._strength[core_died] = 2.0
        #         self._spin[core_died] = 0.0
        #         self._grav[core_died] = 0.0
        #         self._core[core_died] = 0.0
        # СКОЛЬЖЕНИЕ осколков по полу: короткий таймер, пока держит REST_DAMP,
        # затем боковая скорость гасится совсем (см. блок «ПОЛ»)
        sl = self._slide > 0.0
        if np.any(sl):
            self._slide[sl] -= dt

        # --- Чёрная дыра: притяжение живых частиц к точке-аттрактору ---
        if self._attractor is not None:
            ax, ay, strength = self._attractor
            idx_alive = np.nonzero(alive)[0]
            if len(idx_alive):
                dx = ax - self._pos[idx_alive, 0]
                dy = ay - self._pos[idx_alive, 1]
                inv = strength * dt / np.sqrt(dx * dx + dy * dy + 900.0)
                self._vel[idx_alive, 0] += dx * inv
                self._vel[idx_alive, 1] += dy * inv

        # --- osu!-таргеты: анимация по аудио-часам ---
        if audio_time is not None:
            self.update_targets(audio_time)

        qbuf = self._buf
        qbuf[:] = 0.0   # мёртвые слоты остаются нулями (шейдер их отсекает)

        if np.any(alive):
            try:
                self._fill_particles(qbuf, np.nonzero(alive)[0], dt)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[FILL_PARTICLES CRASH] alive={np.sum(alive)} dt={dt}")
        self._fill_rings(qbuf, dt)
        self._fill_targets(qbuf)
        self._fill_stars(qbuf)
        self._flush()
        self._dirty = False

    def _flush(self):
        """Залить текущий буфер в GPU и переключиться на второй (ping-pong)."""
        self._mesh.vertices = self._buf.reshape(-1)
        self._buf_idx = 1 - self._buf_idx
        self._buf = self._bufs[self._buf_idx]

    def clear(self):
        self._life.fill(0.0)
        self._slide_t.fill(0.0)
        self._turn.fill(0.0)
        self._turn_dur.fill(0.0)
        self._floor.fill(0.0)      # пол снимается вместе с осколками
        self._hold.fill(0.0)
        self._slide.fill(0.0)
        self._weld.fill(0.0)
        self._rings = [None] * self.MAX_RINGS
        self._targets = [None] * self.MAX_TARGETS
        self._stars = []
        self._score = 0
        self._misses = 0
        self._expired = []
        for buf in self._bufs:
            buf[:] = 0.0
        self._buf_opaque[:] = 0.0
        self._flush()
        self._dirty = False

