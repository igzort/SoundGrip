# -*- coding: utf-8 -*-
"""menu.py — стартовое меню SoundGrip.

Пункты (главное меню): Выбор мелодии / Эффекты / Опции / Выход.
Режимы: main / tracks / effects / options / sound.
Подменю «Опции» — пункт «Звук» (экран настроек) + «Назад» в главное;
задел на будущие разделы настроек.
Управление: мышь (наведение + клик), ↑/↓ — выбор, Enter/Пробел — ок,
Esc — назад (из подменю в главное, из главного — выход).
Меню не играет музыку: только вызывает колбэки приложения
(on_play / on_exit / on_volume / on_effects).
Меню НЕ хранит ссылку на GameWidget — никакого спавна игровых объектов.
"""
from kivy.core.window import Window
from kivy.core.text import Label as CoreLabel
from kivy.graphics import Color, Rectangle, Line, Ellipse
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.widget import Widget
from kivy.uix.slider import Slider
from kivy.uix.switch import Switch
from kivy.uix.label import Label
from kivy.app import App

import math
import random
import tracks as tracklib

KEY_ENTER = (13, 271)
KEY_ESC = 27
KEY_UP, KEY_DOWN = 273, 274
KEY_LEFT, KEY_RIGHT = 276, 275
KEY_MINUS = (45, 269)
KEY_PLUS = (61, 270, 107)

COL_NEON_WHITE = (1.0, 1.0, 1.0, 1.0)
COL_NEON_CYAN = (0.0, 0.95, 1.0, 1.0)
COL_NEON_SUBTLE = (0.3, 0.7, 0.8, 0.9)
COL_SELECTED = (0.0, 1.0, 1.0, 1.0)  # выбранный трек: чистый циан [0, 1, 1, 1]

VOL_STEP = 0.1

FX_KINDS = ('star', 'cube', 'water')
FX_LABELS = {
    'star': 'Звезда-сфера',
    'cube': 'Кубик-Рубик',
    'water': 'Водяной пузырь',
}



class MenuItem(Widget):
    """Один пункт меню: текст + неоновый чекбокс справа."""

    def __init__(self, text, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        self.text = text
        self._selected = False   # выбран клавиатурой — ярко-белый с неоном
        self._hovered = False    # курсор мыши — тот же яркий неон, но сбрасывается
        self._fx_kind = None
        self._fx_active = False
        self._texture = None
        self._build()

    # ------------------------------------------------------------------ #
    def _build(self):
        cl = CoreLabel(text=self.text, font_size=20, bold=True)
        cl.refresh()
        self._texture = cl.texture
        self.size = cl.texture.size
        self._redraw()  # мгновенная отрисовка после создания текстуры

    def set_fx(self, kind, active):
        """Привязать чекбокс к пункту (для подменю Эффекты)."""
        self._fx_kind = kind
        self._fx_active = active
        self._redraw()

    def set_pos(self, x, y):
        self.pos = (x, y)
        self._redraw()

    def set_selected(self, selected):
        """Клавиатурный выбор (только один пункт selected=True)."""
        if self._selected != selected:
            self._selected = selected
            self._redraw()

    def set_hovered(self, hovered):
        """Визуальный ховер — НЕ меняет клавиатурный _sel."""
        if self._hovered != hovered:
            self._hovered = hovered
            self._redraw()

    # ------------------------------------------------------------------ #
    def _redraw(self):
        """Перерисовать пункт в соответствии с текущим состоянием."""
        self.canvas.clear()
        with self.canvas:
            # --- текст ---
            if self._selected or self._hovered:
                glow = COL_SELECTED if self._selected else COL_NEON_CYAN
                Color(*glow)
                for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2),
                               (-1, -1), (-1, 1), (1, -1), (1, 1)):
                    Rectangle(texture=self._texture,
                              pos=(self.pos[0] + dx, self.pos[1] + dy),
                              size=self.size)
                fill = COL_SELECTED if self._selected else COL_NEON_WHITE
                Color(*fill)
                Rectangle(texture=self._texture, pos=self.pos, size=self.size)
            else:
                Color(*COL_NEON_SUBTLE)
                Rectangle(texture=self._texture, pos=self.pos, size=self.size)

            # --- неоновый чекбокс-квадрат справа ---
            if self._fx_kind is not None:
                bs = 22.0
                bx = self.pos[0] + self.size[0] + 14.0
                by = self.pos[1] + (self.size[1] - bs) * 0.5

                if self._fx_active:
                    Color(*COL_NEON_CYAN)
                    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2),
                                   (-1, -1), (-1, 1), (1, -1), (1, 1)):
                        Rectangle(pos=(bx + dx, by + dy), size=(bs, bs))
                    Color(*COL_NEON_WHITE)
                    Rectangle(pos=(bx, by), size=(bs, bs))
                else:
                    Color(*COL_NEON_SUBTLE)
                    Line(points=[bx, by, bx + bs, by], width=1.5)
                    Line(points=[bx + bs, by, bx + bs, by + bs], width=1.5)
                    Line(points=[bx + bs, by + bs, bx, by + bs], width=1.5)
                    Line(points=[bx, by + bs, bx, by], width=1.5)


