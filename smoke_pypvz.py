#!/usr/bin/env python
"""pypvz 无头冒烟测试。

改完玩法后跑这个，验证「选卡 -> 开局 -> 种植 -> 开火 -> 刷僵尸」整条链路没被改坏。
不需要人眼、不需要窗口，用的是 SDL 的 dummy 驱动 + 手动泵事件循环。

用法（在 D:\\workbuddy\\pvz 目录下，环境是 conda 的 pvz）：
    conda run -n pvz python smoke_pypvz.py                 # 默认测第 3 关（白天 1-3）
    conda run -n pvz python smoke_pypvz.py 14              # 测第 14 关（示例自定义关卡）
    conda run -n pvz python smoke_pypvz.py 3 --seconds 40  # 只跑 40 秒真实时间

如果不用 conda run，也可以先 activate：
    conda activate pvz && python smoke_pypvz.py 14
（直接调用 envs\\pvz\\python.exe 会因缺少 Library/bin 而报 pygame DLL 加载失败，必须走 activate 或 conda run）

退出码 0 表示全通过，非 0 表示有断言失败（失败原因会打印出来）。
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import pygame as pg

pg.init()

sys.path.insert(0, os.path.join(HERE, 'pypvz'))

import source.constants as c

# 存档默认写到 %APPDATA%\pypvz\，这里重定向到工作区，避免污染真实存档
c.USERDATA_PATH = os.path.join(HERE, '_smoke_userdata.json')

from source import tool
from source.component import map as game_map
from source.state import level, mainmenu, screen


def build_ctrl(level_num: int, game_rate: int = 4):
    ctrl = tool.Control()
    states = {
        c.MAIN_MENU: mainmenu.Menu(),
        c.GAME_VICTORY: screen.GameVictoryScreen(),
        c.GAME_LOSE: screen.GameLoseScreen(),
        c.LEVEL: level.Level(),
        c.AWARD_SCREEN: screen.AwardScreen(),
        c.HELP_SCREEN: screen.HelpScreen(),
    }
    persist = c.INIT_USERDATA.copy()
    persist.update(
        {
            c.GAME_MODE: c.MODE_ADVENTURE,
            c.LEVEL_NUM: level_num,
            c.GAME_RATE: game_rate,
            c.CURRENT_TIME: 0,
        }
    )
    ctrl.game_info = persist
    ctrl.fps = 50 * game_rate
    ctrl.setup_states(states, c.LEVEL)
    return ctrl


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('level', nargs='?', type=int, default=3, help='关卡序号，从 0 开始')
    ap.add_argument('--seconds', type=float, default=25.0, help='正式跑的秒数（真实时间）')
    args = ap.parse_args()

    lv_num = args.level
    if not 0 <= lv_num < game_map.TOTAL_LEVEL:
        print(f'关卡序号越界：{lv_num}，冒险模式共 {game_map.TOTAL_LEVEL} 关')
        return 2

    ctrl = build_ctrl(lv_num)
    lv = ctrl.state
    print(f'[1] 加载关卡 #{lv_num}：{lv.map_data[c.GAME_TITLE]}（共 {game_map.TOTAL_LEVEL} 关）')

    assert lv.state == c.CHOOSE, f'开局应停在选卡界面，实际 {lv.state}'

    # 选满 10 张卡后点开始
    for card in lv.panel.card_list[: c.CARD_LIST_NUM]:
        lv.panel.checkCardClick(card.rect.center)
    if lv.panel.selected_num != c.CARD_LIST_NUM:
        print(f'[!] 只选中 {lv.panel.selected_num}/{c.CARD_LIST_NUM} 张卡')
        return 3
    lv.initPlay(lv.panel.getSelectedCards())
    mb = lv.menubar
    print(f'[2] 开局 OK，初始阳光={mb.sun_value}，卡槽={len(mb.card_list)}')

    surface = ctrl.screen
    clock = pg.time.Clock()

    def now() -> int:
        return pg.time.get_ticks() * ctrl.game_info[c.GAME_RATE]

    def frame(mp=None, mc=(False, False), watch=None):
        lv.update(surface, now(), mp, list(mc))
        if watch:
            watch()
        clock.tick(60)

    def pump(seconds: float, watch=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            frame(watch=watch)

    def pump_until(pred, timeout: float) -> bool:
        """跑到 pred 成立为止。比写死 sleep 稳，不受帧率/环境开销影响。"""
        end = time.monotonic() + timeout
        while True:
            if pred():
                return True
            if time.monotonic() > end:
                return False
            frame()

    pump(0.6)                      # 让卡片冷却计时器有个正常初值
    mb.increaseSunValue(5000)      # 测试用：给足阳光

    # 天上掉落的阳光是从 y=0 往下飘的，会经过顶部卡片区。play() 里「捡阳光」先于
    # 「点卡片」判定，一旦命中就跳过卡片点击 —— 表现为随机某一行点不中卡片。
    # 这是游戏本身的正常机制，不是 bug；测试期间先把掉落关掉。
    saved_produce_sun = getattr(lv, 'produce_sun', False)
    lv.produce_sun = False
    lv.sun_group.empty()

    # ---- 五行各种一株豌豆射手，验证 卡片选择 -> 实地种植 ----
    pea_index = next(
        i
        for i, item in enumerate(c.PLANT_CARD_INFO)
        if item[c.PLANT_NAME_INDEX] == c.PEASHOOTER
    )
    card = next(cd for cd in mb.card_list if cd.index == pea_index)

    for row in range(lv.map_y_len):
        # 卡片有冷却，等它真的可点再点（冷却值是游戏毫秒，与环境快慢无关）
        if not pump_until(
            lambda cd=card: cd.canClick(mb.sun_value, mb.current_time), 10.0
        ):
            print(
                f'[!] 第 {row} 行：等卡片冷却超时 '
                f'(frozen_time={card.frozen_time} 游戏毫秒)——检查 setCardFrozenTime / canClick'
            )
            return 4

        pg.mouse.get_pos = lambda r=row: lv.map.getMapGridPos(0, r)
        frame(mp=card.rect.center, mc=(True, False))
        if not lv.drag_plant:
            print(f'[!] 第 {row} 行：卡片没被拿起（阳光={mb.sun_value}）')
            return 4
        frame()                                                   # 更新落点提示
        frame(mp=lv.map.getMapGridPos(0, row), mc=(True, False))   # 落位
        print(f'    第 {row} 行 -> 该行植物数 {len(lv.plant_groups[row])}')

    lv.produce_sun = saved_produce_sun      # 恢复天空掉落，后面按真实机制跑
    placed = sum(len(g) for g in lv.plant_groups)
    print(f'[3] 种植 OK：{placed}/{lv.map_y_len} 行落位，剩余阳光={mb.sun_value}')
    if placed != lv.map_y_len:
        print('[!] 没有铺满每一行，检查 canSeedPlant / addPlant 的 match 分支')
        return 4

    # ---- 跑一段时间，验证开火与刷怪 ----
    peak = [0]

    def watch():
        peak[0] = max(peak[0], sum(len(g) for g in lv.bullet_groups))

    pump(args.seconds, watch=watch)
    zombies = sum(len(g) for g in lv.zombie_groups)
    print(
        f'[4] 游戏内 {now() // 1000}s：在场僵尸 {zombies} 只 | '
        f'子弹峰值 {peak[0]} 发 | 掉落阳光 {len(lv.sun_group)} 个 | state={lv.state}'
    )
    if zombies == 0:
        print('[!] 没有刷出僵尸，检查 map_data 的 ZOMBIE_LIST / INCLUDED_ZOMBIES')
        return 5
    if peak[0] == 0:
        print('[!] 豌豆射手没打出子弹，检查 plant.py 与 collision 逻辑')
        return 6

    print('[PASS] 全链路正常：选卡 -> 开局 -> 种植 -> 开火 -> 刷僵尸')
    return 0


if __name__ == '__main__':
    sys.exit(main())