class MenuWidget(FloatLayout):
    """Виджет меню: пункты, ховер, клавиатурная навигация.
    Режимы: main / tracks / effects / options / sound.

    on_play(track)  — старт выбранного трека (track = Track-объект)
    on_exit()       — выход из приложения
    on_volume(v)    — установить громкость 0..1
    on_effects(kind)— включить демо-режим (kind='both' или конкретный FX_KINDS)
    """

    def __init__(self, on_play=None, on_exit=None, on_volume=None,
                 on_effects=None, on_exit_effects=None, **kw):
        super().__init__(**kw)
        self.on_play = on_play or (lambda _t: None)
        self.on_exit = on_exit or (lambda: None)
        self.on_volume = on_volume or (lambda _v: None)
        self.on_effects = on_effects or (lambda _k: None)
        self.on_exit_effects = on_exit_effects or (lambda: None)
        # Меню НЕ хранит ссылку на GameWidget — никакого спавна объектов!

        self.active = True
        self.mode = 'main'
        self.tracks = tracklib.list_tracks()
        self.track_idx = 0
        self.volume = 0.8
        self.status = ''
        self._rows = []
        self._items = []
        self._fx_active = {'star': True, 'cube': False, 'water': False}
        self._sel = 0
        self._last_mouse = None

        Window.bind(on_key_down=self._on_key)
        self.rebuild()

        # --- виртуальные часы для неоновой пульсации заголовка ---
        self._fx_clock = 0.0
        # --- крупный заголовок игры "SOUNDGRIP" (верх экрана) ---
        self._title_cl = CoreLabel(text='SOUNDGRIP', font_size=54, bold=True)
        self._title_cl.refresh()
        self._title_tex = self._title_cl.texture
        self._title_size = self._title_tex.size
        self._title_pos = (Window.width * 0.5 - self._title_size[0] * 0.5,
                           Window.height * 0.88)
        with self.canvas:
            # Сохраняем ссылку на инструкцию цвета, чтобы менять альфу на лету
            # и не стирать весь canvas (кнопки, чекбоксы и прочий интерфейс)
            self.title_color = Color(1.0, 1.0, 1.0, 1.0)
            Rectangle(texture=self._title_tex,
                      pos=self._title_pos, size=self._title_size)

        # --- фоновые звёзды главного меню (рисуются на self.canvas.before, за кнопками) ---
        self.menu_stars = []
        for _ in range(150):
            self.menu_stars.append({
                'x': random.uniform(0, Window.width),
                'y': random.uniform(0, Window.height),
                'size': random.uniform(3.5, 6.0),
                'speed': random.uniform(2.0, 8.0),
                'color': random.choice([
                    (0.4, 0.9, 1.0, 0.6),   # мягкий бирюзовый
                    (0.6, 1.0, 0.6, 0.6),   # неон-лайн
                    (0.9, 0.9, 1.0, 0.6),   # бледный белый
                ]),
            })

    # ------------------------------------------------------------------ #
    # Жизненный цикл -- показ / скрытие / переходы режимов
    # ------------------------------------------------------------------ #
    def show(self, track=None):
        """Вернуться в главное меню (вызывается из main.py show_menu)."""
        self.active = True
        self.mode = 'main'
        self._sel = 0
        self._last_mouse = None
        self.status = self._track_status()
        self.refresh(track, self.volume)

    def hide(self):
        self.active = False

    def refresh(self, track=None, volume=None):
        if volume is not None:
            self.volume = max(0.0, min(1.0, float(volume)))
        if track is not None:
            try:
                self.track_idx = self.tracks.index(track)
            except ValueError:
                pass
        self.rebuild()

    def set_status(self, text):
        self.status = text

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, float(v)))
        try:
            self.on_volume(self.volume)
        except Exception:
            pass


    # ------------------------------------------------------------------ #
    # Построение пунктов меню
    # ------------------------------------------------------------------ #
    def _track_status(self):
        if not self.tracks:
            return 'положи mp3 в папку игры'
        t = self.tracks[self.track_idx]
        return ('выбран: %s   %s'
                % (t.title,
                   'карта готова' if t.has_map else 'карту соберу при старте'))

    def rebuild(self):
        """Перестроить меню для текущего self.mode и self._rows."""
        self.clear_widgets()
        self._items = []

        # --- определяем строки для текущего режима ---
        if self.mode == 'effects':
            self._rows = []
            for kind in FX_KINDS:
                self._rows.append(
                    (FX_LABELS[kind],
                     lambda k=kind: self._fx_toggle(k),
                     kind))
            self._rows.append(('Назад', self.back, None))
            self.status = 'неоновый квадрат = эффект включён в игре'
        elif self.mode == 'sound':
            self._rows = [
                ('Назад', self.back, None),
            ]
            self.status = 'Громкость: %d%%' % round(self.volume * 100)
            self._build_sound_controls()
        elif self.mode == 'options':
            self._rows = [
                ('Звук', self._open_sound, None),
                ('Назад', self.back, None),
            ]
            self.status = 'Опции'
        elif self.mode == 'tracks':
            self._rows = []
            for i, t in enumerate(self.tracks):
                # строгая нумерация без декоративных знаков > / + / …
                txt = '%d.%s' % (i + 1, str(t.title)[:44])
                # FIX #2: передаём реальный объект Track в on_play!
                self._rows.append(
                    (txt, lambda tr=t: self.on_play(tr), None))
            if not self.tracks:
                self._rows.append(('треки не найдены', None, None))
            self._rows.append(('Назад', self.back, None))
            self.status = self._track_status()
        else:  # main
            self._rows = [
                ('Выбор мелодии', self._open_tracks, None),
                ('Эффекты',       self._open_effects, None),
                ('Опции',         self._open_options, None),
                ('Выход',         self.on_exit, None),
            ]
            self.status = self._track_status()

        # --- создаём пункты ---
        start_y = Window.height * 0.5 + (len(self._rows) * 24)
        for i, row in enumerate(self._rows):
            text = row[0]
            action = row[1]
            fx_kind = row[2] if len(row) > 2 else None
            mi = MenuItem(text=text)
            if fx_kind is not None:
                mi.set_fx(fx_kind, self._fx_active.get(fx_kind, False))
            tw, th = mi._texture.size
            mi.size = (tw, th)
            x = Window.width * 0.5 - tw * 0.5
            if self.mode == 'sound':
                # кнопку «Назад» опускаем вниз, под слайдер и тумблер вибрации
                y = Window.height * 0.18
            else:
                y = start_y - i * 56
            mi.pos = (x, y)
            mi._redraw()
            self._items.append(mi)
            self.add_widget(mi)

        self._sel = min(self._sel, max(0, len(self._items) - 1))
        self._last_mouse = None  # сбросить — update() перевычислит hover
        self.select(self._sel)

    # ------------------------------------------------------------------ #
    # Подменю: переходы
    # ------------------------------------------------------------------ #
    def _open_tracks(self):
        """FIX #2: переключить на экран выбора мелодии (треки)."""
        self.mode = 'tracks'
        self.status = self._track_status()
        self._sel = self.track_idx if self.tracks else 0
        self.rebuild()

    def _open_effects(self):
        """Войти в подменю Эффекты — запросить демо-поле через колбэк."""
        self.mode = 'effects'
        self.status = 'неоновый квадрат = эффект включён в игре'
        self._sel = 0
        self.rebuild()
        # Запросить у main.py показ демо-поля с текущим активным эффектом
        # (через колбэк, не через прямой доступ к self._game).
        active_kind = next((k for k in FX_KINDS if self._fx_active.get(k, False)),
                           FX_KINDS[0])
        self.on_effects(active_kind)

    def show_effects(self, kind='both'):
        """Вызывается из main.py start_effects после показа демо-поля."""
        self.active = True
        self.mode = 'effects'
        self.status = 'неоновый квадрат = эффект включён в игре'
        self._sel = 0
        self.rebuild()

    def _open_sound(self):
        self.mode = 'sound'
        self.status = 'Громкость: %d%%' % round(self.volume * 100)
        self._sel = 0
        self.rebuild()

    def _open_options(self):
        """Войти в подменю «Опции»: пункт «Звук» + кнопка «Назад»."""
        self.mode = 'options'
        self.status = 'Опции'
        self._sel = 0
        self.rebuild()

    def back(self):
        if self.mode == 'main':
            self.on_exit()
            return
        # FIX: если ушли из подменю Эффекты — очищаем демо-поле в main.py!
        # Иначе зелёный куб остаётся висеть на заднем фоне.
        if self.mode == 'effects':
            try:
                self.on_exit_effects()
            except Exception:
                pass
        self.mode = 'main'
        self.status = self._track_status()
        self._sel = 0
        self._last_mouse = None
        self.rebuild()

    def bump_volume(self, delta):
        self.volume = max(0.0, min(1.0, self.volume + delta))
        # слайдер связан через bind — в режиме 'sound' не перестраиваем
        # всё меню, иначе слайдер будет мигать при каждом нажатии
        if self.mode != 'sound':
            self.rebuild()
        self.status = 'Громкость: %d%%' % round(self.volume * 100)
        if getattr(self, 'volume_slider', None) is not None:
            self.volume_slider.value = self.volume
        try:
            self.on_volume(self.volume)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Экран «Звук»: слайдер громкости + тумблер вибрации (Android UX)
    # ------------------------------------------------------------------ #
    def _build_sound_controls(self):
        """Построить слайдер громкости и тумблер вибрации на экране 'sound'."""
        # Подстраховка от дублей, если метод вызван вне rebuild()
        for attr in ('volume_slider', 'vib_switch', 'vib_lbl'):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    self.remove_widget(w)
                except Exception:
                    pass
                setattr(self, attr, None)

        # --- горизонтальный слайдер громкости 0..1 ---
        self.volume_slider = Slider(
            min=0, max=1, value=self.volume,
            size_hint=(0.85, None), height=44,
            pos_hint={'center_x': 0.5, 'center_y': 0.70},
        )
        self.volume_slider.bind(value=self._on_volume_slider)
        self.add_widget(self.volume_slider)

        # --- текущее значение громкости ---
        self.vol_lbl = Label(
            text='Громкость: %d%%' % round(self.volume * 100),
            font_size=18,
            size_hint=(None, None), size=(220, 28),
            pos_hint={'center_x': 0.5, 'center_y': 0.63},
        )
        self.add_widget(self.vol_lbl)

        # --- тумблер вибрации ---
        app = App.get_running_app()
        vib_enabled = bool(getattr(app, 'vibration_enabled', True))
        self.vib_lbl = Label(
            text='Вибрация: %s' % ('ON' if vib_enabled else 'OFF'),
            font_size=18,
            size_hint=(None, None), size=(220, 28),
            pos_hint={'center_x': 0.5, 'center_y': 0.50},
        )
        self.add_widget(self.vib_lbl)

        self.vib_switch = Switch(
            active=vib_enabled,
            size_hint=(None, None), size=(66, 34),
            pos_hint={'center_x': 0.5, 'center_y': 0.42},
        )
        self.vib_switch.bind(active=self._on_vib_switch)
        self.add_widget(self.vib_switch)

    def _on_volume_slider(self, instance, value):
        """Слайдер громкости: колбэк приложения (звук управляется через Kivy Sound)."""
        vol = max(0.0, min(1.0, float(value)))
        self.volume = vol
        try:
            self.on_volume(vol)
        except Exception:
            pass
        if getattr(self, 'vol_lbl', None) is not None:
            self.vol_lbl.text = 'Громкость: %d%%' % round(vol * 100)
        self.status = 'Громкость: %d%%' % round(vol * 100)

    def _on_vib_switch(self, instance, active):
        """Тумблер вибрации: флаг сохраняется в приложении."""
        app = App.get_running_app()
        app.vibration_enabled = bool(active)
        if getattr(self, 'vib_lbl', None) is not None:
            self.vib_lbl.text = 'Вибрация: %s' % ('ON' if active else 'OFF')
    # ------------------------------------------------------------------ #
    # FIX #1: toggle эффекта — ТОЛЬКО флаг + колбэк, НИКАКОГО self._game!
    # ------------------------------------------------------------------ #
    def _fx_toggle(self, kind):
        # Радио-кнопки: только один эффект активен одновременно.
        # Всегда остаётся ровно один True — нельзя выключить все эффекты.
        for k in FX_KINDS:
            self._fx_active[k] = (k == kind)
        self.rebuild()
        self.on_effects(kind)

    def set_game(self, game):
        """No-op: меню НЕ хранит ссылку на игру — не спавнит объекты."""
        pass


    # ------------------------------------------------------------------ #
    # Выбор пункта (клавиатура + мышь)
    # ------------------------------------------------------------------ #
    def select(self, idx):
        """Клавиатурный выбор — подсвечивает ТОЛЬКО один пункт."""
        n = len(self._items)
        if n == 0:
            return
        self._sel = idx % n
        # сбрасываем ховер при смене выбора клавиатурой
        for i, mi in enumerate(self._items):
            mi.set_selected(i == self._sel)
            mi.set_hovered(False)
        # принудительно перевычислить hover в update() на след. кадр,
        # иначе строка _sel может «залипнуть» из-за оптимизации _last_mouse
        self._last_mouse = None

    def move(self, d):
        """Переместить выбор на d шагов (↑/↓)."""
        self.select(self._sel + d)

    def activate(self):
        """Нажать Enter/Space/Клик на текущем пункте."""
        if not self._items:
            return
        action = self._rows[self._sel][1]
        if action:
            action()

    # ------------------------------------------------------------------ #
    # Клавиатурный ввод
    # ------------------------------------------------------------------ #
    def _on_key(self, _win, key, *_a):
        if not self.active:
            return False
        try:
            if key == KEY_UP:
                self.move(-1)
                return True
            if key == KEY_DOWN:
                self.move(+1)
                return True
            if key == KEY_ESC:
                self.back()
                return True
            if key in KEY_ENTER or key == 32:  # Enter или Space
                self.activate()
                return True
            if self.mode == 'effects' and key in (KEY_LEFT, KEY_RIGHT):
                if 0 <= self._sel < len(FX_KINDS):
                    kind = FX_KINDS[self._sel]
                    self._fx_toggle(kind)
                return True
            if self.mode == 'sound':
                if key == KEY_LEFT or key in KEY_MINUS:
                    self.bump_volume(-VOL_STEP)
                    return True
                if key == KEY_RIGHT or key in KEY_PLUS:
                    self.bump_volume(+VOL_STEP)
                    return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------ #
    # Мышь
    # ------------------------------------------------------------------ #
    def on_touch_down(self, touch):
        if not self.active:
            return False
        # стандартные виджеты Kivy (Slider, Switch) получают касание первыми
        if super().on_touch_down(touch):
            return True
        for i, mi in enumerate(self._items):
            if mi.collide_point(touch.x, touch.y):
                self.select(i)
                self.activate()
                return True
        return True

    # ------------------------------------------------------------------ #
    # FIX #3: ховер — пересчитываем каждый кадр
    # ------------------------------------------------------------------ #
    def update(self, _dt):
        """Перерисовка ховера каждый кадр (bind в main.py).

        Правило приоритета:
        1. Если курсор над пунктом — ТОЛЬКО он светится (ярко-белый + неон).
           Пункт выбранный клавиатурой при этом НЕ светится (гаснет в тусклый).
        2. Если курсор ни над одним пунктом — светится ТОЛЬКО клавиатурный
           выбор (self._sel). Все остальные — тусклый COL_NEON_SUBTLE.
        """
        if not self.active:
            return

        # --- движение и отрисовка фоновых звёзд (на self.canvas.before, за кнопками) ---
        for star in self.menu_stars:
            star['y'] += star['speed'] * _dt
            if star['y'] > Window.height:
                star['y'] = 0
                star['x'] = random.uniform(0, Window.width)

        self.canvas.before.clear()
        with self.canvas.before:
            for star in self.menu_stars:
                Color(*star['color'])
                Ellipse(pos=(star['x'], star['y']), size=(star['size'], star['size']))

        # --- пульсация заголовка "SOUNDGRIP" (работает всегда, даже без мыши) ---
        self._fx_clock += _dt
        self.title_color.a = 0.85 + 0.15 * math.sin(self._fx_clock * 2.0)  # 0.7 .. 1.0

        try:
            mx, my = Window.mouse_pos
        except Exception:
            return

        # оптимизация: пропускаем кадр если мышь не двинулась
        if self._last_mouse == (mx, my):
            return
        self._last_mouse = (mx, my)

        hovered_idx = -1
        for i, mi in enumerate(self._items):
            if mi.collide_point(mx, my):
                hovered_idx = i
                break

        if hovered_idx >= 0:
            # --- курсор над пунктом ---
            for i, mi in enumerate(self._items):
                mi.set_hovered(i == hovered_idx)
                if self.mode == 'tracks':
                    # выбранный трек всегда остаётся подсвеченным,
                    # даже если курсор навели на другую строку
                    mi.set_selected(i == self._sel)
                else:
                    mi.set_selected(False)
        else:
            # --- курсор ни над чем: только клавиатурный выбор ---
            for i, mi in enumerate(self._items):
                mi.set_selected(i == self._sel)
                mi.set_hovered(False)
